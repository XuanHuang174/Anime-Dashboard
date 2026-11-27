import test from 'node:test'
import assert from 'node:assert/strict'
import { createReviewController, reviewParams, reviewPeriod, reviewTitle, shiftReviewYear, validReviewSummary, validReviewEntries } from '../src/utils/watchReview.js'

import { annual, monthly } from './fixtures/watchReviewAnnualData.js'

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
