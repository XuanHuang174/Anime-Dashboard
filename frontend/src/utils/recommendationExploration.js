// 11A selection contract. Seed choices never enter personal recommendation mappers.
import { checkScopeResponse } from './recommendationContentScope.js'
import { createLatestRequestGate, getRecommendationCover } from './recommendations.js'

export const EXPLORATION_VERSIONS = Object.freeze({
  capabilities: 'recommendations.explore.capabilities.v1', query: 'recommendations.explore.query.v1',
  catalog: 'recommendations.explore.catalog.v1', evidence: 'recommendations.explore.evidence.v1',
  result: 'recommendations.explore.v1',
})
export const EXPLORATION_NAVIGATION = Object.freeze([
  Object.freeze({ id: 'personal', label: '个性化推荐', path: '/recommendations' }),
  Object.freeze({ id: 'similar', label: '单番相似', path: '/recommendations/similar' }),
  Object.freeze({ id: 'tags', label: '按标签找', path: '/recommendations/tags' }),
])
const invalid = () => { throw new Error('探索合同不兼容，请重新加载。') }
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const identity = value => Number.isInteger(value) && value > 0 && value <= 2147483647

export function explorationNavigationLabel(id, current) {
  const item = EXPLORATION_NAVIGATION.find(row => row.id === id)
  if (!item) return invalid()
  return `${item.label}${current === id ? ' · 当前' : ''}`
}

export function validateExplorationCapabilities(data) {
  if (data?.contract_version !== EXPLORATION_VERSIONS.capabilities || typeof data.enabled !== 'boolean'
    || data.catalog_search_enabled !== data.enabled || data.query_execution_enabled !== false
    || !['query', 'catalog', 'evidence', 'result'].every(key => data[`${key}_contract_version`] === EXPLORATION_VERSIONS[key])
    || !Array.isArray(data.navigation) || data.navigation.length !== 3
    || !EXPLORATION_NAVIGATION.every((row, index) => Object.entries(row).every(([key, value]) => data.navigation[index]?.[key] === value)
      && data.navigation[index].available === (row.id === 'personal'))) return invalid()
  return data
}

export function validateExplorationContract(data) {
  if (data?.contract_version !== EXPLORATION_VERSIONS.query || data.taxonomy_version !== 'genres.v8'
    || !Array.isArray(data.available_tags) || data.available_tags.length !== 51
    || new Set(data.available_tags).size !== data.available_tags.length
    || !data.available_tags.every(tag => typeof tag === 'string' && tag.trim())
    || data.search?.contract_version !== EXPLORATION_VERSIONS.catalog || data.search.identity_namespace !== 'bangumi'
    || data.search.limit !== 15 || data.search.max_query_length !== 120
    || data.query?.intents?.similar !== 'similarity.v1' || data.query.intents.tags !== 'tag-explore.v1'
    || data.query.maximum_include_tags !== 5 || data.query.maximum_exclude_tags !== 5
    || data.query.requires_profile !== false || data.query.saves_preferences !== false
    || data.evidence?.contract_version !== EXPLORATION_VERSIONS.evidence
    || data.evidence.kinds?.similar !== 'selected_work' || data.evidence.kinds.tags !== 'selected_tags'
    || data.evidence.is_probability !== false || data.evidence.private_score_source !== false
    || data.limits?.catalog_rows !== 50000 || data.limits.results !== 120 || data.limits.batch_size !== 24
    || data.limits.page_size !== 12 || data.limits.snapshot_ttl_seconds !== 900) return invalid()
  validateExplorationCapabilities(data.capabilities)
  return data
}

export function normalizeExplorationSearch(raw) {
  if (typeof raw !== 'string' || [...raw].length > 120) throw new Error('搜索内容应为不超过 120 个字符的名称或 Bangumi ID。')
  let value = raw.normalize('NFKC').trim()
  if (!value || [...value].length > 120 || /\p{C}/u.test(value)) throw new Error('请输入有效的作品名称或 Bangumi ID。')
  const url = /^https?:\/\/(?:bgm\.tv|bangumi\.tv|chii\.in)\/subject\/([1-9][0-9]*)\/?$/.exec(value)
  if (url) value = url[1]
  else if (value.includes('://')) throw new Error('仅支持完整的 Bangumi subject 链接。')
  if (/^\p{Nd}+$/u.test(value)) {
    if (!/^[1-9][0-9]*$/.test(value) || !identity(Number(value))) throw new Error('Bangumi ID 无效。')
    return { kind: 'bangumi_id', value }
  }
  if ([...value].length < 2) throw new Error('作品名称至少输入两个字符。')
  return { kind: 'title', value }
}

