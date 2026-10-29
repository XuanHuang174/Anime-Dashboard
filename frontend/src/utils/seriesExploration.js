import { SERIES_KINDS } from './recommendationSeries.js'
import { checkScopeResponse } from './recommendationContentScope.js'
export const SERIES_EXPLORE_RESULT = 'recommendations.explore.series.v1'
export const SERIES_EXPLORE_POLICY = 'series-explore.direct.v2'
const id = value => Number.isInteger(value) && value > 0 && value <= 2147483647
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const text = value => typeof value === 'string' && value.length <= 500
const invalid = () => { throw new Error('探索合同不兼容，请重新探索。') }
const coverage = ['observed', 'empty_observed', 'not_loaded', 'partial', 'failed', 'stale']
function factValid(fact) {
  return fact && id(fact.source_id) && id(fact.target_id) && fact.source_id !== fact.target_id
    && typeof fact.kind === 'string' && Object.hasOwn(SERIES_KINDS, fact.kind) && SERIES_KINDS[fact.kind][0] === fact.relation_label
    && ['manual', 'archive'].includes(fact.origin) && text(fact.source_title) && text(fact.target_title)
}
export function validateRelated(data, query) {
  if (!data || data.policy_version !== SERIES_EXPLORE_POLICY || !Number.isSafeInteger(data.series_revision) || data.series_revision < 0
    || !hash(data.fingerprint) || data.seed_bangumi_id !== query.seed_bangumi_id || query.intent !== 'similar'
    || !coverage.includes(data.coverage) || !coverage.includes(data.last_attempt_status) || data.direct_only !== true || data.limit !== 12
    || !Number.isInteger(data.filtered_count) || data.filtered_count < 0 || data.filtered_count > 5000
    || data.truncated !== (data.filtered_count > 12) || !Array.isArray(data.items) || data.items.length !== Math.min(12, data.filtered_count)) invalid()
  const seen = new Set()
  for (const row of data.items) {
    if (!id(row.bangumi_id) || row.bangumi_id === query.seed_bangumi_id || seen.has(row.bangumi_id)
      || !text(row.title_zh) || !text(row.title_jp) || !text(row.release_date) || !text(row.release_format)
      || ![null, 'plan', 'watching', 'completed', 'on_hold'].includes(row.watch_status)
      || !Array.isArray(row.facts) || !row.facts.length || row.facts.length > 28 || !row.facts.every(fact => factValid(fact)
        && SERIES_KINDS[fact.kind][1] !== 'related' && [fact.source_id, fact.target_id].includes(row.bangumi_id)
        && [fact.source_id, fact.target_id].includes(query.seed_bangumi_id))) invalid()
    seen.add(row.bangumi_id)
  }
  return data
}
export function sameSeriesContext(a, b) {
  return !a && !b || !!a && !!b && ['policy_version', 'series_revision', 'fingerprint', 'seed_bangumi_id'].every(key => a[key] === b[key])
}
export function validateAnnotations(data, ids, scope, expectedRevision) {
  checkScopeResponse(data, scope)
  if (data?.contract_version !== 'recommendations.series.annotations.v1' || data.policy_version !== SERIES_EXPLORE_POLICY
    || !Number.isSafeInteger(data.series_revision) || data.series_revision < 0 || !hash(data.fingerprint) || !hash(data.catalog_revision)
    || data.content_scope_context?.content_scope !== scope || !hash(data.content_scope_context?.scope_revision)
    || !Array.isArray(data.results) || data.results.length > ids.length) invalid()
  if (expectedRevision !== undefined && data.series_revision !== expectedRevision) {
    const error = new Error('关系版本已变化，请重新探索。'); error.seriesChanged = true; throw error
  }
  const map = new Map()
  for (const row of data.results) {
    if (!ids.includes(row.bangumi_id) || map.has(row.bangumi_id) || typeof row.truncated !== 'boolean'
      || !Array.isArray(row.facts) || row.facts.length > 2 || !row.facts.every(fact => factValid(fact) && [fact.source_id, fact.target_id].includes(row.bangumi_id))) invalid()
    map.set(row.bangumi_id, row)
  }
  return map
}
export function relationText(fact, identity) {
  return fact.target_id === identity ? `本作是《${fact.source_title}》的${fact.relation_label}` : `《${fact.target_title}》是本作的${fact.relation_label}`
}
