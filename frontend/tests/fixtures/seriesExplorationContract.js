import { page, detail, seed, scope } from './explorationContract.js'
export const related = () => ({ policy_version: 'series-explore.direct.v1', series_revision: 1, fingerprint: 'f'.repeat(64),
  coverage: 'observed', last_attempt_status: 'observed', seed_bangumi_id: 1, direct_only: true, filtered_count: 2, limit: 12, truncated: false,
  items: [{ bangumi_id: 2, title_zh: '合成故事：第二季', title_jp: 'Synthetic Sequel', release_date: '2026-01', release_format: 'TV', watch_status: null,
    facts: [{ source_id: 1, target_id: 2, kind: 'sequel', relation_label: '续集', origin: 'archive', source_title: seed.title_zh, target_title: '合成故事：第二季' }] },
  { bangumi_id: 3, title_zh: '合成故事：回忆篇', title_jp: 'Synthetic Summary', release_date: '2026-07', release_format: '剧场版', watch_status: null,
    facts: [{ source_id: 1, target_id: 3, kind: 'summary', relation_label: '总集篇', origin: 'manual', source_title: seed.title_zh, target_title: '合成故事：回忆篇' }] }] })
export const seriesExplorePage = options => ({ ...page(options), contract_version: 'recommendations.explore.series.v1', series_context: related() })
export const seriesExploreDetail = (id = 10) => ({ ...detail(id), series_context: related() })
export const annotations = (ids = [10]) => ({ contract_version: 'recommendations.series.annotations.v1', policy_version: 'series-explore.direct.v1',
  series_revision: 1, fingerprint: 'f'.repeat(64), catalog_revision: 'b'.repeat(64), content_scope_context: scope,
  results: ids.map(id => ({ bangumi_id: id, truncated: false, facts: [{ source_id: 100, target_id: id, kind: 'sequel', relation_label: '续集',
    source_title: '合成故事：前一季', target_title: `合成故事${id}`, origin: 'archive' }] })) })
