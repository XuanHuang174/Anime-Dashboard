import { scope } from './explorationContract.js'
export function reminders({ hidden = new Set(), owned = new Set(), collapsed = false, version = 0, contentScope = scope } = {}) {
  const rows = [2, 4].filter(id => !owned.has(id)), visible = rows.filter(id => !hidden.has(id))
  return { contract_version: 'recommendations.series.reminders.v1', policy_version: 'series-reminders.completed-direct.v1',
    fingerprint: 'a'.repeat(64), dismissal_revision: (hidden.size ? 'b' : 'a').repeat(64), context: `synthetic-reminders-${version}`, version, collapsed, limit: 6, count: visible.length,
    hidden_count: rows.filter(id => hidden.has(id)).length, truncated: false, expires_at: Math.floor(Date.now() / 1000) + 900,
    content_scope_context: contentScope, record_note: '仅依据本人已看完的已确认记录；无已确认记录不代表没看过。发行日期不代表已播出或可观看。',
    results: visible.map(id => ({ bangumi_id: id, title_zh: id === 2 ? '合成故事：下一段旅程' : '合成故事：后续新篇', title_jp: '',
      release_date: '2026-10', release_format: 'TV', record_state: 'no_confirmed_record', more_sources: false,
      facts: [{ source_id: 1, target_id: id, kind: 'sequel', relation_label: '续集', origin: 'archive',
        source_title: '合成故事：起点', target_title: id === 2 ? '合成故事：下一段旅程' : '合成故事：后续新篇', completed_id: 1 }] })) }
}
