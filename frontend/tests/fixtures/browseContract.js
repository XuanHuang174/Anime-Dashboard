// Test-only 9A response builder. Never imported by application code.
export const browseRow = (id, context) => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: `作品${id}`, title_jp: '',
  score: 7.5, tags: ['音乐'], season: '2026-04', status_type: 'TV', watch_status: null,
  evidence: null, reason: '', evidence_context: context, evidence_token: 'b'.repeat(64) })
export function browsePage({ ids = Array.from({ length: 50 }, (_, n) => 101 + n), offset = 0, pageSize = 12,
  category = 'all', style = 'balanced', filters = {}, context = { algorithm_version: 'content.v1', profile_version: 'a'.repeat(64), preference_version: 0, feedback_version: 0 },
  snapshot = 'a'.repeat(32), expires = 2000000000, row = id => browseRow(id, context), makeCursor = value => `cursor-${value}` } = {}) {
  const start = Math.floor(offset / 24) * 24, end = Math.min(start + 24, ids.length), pageEnd = Math.min(offset + pageSize, end)
  return { contract_version: 'recommendations.3b.v1', browse_contract_version: 'recommendations.browse.v1',
    algorithm_version: 'content.v1', control_policy_version: 'browse-controls.v1', style,
    effective_conditions: { category, style, type: filters.type || 'all', min_score: Number(filters.minScore || 0).toFixed(1),
      year_from: filters.yearFrom ? Number(filters.yearFrom) : null, year_to: filters.yearTo ? Number(filters.yearTo) : null,
      hide_recorded: filters.hideRecorded ?? true, page_size: pageSize },
    snapshot_id: snapshot, snapshot_expires_at: expires, batch: Math.floor(offset / 24) + 1, batch_size: 24,
    batch_count: end - start, batch_hero_bangumi_id: ids[start] ?? null, page: Math.floor((offset - start) / pageSize) + 1,
    page_size: pageSize, count: ids.length, result_limit: 120,
    next_page_cursor: pageEnd < end ? makeCursor(pageEnd) : null, next_batch_cursor: end < ids.length ? makeCursor(end) : null,
    batch_exhausted: pageEnd >= end, exhausted: pageEnd >= ids.length,
    mode: 'personalized', readiness: 'ready', evidence_context: context,
    feature_version: 'genres-idf.v4', taxonomy_version: 'genres.v4', evidence_contract_version: 'recommendations.evidence.v1',
    results: ids.slice(offset, pageEnd).map(row) }
}
