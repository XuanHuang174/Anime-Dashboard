import { SERIES_KINDS } from '../../src/utils/recommendationSeries.js'
export const scope = { content_scope: 'safe', scope_revision: 'b'.repeat(64), scope_valid_until: null }
export function seriesPage({ source = 1, coverage = 'observed', rows = true } = {}) {
  const names = { 1: '合成故事：起点', 2: '合成故事：下一段旅程', 3: '合成故事：回忆篇', 4: '合成故事：日常番外', 5: '合成世界的另一段故事' }
  return { contract_version: 'recommendations.series.workspace.v1', series_revision: 1, catalog_revision: 'a'.repeat(64),
    selected_seed: { bangumi_id: source, title_zh: names[source] || `合成故事 ${source}`, title_jp: 'Synthetic Story', watch_status: 'completed', record_state: 'linked' },
    coverage, last_attempt_status: coverage, snapshot_version: 'dump-2026-10-20.000000Z', context: 'synthetic-series-context', ordering: 'release_date_not_watch_order', depth: 1,
    limit: 200, truncated: false, scope_note: '仅展示当前内容范围内、已收录且身份与分级一致的动画；关系资料可能不完整。',
    record_note: '仅对应本人已确认身份的记录；无已确认记录不代表没看过。旧资料可能需要先核对身份。', content_scope_context: scope,
    results: rows ? ['sequel', 'summary', 'side_story', 'same_setting'].map((kind, index) => ({ bangumi_id: index + 2, title_zh: names[index + 2], title_jp: `Synthetic ${index + 2}`,
      release_date: index === 3 ? '' : `202${index + 4}-01`, release_precision: index === 3 ? 'unknown' : 'month', release_format: index === 1 ? '剧场版' : index === 2 ? 'OVA' : 'TV',
      kind, relation_label: SERIES_KINDS[kind][0], section: SERIES_KINDS[kind][1], origin: index === 2 ? 'manual' : 'archive',
      watch_status: index === 1 ? 'on_hold' : null, record_state: index === 1 ? 'linked' : 'no_confirmed_record', can_add_from_series: index !== 1 })) : [] }
}
export function seriesDetail(page, target = 2) {
  const row = page.results.find(item => item.bangumi_id === target) || page.selected_seed
  return { ...page, contract_version: 'recommendations.series.detail.v1', item: { bangumi_id: target, title_zh: row.title_zh, title_jp: row.title_jp,
    watch_status: row.watch_status, record_state: row.record_state, season: '2026-01', status_type: 'TV', cover_url: '', tags: ['日常', '音乐'], score: '8.0',
    synopsis: '这是仅用于本地组件验收的合成故事。作品详情与资源检索复用既有功能，系列关系不会被当作观看指南。', content_rating: 'non_adult' } }
}
