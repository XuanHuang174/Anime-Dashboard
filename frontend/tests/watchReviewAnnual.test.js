import test from 'node:test'
import assert from 'node:assert/strict'
import { createReviewController, reviewParams, reviewPeriod, reviewTitle, shiftReviewYear, validReviewSummary, validReviewEntries } from '../src/utils/watchReview.js'

const zero = () => ({ started_works: 0, started_rounds: 0, completed_works: 0, completed_rounds: 0, first_completed_rounds: 0,
  rewatch_started_rounds: 0, rewatch_completed_rounds: 0, notes: 0, memories: 0, viewed_works: 0, related_works: 0, activity_facts: 0,
  completed_round_rating: { count: 0, missing_count: 0, average: null, basis: 'effective_completed_round' } })
const work = id => ({ record_id: id, title: `年度作品 ${id}`, original_title: '', airing_period: '', poster_url: '', current_score: '9.0', current_watch_status: 'completed', metadata_basis: 'current_journal' })
function annual(query = {}, token = 'annual-snapshot') {
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
function monthly(query = {}) {
  const data = annual(), month = query.month || '2026-01', row = data.annual.months[Number(month.slice(5)) - 1]
  delete data.annual
  const count = row.summary.related_works
  return { ...data, period: reviewPeriod({ month }), snapshot: 'monthly-snapshot', summary: row.summary, section: 'works', page: 1, page_size: query.page_size || 12, count, next_page: null,
    results: count ? [{ entry_type: 'work', work: work(1), matched_fact_count: row.facts, matched_kinds: { started: 1, completed: 1 }, latest_actual_date: month }] : [] }
}
const options = { params: { year: '2026' }, section: 'works', page: 1, pageSize: 12 }

test('annual date helpers preserve natural years, leap years and distinct report titles', () => {
  assert.deepEqual(reviewParams({ mode: 'year', year: '2024', month: '2026-01' }), { year: '2024' })
  assert.deepEqual(reviewPeriod({ year: '2024' }), { mode: 'year', start: '2024-01-01', end: '2024-12-31', timezone: 'Asia/Shanghai', end_inclusive: true })
  assert.equal(reviewTitle(reviewPeriod({ year: '2026' })), '2026年观看回顾')
  assert.equal(shiftReviewYear('2026', -1), '2025')
  for (const y of ['', '0999', '10000', '2026-01', '2026.5']) assert.throws(() => reviewParams({ mode: 'year', year: y }))
  assert.throws(() => shiftReviewYear('1000', -1)); assert.throws(() => shiftReviewYear('9999', 1))
})

test('annual contracts require all twelve ordered months, coherent counts and a distinct unknown-month bucket', () => {
  assert.equal(validReviewEntries(annual(), options), true)
  const mutations = [d => delete d.annual, d => d.annual.months.pop(), d => d.annual.months.reverse(),
    d => d.annual.months[1].month = '2026-01', d => delete d.annual.month_unknown,
    d => d.annual.month_unknown.works = 2, d => d.annual.months[0].facts = 100,
    d => d.annual.months[0].summary.started_rounds++, d => d.annual.year = '2025',
    d => d.summary.completed_works = 0, d => d.summary.completed_round_rating.count = 1]
  for (const change of mutations) { const data = annual(); change(data); assert.equal(validReviewSummary(data, options.params), false) }
  const regular = monthly(); assert.equal(validReviewSummary(regular, { month: '2026-01' }), true)
  regular.annual = annual().annual; assert.equal(validReviewSummary(regular, { month: '2026-01' }), false)
})

test('unknown-month facts retain year precision and cannot masquerade as a month or an unknown date', () => {
  const data = annual({ section: 'month_unknown' }), opts = { ...options, section: 'month_unknown' }
  assert.equal(validReviewEntries(data, opts), true)
  for (const change of [d => d.results[0].date_precision = 'month', d => d.results[0].actual_date = '2026-01',
    d => d.results[0].attribution = 'undated', d => d.count = 2]) {
    const bad = structuredClone(data); change(bad); assert.equal(validReviewEntries(bad, opts), false)
  }
  const changed = annual(); [changed.annual.months[0], changed.annual.months[9]] = [
    { ...changed.annual.months[9], month: '2026-01' }, { ...changed.annual.months[0], month: '2026-10' }]
  assert.equal(validReviewEntries(changed, options), true)
  assert.equal(validReviewEntries(changed, { ...options, snapshot: data.snapshot, baseline: annual() }), false)
})

test('year-to-month navigation drops annual-only sections and can resume a guarded annual page', async () => {
  const calls = [], engine = createReviewController({ getSession: () => 'owner', initialParams: { year: '2026' },
    client: { get: async (_path, { params: q }) => { calls.push(q); return { data: q.year ? annual(q) : monthly(q) } } } })
  assert.equal(await engine.read({ section: 'month_unknown', pageSize: 1 }), true)
  const checkpoint = engine.getSnapshot()
  assert.equal(await engine.read({ params: { month: '2026-01' } }), true)
  assert.equal(calls[1].section, 'works'); assert.equal(calls[1].snapshot, undefined)
  // Restore a prior page of a different cohort without displaying the cached data.
  const saved = { ...checkpoint, section: 'works', page: 2, data: annual({ page: 2, page_size: 1 }) }
  assert.equal(await engine.read({ params: saved.params, section: saved.section, page: 2, pageSize: 1, checkpoint: saved }), true)
  assert.equal(calls[2].snapshot, 'annual-snapshot'); assert.equal(calls[2].page, 2)
  assert.equal(engine.getSnapshot().page, 2)
})

test('expired annual return checkpoints refresh once to page one and cannot survive another account', async () => {
  let owner = 'owner'; const calls = [], saved = { params: { year: '2026' }, data: annual() }
  const engine = createReviewController({ getSession: () => owner, client: { get: async (_path, { params: q }) => {
    calls.push(q); if (q.snapshot) throw { response: { status: 409 } }; return { data: annual(q, 'fresh-annual') }
  } } })
  assert.equal(await engine.read({ params: saved.params, checkpoint: saved, page: 2 }), true)
  assert.equal(calls.length, 2); assert.equal(calls[1].page, 1); assert.equal(calls[1].snapshot, undefined)
  assert.match(engine.getSnapshot().message, /已更新/)
  owner = 'other'; assert.equal(await engine.read({ params: saved.params, checkpoint: saved }), false)
  assert.equal(calls.length, 2)
})