export function validateExplorationCatalog(data, query, scope = 'safe') {
  if (!['safe', 'include_adult'].includes(scope)) return invalid()
  checkScopeResponse(data, scope)
  if (data?.contract_version !== EXPLORATION_VERSIONS.catalog || data.identity_namespace !== 'bangumi'
    || data.taxonomy_version !== 'genres.v8' || data.feature_rule_version !== 'journal-genres.v3'
    || data.query?.kind !== query.kind || data.query?.value !== query.value || data.limit !== 15
    || typeof data.has_more !== 'boolean' || !Array.isArray(data.results) || data.results.length > 15
    || data.has_more && data.results.length !== 15
    || data.content_scope_context?.content_scope !== scope || !hash(data.content_scope_context?.scope_revision)) return invalid()
  const seen = new Set()
  const items = data.results.map(row => {
    if (!identity(row.bangumi_id) || seen.has(row.bangumi_id)
      || typeof row.title_zh !== 'string' || typeof row.title_jp !== 'string'
      || row.title_zh.length > 500 || row.title_jp.length > 500
      || !Array.isArray(row.tags) || row.tags.length > 51 || new Set(row.tags).size !== row.tags.length
      || !row.tags.every(tag => typeof tag === 'string' && tag.trim() && tag.length <= 50)
      || row.can_seed !== (row.tags.length > 0) || row.unavailable_reason !== (row.can_seed ? null : 'no_valid_topics')
      || !hash(row.feature_revision) || !['adult', 'non_adult'].includes(row.content_rating)
      || typeof row.season !== 'string' || typeof row.type !== 'string'
      || !(row.year === null || Number.isInteger(row.year) && row.year >= 1000 && row.year <= 9999)
      || !Number.isFinite(row.score) || row.score < 0 || row.score > 10 || typeof row.cover_url !== 'string'
      || row.content_rating === 'adult' && row.cover_url !== ''
      || query.kind === 'bangumi_id' && row.bangumi_id !== Number(query.value)) return invalid()
    seen.add(row.bangumi_id)
    return { bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp || `未命名番剧 ${row.bangumi_id}`,
      originalTitle: row.title_jp, tags: [...row.tags], score: row.score, year: row.year,
      season: row.season, type: row.type, contentRating: row.content_rating,
      coverUrl: row.content_rating === 'adult' ? '' : getRecommendationCover(row.cover_url),
      canSeed: row.can_seed, unavailableReason: row.unavailable_reason, featureRevision: row.feature_revision }
  })
  return { items, hasMore: data.has_more, query: { ...query }, contentScope: data.content_scope_context }
}

export function createExplorationSearchController(client, scope = 'safe') {
  if (!['safe', 'include_adult'].includes(scope)) return invalid()
  const gate = createLatestRequestGate(), listeners = new Set()
  let state = { status: 'idle', items: [], hasMore: false }, active = true
  const emit = value => { state = value; for (const listener of listeners) listener() }
  const clear = () => { gate.cancel(); if (active) emit({ status: 'idle', items: [], hasMore: false }) }
  return {
    getSnapshot: () => state,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener) },
    clear,
    dispose: () => { active = false; gate.cancel(); listeners.clear() },
    async search(raw) {
      if (!active) return
      gate.cancel()
      if (typeof raw === 'string' && !raw.trim()) { clear(); return }
      let query
      try { query = normalizeExplorationSearch(raw) } catch (error) {
        emit({ status: 'error', items: [], hasMore: false, message: error.message }); return
      }
      const request = gate.begin()
      emit({ status: 'loading', items: [], hasMore: false, query })
      try {
        const data = await client.explorationCatalog(raw, request.signal)
        if (!active || !request.isCurrent()) return
        const result = validateExplorationCatalog(data, query, scope)
        emit({ status: result.items.length ? 'ready' : 'empty', ...result })
      } catch (error) {
        if (!active || !request.isCurrent() || error.name === 'AbortError') return
        const status = error.response?.status
        const disabled = status === 503 && error.response?.data?.code === 'exploration_disabled'
        const message = disabled ? '探索功能暂未开放。' : status === 401 ? '登录已失效，请重新登录。'
          : status === 403 || status === 409 ? '内容范围或目录已变化，请重新读取。'
            : status === 429 ? '搜索较频繁，请稍后重试。' : status === 503 ? '目录暂不可用，请稍后重试。'
              : error.message?.startsWith('探索合同') ? error.message : '搜索失败，请重试。'
        emit({ status: disabled ? 'unavailable' : 'error', items: [], hasMore: false, query, message })
      }
    },
  }
}
