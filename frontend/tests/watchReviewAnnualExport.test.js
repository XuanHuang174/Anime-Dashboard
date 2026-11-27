import test from 'node:test'
import assert from 'node:assert/strict'
import { loadReviewExport, verifyReviewExport, reviewExportBlocks, exportFilename } from '../src/utils/watchReviewExport.js'
import { annual, zero } from './fixtures/watchReviewAnnualData.js'

const work = id => ({ record_id: id, title: `年度清单 ${id}`, original_title: '', airing_period: '', poster_url: '', current_score: null, current_watch_status: 'completed', metadata_basis: 'current_journal' })
const row = id => ({ entry_type: 'work', work: work(id), matched_fact_count: 1, matched_kinds: { completed: 1 }, latest_actual_date: '2026-01' })
function fullSource(total = 101) {
  const source = annual()
  source.summary = { ...zero(), completed_works: total, completed_rounds: total, first_completed_rounds: total,
    viewed_works: total, related_works: total, activity_facts: total,
    completed_round_rating: { count: 0, missing_count: total, average: null, basis: 'effective_completed_round' } }
  source.annual.months.forEach((month, index) => { month.facts = index ? 0 : total; month.summary = index ? zero() : structuredClone(source.summary) })
  source.annual.month_unknown = { facts: 0, works: 0, summary: zero() }
  source.count = total; source.next_page = total > 12 ? 2 : null; source.results = Array.from({ length: Math.min(total, 12) }, (_, i) => row(i + 1))
  return source
}
const client = (source, calls, mutate = () => {}) => ({ get: async (path, { params: q }) => {
  calls.push({ path, params: q })
  const data = structuredClone(source)
  if (path.endsWith('entries/')) Object.assign(data, { section: q.section, page: q.page, page_size: q.page_size, count: source.count,
    count_unit: 'works', next_page: q.page * q.page_size < source.count ? q.page + 1 : null,
    results: Array.from({ length: Math.min(q.page_size, Math.max(0, source.count - (q.page - 1) * q.page_size)) }, (_, i) => row((q.page - 1) * q.page_size + i + 1)) })
  mutate(data, q, path)
  return { data }
} })

test('annual exports read every selected page with year mode and the original annual proof', async () => {
  const source = fullSource(), calls = []
  const report = await loadReviewExport(client(source, calls), source, { summary: true, months: true, works: true }, new AbortController().signal)
  assert.equal(report.sections.works.length, 101); assert.equal(calls.length, 3)
  for (const call of calls) {
    assert.equal(call.params.year, '2026'); assert.equal(call.params.snapshot, source.snapshot)
    assert.equal(Object.hasOwn(call.params, 'start'), false); assert.equal(Object.hasOwn(call.params, 'month'), false)
  }
  const blocks = reviewExportBlocks(report, { summary: true, months: true, works: true })
  assert.equal(blocks.filter(b => b.kind === 'months').length, 1)
  assert.equal(blocks.find(b => b.kind === 'months').months.length, 12)
  assert.ok(blocks.some(b => b.title === '年度清单 101'))
  assert.equal(exportFilename(report), '2026年观看回顾')
})

test('monthly distribution and unknown-month explanation are selectable without exporting private text', () => {
  const source = annual(), secret = '私人轮次备注不可默认导出', report = { source, sections: { completed: [{ work: work(1), round_number: 1,
    kind: 'completed', actual_date: '2026', date_precision: 'year', rating: '9.0', note: secret }], notes: [] } }
  const selected = reviewExportBlocks(report, { summary: true, months: true, completed: true })
  assert.equal(selected[0].title, '这一年的观看记录')
  assert.match(selected.find(b => b.title === '属于本年，月份待补充').text, /1 条记录，涉及 1 部作品/)
  assert.equal(JSON.stringify(selected).includes(secret), false)
  assert.equal(reviewExportBlocks(report, { summary: true }).some(b => b.kind === 'months'), false)
  assert.ok(reviewExportBlocks(report, { completed: true, notes: true }).some(b => b.text.includes(secret)))
})

test('coherent-looking annual distribution drift under the same proof is rejected at every export check', async () => {
  const source = fullSource(), calls = []
  const drift = data => { [data.annual.months[0].summary, data.annual.months[1].summary] = [data.annual.months[1].summary, data.annual.months[0].summary];
    [data.annual.months[0].facts, data.annual.months[1].facts] = [data.annual.months[1].facts, data.annual.months[0].facts] }
  await assert.rejects(loadReviewExport(client(source, calls, (data, q) => { if (q.page === 2) drift(data) }), source, { works: true }, new AbortController().signal), /不完整/)
  await assert.rejects(verifyReviewExport(client(source, [], drift), { source, params: { year: '2026' } }, new AbortController().signal, () => true), /变化/)
  const partial = structuredClone(source); partial.annual.months.pop()
  const noCalls = []
  await assert.rejects(loadReviewExport(client(source, noCalls), partial, { months: true }, new AbortController().signal), /不完整/)
  assert.equal(noCalls.length, 0)
})

test('summary-only annual reports are verified; selection limits and cancellation never yield partial success', async () => {
  const source = fullSource(), calls = []
  await loadReviewExport(client(source, calls), source, { months: true }, new AbortController().signal)
  assert.equal(calls.length, 1); assert.equal(calls[0].path, 'watch-review/')
  const huge = fullSource(501)
  await assert.rejects(loadReviewExport(client(huge, []), huge, { works: true }, new AbortController().signal), /500/)
  const controller = new AbortController(); controller.abort()
  await assert.rejects(loadReviewExport(client(source, []), source, { months: true }, controller.signal), /取消/)
  await assert.rejects(loadReviewExport({ get: async () => { throw { response: { status: 409 } } } }, source, { months: true }, new AbortController().signal))
})
