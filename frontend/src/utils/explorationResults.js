// Exploration owns its query/evidence contract. Never use the profile mapper here.
import { checkScopeResponse } from './recommendationContentScope.js'
import { EXPLORATION_VERSIONS as V, validateExplorationCatalog } from './recommendationExploration.js'
import { getRecommendationCover } from './recommendations.js'
import { SERIES_EXPLORE_RESULT, validateRelated, sameSeriesContext } from './seriesExploration.js'

const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const int = (value, low, high) => Number.isInteger(value) && value >= low && value <= high
const text = (value, max = 500) => typeof value === 'string' && value.length <= max
const token = value => typeof value === 'string' && value.length > 0 && value.length <= 8192 && /^[A-Za-z0-9_.:-]+$/.test(value)
const topics = value => Array.isArray(value) && value.length <= 51 && new Set(value).size === value.length && value.every(tag => text(tag, 50) && tag.trim())
export const explorationInvalid = () => { throw new Error('探索合同不兼容，请重新探索。') }
export const sameExplorationContext = (a, b) => a && b && ['query_fingerprint', 'catalog_feature_version', 'scope_revision', 'taxonomy_version', 'ranking_policy_version'].every(key => a[key] === b[key])
export const sameExplorationQuery = (a, b) => a && b && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Array.isArray(a[key])
  ? Array.isArray(b[key]) && a[key].length === b[key].length && a[key].every((value, index) => value === b[key][index]) : a[key] === b[key])

export function similarQuery(seedId, filters) {
  return { intent: 'similar', seed_bangumi_id: seedId, type: filters.type,
    min_score: Number(filters.minScore === 'lt6' ? 0 : filters.minScore).toFixed(1),
    max_score: filters.minScore === 'lt6' ? '6.0' : '10.0', max_score_exclusive: filters.minScore === 'lt6',
    hide_recorded: filters.hideRecorded, page_size: 12,
    ...(filters.yearFrom ? { year_from: Number(filters.yearFrom) } : {}),
    ...(filters.yearTo ? { year_to: Number(filters.yearTo) } : {}) }
}

