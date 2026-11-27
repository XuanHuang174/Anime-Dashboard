import { reviewPeriod } from '../../src/utils/watchReview.js'

export const zero = () => ({ started_works: 0, started_rounds: 0, completed_works: 0, completed_rounds: 0, first_completed_rounds: 0,
  rewatch_started_rounds: 0, rewatch_completed_rounds: 0, notes: 0, memories: 0, viewed_works: 0, related_works: 0, activity_facts: 0,
  completed_round_rating: { count: 0, missing_count: 0, average: null, basis: 'effective_completed_round' } })
const work = id => ({ record_id: id, title: `年度作品 ${id}`, original_title: '', airing_period: '', poster_url: '', current_score: '9.0', current_watch_status: 'completed', metadata_basis: 'current_journal' })
export function annual(query = {}, token = 'annual-snapshot') {
  const y = query.year || '2026', months = Array.from({ length: 12 }, (_, i) => ({ month: `${y}-${String(i + 1).padStart(2, '0')}`, facts: 0, summary: zero() }))
  months[0] = { ...months[0], facts: 2, summary: { ...zero(), started_works: 1, started_rounds: 1, completed_works: 1, completed_rounds: 1,
    first_completed_rounds: 1, viewed_works: 1, related_works: 1, activity_facts: 2,
    completed_round_rating: { count: 1, missing_count: 0, average: '9.00', basis: 'effective_completed_round' } } }
  months[9] = { ...months[9], facts: 1, summary: { ...zero(), completed_works: 1, completed_rounds: 1, rewatch_completed_rounds: 1,
    viewed_works: 1, related_works: 1, activity_facts: 1, completed_round_rating: { count: 1, missing_count: 0, average: '3.00', basis: 'effective_completed_round' } } }
  const summary = { ...zero(), started_works: 1, started_rounds: 1, completed_works: 1, completed_rounds: 2, first_completed_rounds: 1,
    rewatch_completed_rounds: 1, viewed_works: 1, related_works: 2, activity_facts: 3, notes: 1, memories: 1,
    completed_round_rating: { count: 2, missing_count: 0, average: '6.00', basis: 'effective_completed_round' } }
  const section = query.section || 'works', page = query.page || 1, pageSize = query.page_size || 12
  const unknown = section === 'month_unknown'
  const rows = unknown ? [{ entry_type: 'fact', id: '2:event:1', record_id: 2, work: work(2), kind: 'memory', round_number: 1,
    actual_date: y, date_precision: 'year', date_bounds: { start: `${y}-01-01`, end: `${y}-12-31` }, attribution: 'definite',
    is_rewatch: false, note: '只记得这一年', date_source: 'explicit_event' }]
    : [{ entry_type: 'work', work: work(1), matched_fact_count: 3, matched_kinds: { started: 1, completed: 2 }, latest_actual_date: `${y}-10` },
      { entry_type: 'work', work: work(2), matched_fact_count: 1, matched_kinds: { memory: 1 }, latest_actual_date: y }]
  return { contract_version: 'journal.watch-review.v1', policy_version: 'actual-date-contained.v1', period: reviewPeriod({ year: y }),
    snapshot: token, generated_at: '2026-11-28T00:00:00Z', counts_scope: 'owner_all_records_selected_period', live: true, metadata_basis: 'current_journal', summary,
    coverage: { uncertain: { facts: 0, works: 0 }, undated: { facts: 1, works: 1 }, undated_scope: 'owner_all_history', uncertain_scope: 'overlaps_selected_period', includes_undated_in_summary: false, includes_uncertain_in_summary: false },
    annual: { year: y, basis: 'effective_facts_by_actual_month', months, month_unknown: { facts: 1, works: 1, summary: { ...zero(), notes: 1, memories: 1, related_works: 1 } } },
    section, page, page_size: pageSize, count: rows.length, count_unit: unknown ? 'facts' : 'works', next_page: page * pageSize < rows.length ? page + 1 : null,
    results: rows.slice((page - 1) * pageSize, page * pageSize) }
}
export function monthly(query = {}) {
  const data = annual(), month = query.month || '2026-01', row = data.annual.months[Number(month.slice(5)) - 1]
  delete data.annual
  const count = row.summary.related_works
  return { ...data, period: reviewPeriod({ month }), snapshot: 'monthly-snapshot', summary: row.summary, section: 'works', page: 1, page_size: query.page_size || 12, count, next_page: null,
    results: count ? [{ entry_type: 'work', work: work(1), matched_fact_count: row.facts, matched_kinds: { started: 1, completed: 1 }, latest_actual_date: month }] : [] }
}
