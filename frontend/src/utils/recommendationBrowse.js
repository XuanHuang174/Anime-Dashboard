import { mapRecommendationItem, validateColdStartState } from './recommendations.js'
import { sameEvidenceContext } from './recommendationEpoch.js'
import { validExecutionRecipe, sameExecutionRecipe, matchesExecutionContext, validDetailContext } from './recommendationExecution.js'

export const BROWSE_UPDATED = '浏览依据已更新，请重新加载推荐。'
export const REC_STYLES = Object.freeze([
  { id: 'close', label: '贴近口味', description: '更贴近已有喜好，允许题材适当集中。' },
  { id: 'balanced', label: '均衡', description: '保持当前 V1 排序，兼顾熟悉与发现。' },
  { id: 'open', label: '开放一些', description: '保留真实关联，多看看不同题材组合。' },
])
export const DEFAULT_BROWSE_FILTERS = Object.freeze({ type: 'all', minScore: '0', hideRecorded: true, yearFrom: '', yearTo: '' })
const keys = ['category', 'style', 'type', 'min_score', 'year_from', 'year_to', 'hide_recorded', 'page_size']
const int = (value, low, high) => Number.isInteger(value) && value >= low && value <= high
const cursor = value => value === null || typeof value === 'string' && value.length > 0 && value.length <= 8192 && /^[A-Za-z0-9_.:-]+$/.test(value)
const invalid = () => { throw new Error('推荐浏览合同不兼容，请重新加载。') }

export function browseConditions({ category, style, filters }) {
  return { category, style, type: filters.type, min_score: filters.minScore === 'lt6' ? '0.0' : Number(filters.minScore).toFixed(1),
    ...(filters.minScore === 'lt6' ? { max_score: '6.0', max_score_exclusive: true } : {}),
    year_from: filters.yearFrom ? Number(filters.yearFrom) : null,
    year_to: filters.yearTo ? Number(filters.yearTo) : null, hide_recorded: filters.hideRecorded, page_size: 12 }
}
export function sameConditions(a, b) { return a && b && keys.every(key => a[key] === b[key]) && (a.max_score ?? '10.0') === (b.max_score ?? '10.0') && (a.max_score_exclusive ?? false) === (b.max_score_exclusive ?? false) }