export function validateExplorationQuery(q) {
  const required = ['intent', 'type', 'min_score', 'max_score', 'max_score_exclusive', 'hide_recorded', 'page_size',
    ...(q?.intent === 'tags' ? ['include_tags', 'exclude_tags', 'match', 'sort'] : ['seed_bangumi_id'])]
  if (!q || !required.every(key => Object.hasOwn(q, key)) || Object.keys(q).some(key => ![...required, 'year_from', 'year_to'].includes(key))
    || !['similar', 'tags'].includes(q.intent) || q.intent === 'similar' && !int(q.seed_bangumi_id, 1, 2147483647) || q.page_size !== 12
    || q.intent === 'tags' && (!topics(q.include_tags) || !q.include_tags.length || q.include_tags.length > 5
      || !topics(q.exclude_tags) || q.exclude_tags.length > 5 || q.include_tags.some(tag => q.exclude_tags.includes(tag))
      || !['all', 'any'].includes(q.match) || !['relevance', 'score'].includes(q.sort))
    || !['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].includes(q.type)
    || ![q.min_score, q.max_score].every(value => typeof value === 'string' && /^(?:[0-9]\.\d|10\.0)$/.test(value))
    || typeof q.max_score_exclusive !== 'boolean' || typeof q.hide_recorded !== 'boolean'
    || Number(q.min_score) > Number(q.max_score) || q.max_score_exclusive && Number(q.min_score) >= Number(q.max_score)
    || ['year_from', 'year_to'].some(key => q[key] !== undefined && !int(q[key], 1000, 9999))
    || (q.year_from ?? 1000) > (q.year_to ?? 9999)) explorationInvalid()
}

function validateVersions(context, intent) {
  if (!context || Object.keys(context).length !== 5 || !['query_fingerprint', 'catalog_feature_version', 'scope_revision'].every(key => hash(context[key]))
    || context.taxonomy_version !== 'genres.v8' || context.ranking_policy_version !== (intent === 'tags' ? 'tag-explore.v1' : 'similarity.v1')) explorationInvalid()
}

export function mapExplorationSeed(row, scopeContext, scope = 'safe') {
  return validateExplorationCatalog({ contract_version: V.catalog, identity_namespace: 'bangumi', taxonomy_version: 'genres.v8',
    feature_rule_version: 'journal-genres.v3', query: { kind: 'bangumi_id', value: String(row?.bangumi_id) }, limit: 15,
    has_more: false, content_scope_context: scopeContext, results: [row] }, { kind: 'bangumi_id', value: String(row?.bangumi_id) }, scope).items[0]
}

function mapItem(row, query, seed, versions, scope, seriesContext) {
  const e = row?.evidence, source = e?.source
  validateVersions(row?.evidence_context, query.intent); validateVersions(e?.versions, query.intent)
  if (!int(row?.bangumi_id, 1, 2147483647) || row.bangumi_id === query.seed_bangumi_id
    || !text(row.title_zh) || !text(row.title_jp) || !text(row.season, 100) || !text(row.status_type, 100)
    || !topics(row.tags) || !Number.isFinite(row.score) || row.score < 0 || row.score > 10
    || !['adult', 'non_adult'].includes(row.content_rating) || row.content_rating === 'adult' && (scope !== 'include_adult' || row.cover_url !== '')
    || ![null, 'plan', 'watching', 'completed', 'on_hold'].includes(row.watch_status)
    || !text(row.reason, 4000) || !row.reason || !hash(row.evidence_token) || !token(row.detail_context)
    || !sameExplorationContext(row.evidence_context, versions)
    || e?.contract_version !== V.evidence || e.kind !== (query.intent === 'tags' ? 'selected_tags' : 'selected_work') || e.is_probability !== false
    || Object.keys(e).length !== 7
    || e.interpretation !== 'shared_genre_association' || !sameExplorationContext(e.versions, versions)
    || !topics(e.shared_tags) || !e.shared_tags.length) explorationInvalid()
  if (query.intent === 'tags') {
    if (source !== null || query.exclude_tags.some(tag => row.tags.includes(tag))
      || query.match === 'all' && !query.include_tags.every(tag => row.tags.includes(tag))
      || query.include_tags.filter(tag => row.tags.includes(tag)).join('\0') !== e.shared_tags.join('\0')) explorationInvalid()
  } else if (!source || Object.keys(source).length !== 3 || !topics(source.tags)
    || source.bangumi_id !== seed.bangumiId || source.title !== seed.title
    || source.tags.length !== seed.tags.length || source.tags.some((tag, index) => tag !== seed.tags[index])
    || e.shared_tags.some(tag => !row.tags.includes(tag) || !source.tags.includes(tag))
    || source.tags.filter(tag => row.tags.includes(tag)).join('\0') !== e.shared_tags.join('\0')) explorationInvalid()
  if (query.type !== 'all' && row.status_type !== query.type || query.hide_recorded && row.watch_status !== null
    || row.score < Number(query.min_score) || row.score > Number(query.max_score)
    || query.max_score_exclusive && !(row.score > 0 && row.score < Number(query.max_score))) explorationInvalid()
  const year = /^([1-9]\d{3})/.exec(row.season)?.[1]
  if ((query.year_from || query.year_to) && (!year || Number(year) < (query.year_from ?? 1000) || Number(year) > (query.year_to ?? 9999))) explorationInvalid()
  return { id: String(row.bangumi_id), bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp,
    originalTitle: row.title_jp, season: row.season, year, type: row.status_type, score: row.score,
    tags: [...row.tags], synopsis: text(row.synopsis, 100000) ? row.synopsis : '', contentRating: row.content_rating,
    coverUrl: row.content_rating === 'adult' ? '' : getRecommendationCover(row.cover_url), watchStatus: row.watch_status,
    reason: row.reason, evidence: e, evidenceContext: row.evidence_context, evidenceToken: row.evidence_token,
    detailContext: row.detail_context, explorationQuery: query, explorationSeed: seed, explorationScope: scope, seriesContext }
}

export function mapExplorationPage(data, expectedQuery, scope = 'safe', expectedSeries) {
  checkScopeResponse(data, scope)
  validateExplorationQuery(data?.query); validateVersions(data?.evidence_context, data.query.intent)
  const series = data.contract_version === SERIES_EXPLORE_RESULT
  if (series) validateRelated(data.series_context, data.query)
  if (expectedSeries !== undefined && series !== expectedSeries || !series && data.series_context !== undefined) explorationInvalid()
  if (!series && data.contract_version !== V.result || data.query_contract_version !== V.query || data.evidence_contract_version !== V.evidence
    || data.intent !== data.query.intent || data.algorithm_version !== (data.intent === 'tags' ? 'tag-explore.v1' : 'similarity.v1') || expectedQuery && !sameExplorationQuery(data.query, expectedQuery)
    || data.evidence_context.scope_revision !== data.content_scope_context?.scope_revision
    || !/^[a-f0-9]{32}$/.test(data.snapshot_id) || !int(data.snapshot_expires_at, 1, Number.MAX_SAFE_INTEGER)
    || data.snapshot_expires_at * 1000 <= Date.now() || !token(data.snapshot_context)
    || !int(data.eligible_count, 0, 50000) || data.returned_count !== Math.min(data.eligible_count, 120) || data.result_limit !== 120
    || data.batch_size !== 24 || data.page_size !== 12 || !int(data.batch, 1, Math.max(1, Math.ceil(data.returned_count / 24)))
    || !int(data.page, 1, 2) || !Array.isArray(data.results)
    || ![data.next_page_cursor, data.next_batch_cursor].every(value => value === null || token(value))) explorationInvalid()
  const start = (data.batch - 1) * 24, offset = start + (data.page - 1) * 12, end = Math.min(start + 24, data.returned_count)
  if (data.batch_count !== end - start || data.results.length !== Math.min(12, end - offset) || offset >= end && data.returned_count !== 0
    || (data.next_page_cursor !== null) !== (offset + data.results.length < end)
    || (data.next_batch_cursor !== null) !== (end < data.returned_count)
    || data.batch_exhausted !== (offset + data.results.length >= end) || data.exhausted !== (offset + data.results.length >= data.returned_count)
    || data.returned_count === 0 && (data.batch !== 1 || data.page !== 1 || data.batch_hero_bangumi_id !== null)
    || data.returned_count > 0 && (!int(data.batch_hero_bangumi_id, 1, 2147483647) || data.page === 1 && data.batch_hero_bangumi_id !== data.results[0]?.bangumi_id)) explorationInvalid()
  const seed = data.intent === 'tags' ? null : mapExplorationSeed(data.selected_seed, data.content_scope_context, scope)
  if ((data.intent === 'tags' ? data.selected_seed !== null : seed.bangumiId !== data.query.seed_bangumi_id)
    || data.empty_reason !== (seed && !seed.canSeed ? 'seed_without_topics' : !data.returned_count ? 'no_matches' : null)) explorationInvalid()
  const items = data.results.map(row => mapItem(row, data.query, seed, data.evidence_context, scope, data.series_context))
  if (series && items.some(item => data.series_context.items.some(row => row.bangumi_id === item.bangumiId))) explorationInvalid()
  if (new Set(items.map(item => item.bangumiId)).size !== items.length) explorationInvalid()
  return { ...data, selectedSeed: seed, items, count: data.returned_count }
}

export function requireExplorationTransition(current, next, action, seen) {
  // Django may re-sign the same context in another second. Its signature bytes
  // are opaque; compare server-owned snapshot/query/version invariants instead.
  if (['snapshot_id', 'snapshot_expires_at', 'returned_count', 'eligible_count', 'algorithm_version'].some(key => current[key] !== next[key])
    || current.contract_version !== next.contract_version || !sameSeriesContext(current.series_context, next.series_context)
    || !sameExplorationQuery(current.query, next.query) || !sameExplorationContext(current.evidence_context, next.evidence_context)
    || current.selectedSeed?.featureRevision !== next.selectedSeed?.featureRevision
    || (action === 'more' ? next.batch !== current.batch || next.page !== current.page + 1 || next.batch_hero_bangumi_id !== current.batch_hero_bangumi_id
      : next.batch !== current.batch + 1 || next.page !== 1)
    || next.items.some(item => seen.has(item.bangumiId))) explorationInvalid()
}

// The shared detail state machine still discards the WHOLE old item after a POST.
export const explorationDetailAdapter = {
  select: item => ({ explorationQuery: item.explorationQuery, explorationScope: item.explorationScope, seriesContext: item.seriesContext }),
  load: (client, selection, expectation, signal) => client.explorationDetail(selection.bangumiId, selection.detailContext, signal, expectation?.token, !!selection.seriesContext),
  map(raw, selection, expectation) {
    const scope = selection.explorationScope
    checkScopeResponse(raw, scope); validateExplorationQuery(raw?.query); validateVersions(raw?.evidence_context, raw.query.intent)
    if (raw.series_context) validateRelated(raw.series_context, raw.query)
    if (!sameSeriesContext(raw.series_context, selection.seriesContext)) explorationInvalid()
    if (raw.contract_version !== 'recommendations.explore.detail.v1' || raw.intent !== raw.query.intent
      || !sameExplorationQuery(raw.query, selection.explorationQuery) || !int(raw.snapshot_expires_at, 1, Number.MAX_SAFE_INTEGER)
      || raw.snapshot_expires_at * 1000 <= Date.now()
      || raw.evidence_context.scope_revision !== raw.content_scope_context?.scope_revision
      || expectation && (!sameExplorationContext(raw.evidence_context, expectation.context) || raw.evidence_token !== expectation.token)) explorationInvalid()
    const seed = raw.intent === 'tags' ? null : mapExplorationSeed(raw.selected_seed, raw.content_scope_context, scope)
    if (raw.intent === 'tags' ? raw.selected_seed !== null : seed.bangumiId !== raw.query.seed_bangumi_id) explorationInvalid()
    // A confirmed own write may now make this candidate owned; all other query constraints remain.
    const item = mapItem(raw, { ...raw.query, hide_recorded: false }, seed, raw.evidence_context, scope, raw.series_context)
    return { ...item, explorationQuery: raw.query }
  },
}
