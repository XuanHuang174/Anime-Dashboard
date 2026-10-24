import { similarQuery } from '../../src/utils/explorationResults.js'
import { DEFAULT_BROWSE_FILTERS } from '../../src/utils/recommendationBrowse.js'
export const scope = { content_scope: 'safe', scope_revision: 'b'.repeat(64) }
export const adultScope = { ...scope, content_scope: 'include_adult', content_scope_contract_version: 'recommendations.content-scope.n2-2.v1',
  content_scope_policy_version: 'content-scope.general-adult.v1', content_release_policy_version: 'release-review.general-adult.v2',
  eligibility_version: 0, content_review_version: 1, serving_control_version: 1, release_revision: 'a'.repeat(64), scope_valid_until: null }
export const query = similarQuery(1, DEFAULT_BROWSE_FILTERS)
export const versions = { query_fingerprint: 'a'.repeat(64), catalog_feature_version: 'c'.repeat(64), scope_revision: scope.scope_revision,
  taxonomy_version: 'genres.v8', ranking_policy_version: 'similarity.v1' }
export const seed = { bangumi_id: 1, title_zh: '合成参照', title_jp: '参照', year: 2026, season: '2026-01', type: 'TV', score: 8,
  tags: ['日常', '音乐'], content_rating: 'non_adult', cover_url: '', can_seed: true, unavailable_reason: null, feature_revision: 'd'.repeat(64) }
export const item = id => ({ bangumi_id: id, title_zh: `合成故事${id}`, title_jp: '', season: '2026-01', status_type: 'TV', score: 8,
  tags: ['日常', '音乐'], content_rating: 'non_adult', cover_url: '', watch_status: null, reason: '与参照作品共同包含日常、音乐题材。',
  evidence: { contract_version: 'recommendations.explore.evidence.v1', kind: 'selected_work', interpretation: 'shared_genre_association', is_probability: false,
    shared_tags: ['日常', '音乐'], source: { bangumi_id: 1, title: seed.title_zh, tags: seed.tags }, versions },
  evidence_context: versions, evidence_token: 'e'.repeat(64), detail_context: `detail-${id}` })
export function page({ batch = 1, part = 1, count = 30 } = {}) {
  const start = (batch - 1) * 24, offset = start + (part - 1) * 12, end = Math.min(start + 24, count), length = Math.min(12, end - offset)
  return { contract_version: 'recommendations.explore.v1', query_contract_version: 'recommendations.explore.query.v1', intent: 'similar',
    algorithm_version: 'similarity.v1', query: { ...query }, evidence_contract_version: 'recommendations.explore.evidence.v1',
    evidence_context: versions, selected_seed: seed, snapshot_id: '1'.repeat(32), snapshot_expires_at: Math.floor(Date.now() / 1000) + 900,
    snapshot_context: 'signed-context', eligible_count: count, returned_count: count, result_limit: 120, batch, batch_size: 24,
    batch_count: end - start, batch_hero_bangumi_id: count ? start + 10 : null, page: part, page_size: 12,
    next_page_cursor: offset + length < end ? 'page-two' : null, next_batch_cursor: end < count ? 'batch-two' : null,
    batch_exhausted: offset + length >= end, exhausted: offset + length >= count, empty_reason: count ? null : 'no_matches',
    results: Array.from({ length }, (_, i) => item(offset + i + 10)), content_scope_context: scope }
}
export function detail(id = 10) {
  return { ...item(id), synopsis: '合成公开简介', contract_version: 'recommendations.explore.detail.v1', intent: 'similar', query: { ...query },
    selected_seed: seed, snapshot_expires_at: Math.floor(Date.now() / 1000) + 900, content_scope_context: scope }
}
export const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