export function mapBrowsePage(data) {
  const c = data?.effective_conditions
  const prepared = data?.browse_contract_version === 'recommendations.browse.v2'
  if (prepared ? !validExecutionRecipe(data.execution_recipe) || data.algorithm_version !== 'content.v2'
    || data.control_policy_version !== data.execution_recipe.control_policy_version
    || !matchesExecutionContext(data.evidence_context, data.execution_recipe)
    || data.results?.some(row => !validDetailContext(row.detail_context))
    : data?.execution_recipe !== undefined || data?.evidence_context?.recipe_revision !== undefined || data?.algorithm_version !== 'content.v1'
      || data?.control_policy_version !== 'browse-controls.v1') invalid()
  if (data?.contract_version !== 'recommendations.3b.v1' || (data.browse_contract_version !== 'recommendations.browse.v1'
    && !prepared)
    || !REC_STYLES.some(style => style.id === data.style) || !c || Object.keys(c).length !== keys.length + (c.max_score === undefined ? 0 : 1) + (c.max_score_exclusive === undefined ? 0 : 1)
    || c.max_score_exclusive !== undefined && (c.max_score_exclusive !== true || c.max_score === undefined || Number(c.min_score) >= Number(c.max_score))
    || c.max_score !== undefined && (typeof c.max_score !== 'string' || !/^(?:[0-9]\.\d|10\.0)$/.test(c.max_score) || Number(c.max_score) >= 10 || Number(c.max_score) < Number(c.min_score))
    || !['all', 'familiar', 'explore'].includes(c.category) || c.style !== data.style
    || !['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].includes(c.type)
    || typeof c.min_score !== 'string' || !/^(?:[0-9]\.\d|10\.0)$/.test(c.min_score)
    || typeof c.hide_recorded !== 'boolean' || !int(c.page_size, 1, 24)
    || ![c.year_from, c.year_to].every(year => year === null || int(year, 1000, 9999))
    || c.year_from !== null && c.year_to !== null && c.year_from > c.year_to
    || !/^[a-f0-9]{32}$/.test(data.snapshot_id) || !int(data.snapshot_expires_at, 1, Number.MAX_SAFE_INTEGER)
    || !int(data.count, 0, 120) || data.result_limit !== 120 || data.batch_size !== 24
    || !int(data.batch, 1, Math.max(1, Math.ceil(data.count / 24))) || !int(data.page, 1, 24)
    || data.page_size !== c.page_size || !Array.isArray(data.results)
    || !cursor(data.next_page_cursor) || !cursor(data.next_batch_cursor)
    || !['personalized', 'preference_based', 'cold_start'].includes(data.mode)
    || data.state_token !== undefined || data.next_page !== undefined) invalid()
  const start = (data.batch - 1) * 24, offset = start + (data.page - 1) * data.page_size
  const end = Math.min(start + 24, data.count)
  if (data.batch_count !== end - start || offset >= end && data.count !== 0
    || data.results.length !== Math.min(data.page_size, end - offset)
    || (data.next_page_cursor !== null) !== (offset + data.results.length < end)
    || (data.next_batch_cursor !== null) !== (end < data.count)
    || data.batch_exhausted !== (offset + data.results.length >= end)
    || data.exhausted !== (offset + data.results.length >= data.count)
    || !data.count && (data.page !== 1 || data.batch !== 1 || data.batch_hero_bangumi_id !== null)
    || data.count && (!int(data.batch_hero_bangumi_id, 1, 2147483647)
      || data.page === 1 && data.batch_hero_bangumi_id !== data.results[0]?.bangumi_id)
    || data.mode === 'cold_start' && data.count !== 0) invalid()
  validateColdStartState(data.cold_start_state)
  if (!data.evidence_context || data.evidence_context.algorithm_version !== data.algorithm_version
    || data.results.some(row => !sameEvidenceContext(row.evidence_context, data.evidence_context))
    || data.cold_start_state && data.mode !== ({ personalized: 'personalized', explicit_preferences: 'preference_based', none: 'cold_start' }[data.cold_start_state.recommendation_basis])) invalid()
  const items = data.results.map(mapRecommendationItem)
  if (c.max_score !== undefined && data.results.some(row => !(row.score > 0 && (c.max_score_exclusive ? row.score < Number(c.max_score) : row.score <= Number(c.max_score))))) invalid()
  if (new Set(items.map(item => item.bangumiId)).size !== items.length) invalid()
  return { ...data, items }
}

export function requireBrowseTransition(current, next, action, seen) {
  if (['snapshot_id', 'snapshot_expires_at', 'algorithm_version', 'control_policy_version', 'browse_contract_version',
    'feature_version', 'taxonomy_version', 'evidence_contract_version', 'mode', 'count', 'page_size']
    .some(key => current[key] !== next[key]) || !sameConditions(current.effective_conditions, next.effective_conditions)
    || !sameEvidenceContext(current.evidence_context, next.evidence_context)
    || !sameExecutionRecipe(current.execution_recipe, next.execution_recipe)
    || (action === 'more' ? next.batch !== current.batch || next.page !== current.page + 1
      || next.batch_hero_bangumi_id !== current.batch_hero_bangumi_id : next.batch !== current.batch + 1 || next.page !== 1)
    || next.items.some(item => seen.has(item.bangumiId))) {
    const error = new Error(BROWSE_UPDATED); error.snapshotChanged = true; throw error
  }
}

export function filterDraftError(filters) {
  for (const value of [filters.yearFrom, filters.yearTo]) {
    if (value !== '' && !/^[1-9][0-9]{3}$/.test(value)) return '请输入四位年份，或留空表示不限。'
  }
  return filters.yearFrom && filters.yearTo && Number(filters.yearFrom) > Number(filters.yearTo) ? '年份起点不能大于终点。' : ''
}

export function filterChips(filters) {
  return [filters.type !== 'all' && { id: 'type', label: filters.type },
    (filters.yearFrom || filters.yearTo) && { id: 'years', label: `${filters.yearFrom || '不限'}—${filters.yearTo || '不限'}` },
    filters.minScore === 'lt6' ? { id: 'minScore', label: 'Bangumi 评分 < 6.0' }
      : Number(filters.minScore) > 0 && { id: 'minScore', label: `Bangumi 评分 ≥ ${Number(filters.minScore).toFixed(1)}` },
    filters.hideRecorded && { id: 'hideRecorded', label: '隐藏手账已有记录' }].filter(Boolean)
}
