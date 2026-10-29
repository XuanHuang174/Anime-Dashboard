import { scoreDraft } from './browseScore.js'
import { DEFAULT_BROWSE_FILTERS, filterDraftError } from './recommendationBrowse.js'
import { similarQuery, validateExplorationQuery } from './explorationResults.js'

export const emptyTagDraft = () => ({ include: [], exclude: [], match: 'all', sort: 'relevance', filters: { ...DEFAULT_BROWSE_FILTERS } })
export function tagDraftError(draft, tags) {
  if (!draft.include.length) return '至少选择 1 个包含题材。'
  if (draft.include.length > 5 || draft.exclude.length > 5) return '包含与排除各最多选择 5 个题材。'
  if ([...draft.include, ...draft.exclude].some(tag => !tags.includes(tag))) return '请选择目录中的标准题材。'
  if (new Set([...draft.include, ...draft.exclude]).size !== draft.include.length + draft.exclude.length) return '题材不能重复或同时包含与排除。'
  if (!['all', 'any'].includes(draft.match) || !['relevance', 'score'].includes(draft.sort)) return '匹配规则或排序无效。'
  if (!['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].includes(draft.filters.type)
    || !['0', '6', '7', '8', '9', 'lt6', 'custom'].includes(draft.filters.minScore) || typeof draft.filters.hideRecorded !== 'boolean') return '普通筛选条件无效。'
  return filterDraftError(draft.filters)
}
export function tagQuery(draft, tags) {
  const error = tagDraftError(draft, tags)
  if (error) throw new Error(error)
  const { seed_bangumi_id, ...common } = similarQuery(1, draft.filters)
  return { ...common, intent: 'tags', include_tags: tags.filter(tag => draft.include.includes(tag)),
    exclude_tags: tags.filter(tag => draft.exclude.includes(tag)), match: draft.match, sort: draft.sort }
}
export function tagDraftFromQuery(query) {
  validateExplorationQuery(query)
  if (query.intent !== 'tags') throw new Error('标签链接无效。')
  return { include: [...query.include_tags], exclude: [...query.exclude_tags], match: query.match, sort: query.sort,
    filters: { type: query.type, ...scoreDraft(query), hideRecorded: query.hide_recorded,
      yearFrom: query.year_from ? String(query.year_from) : '', yearTo: query.year_to ? String(query.year_to) : '' } }
}
// Only public conditions are shareable. Never serialize scope, tokens or private data.
export function tagQueryPath(query) {
  const draft = tagDraftFromQuery(query), params = new URLSearchParams()
  params.set('include', draft.include.join(','))
  if (draft.exclude.length) params.set('exclude', draft.exclude.join(','))
  if (draft.match !== 'all') params.set('match', draft.match)
  if (draft.sort !== 'relevance') params.set('sort', draft.sort)
  for (const key of ['type', 'minScore', 'yearFrom', 'yearTo', 'scoreFrom', 'scoreTo']) if (draft.filters[key] !== undefined && draft.filters[key] !== DEFAULT_BROWSE_FILTERS[key]) params.set(key, draft.filters[key])
  if (!draft.filters.hideRecorded) params.set('hideRecorded', 'false')
  return `/recommendations/tags?${params}`
}
export function parseTagQuery(search, tags) {
  if (!search || search === '?') return { draft: emptyTagDraft(), query: null }
  try {
    const params = new URLSearchParams(search), allowed = ['include', 'exclude', 'match', 'sort', 'type', 'minScore', 'yearFrom', 'yearTo', 'scoreFrom', 'scoreTo', 'hideRecorded']
    if ([...params.keys()].some(key => !allowed.includes(key) || params.getAll(key).length !== 1)
      || params.has('hideRecorded') && params.get('hideRecorded') !== 'false') throw new Error()
    const draft = emptyTagDraft()
    draft.include = (params.get('include') || '').split(',')
    draft.exclude = params.has('exclude') ? params.get('exclude').split(',') : []
    draft.match = params.get('match') || 'all'; draft.sort = params.get('sort') || 'relevance'
    for (const key of ['type', 'minScore', 'yearFrom', 'yearTo', 'scoreFrom', 'scoreTo']) if (params.has(key)) draft.filters[key] = params.get(key)
    if (draft.filters.minScore !== 'custom' && (params.has('scoreFrom') || params.has('scoreTo'))) throw new Error()
    draft.filters.hideRecorded = !params.has('hideRecorded')
    return { draft, query: tagQuery(draft, tags) }
  } catch { return { draft: emptyTagDraft(), query: null, error: '链接中的标签条件无效，请重新选择标准题材。' } }
}
