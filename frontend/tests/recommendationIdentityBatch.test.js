import test from 'node:test'
import assert from 'node:assert/strict'
import { createIdentityReviewController, validateIdentityBatch, identitySeasonDiffers } from '../src/utils/recommendationIdentityBatch.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const row = id => ({ record_id: id, title: `旧作品${id}`, original_title: 'Original', season: '2026-1',
  identity_version: 0, status: 'needs_confirmation', candidates: [{ bangumi_id: id + 100,
    title_zh: `标准作品${id}`, title_jp: 'Original', season: '2026-01', status_type: 'TV', ticket: `signed-${id}`, content_rating: 'non_adult' }] })
const page = (n = 1, rows = Array.from({ length: 5 }, (_, i) => row((n - 1) * 5 + i + 1))) => ({ page: n, count: 30, next_page: n < 6 ? n + 1 : null, results: rows })
const result = (payload, outcomes = {}) => {
  const results = payload.items.map(item => ({ record_id: item.record_id, bangumi_id: item.bangumi_id,
    outcome: outcomes[item.record_id] || 'success', code: outcomes[item.record_id] ? 'identity_changed' : 'linked', version: 1 }))
  const success_count = results.filter(row => row.outcome === 'success').length
  return { contract_version: 'recommendations.identity-batch.v1', batch_id: payload.batch_id, results,
    processed_count: results.length, success_count, failed_count: results.length - success_count }
}
const make = (overrides = {}) => createIdentityReviewController({ identities: async options => page(options.page),
  confirmIdentities: async payload => result(payload), ...overrides }, 'high-score', () => 'synthetic-batch')
const choose = (controller, item = controller.getSnapshot().data.results[0]) => {
  controller.choose(item, item.candidates[0]); controller.confirm(item.record_id, true); return item
}

test('date warnings ignore month padding but never guess a season from title or real date differences', () => {
  assert.equal(identitySeasonDiffers('2026-1', '2026-01'), false)
  assert.equal(identitySeasonDiffers('2008-10', '2008-07'), true)
  assert.equal(identitySeasonDiffers('', '2026-01'), true)
  assert.equal(identitySeasonDiffers('年份未知', '2026-01'), true)
})

test('batch transport only posts explicit record IDs, candidate proof, version and signal', async () => {
  let call
  const client = createRecommendationClient({ post: async (...args) => { call = args; return { data: {} } } })
  const body = { batch_id: 'id', items: [] }, signal = new AbortController().signal
  await client.confirmIdentities(body, signal)
  assert.deepEqual(call, ['recommendations/identities/batch/', body, { signal }])
})

test('drafts survive pagination and no choice/confirmation/clear causes any write', async () => {
  let writes = 0
  const controller = make({ confirmIdentities: async () => { writes++ } })
  await controller.loadPage(); choose(controller)
  await controller.loadPage(2); choose(controller)
  await controller.loadPage(1)
  assert.equal(Object.keys(controller.getSnapshot().drafts).length, 2)
  assert.equal(controller.getSnapshot().drafts[1].confirmed, true)
  controller.clear(); assert.equal(writes, 0)
  controller.dispose()
})

test('changing candidate, new metadata or search invalidates only that row confirmation', async () => {
  const controller = make(); await controller.loadPage()
  const first = choose(controller); choose(controller, controller.getSnapshot().data.results[1])
  controller.choose(first, { ...first.candidates[0], bangumi_id: 999 })
  assert.equal(controller.getSnapshot().drafts[1].confirmed, false)
  assert.equal(controller.getSnapshot().drafts[2].confirmed, true)
  controller.confirm(1, true); await controller.loadPage()
  assert.equal(controller.getSnapshot().drafts[1].confirmed, false)
  assert.ok(controller.getSnapshot().drafts[1].error)
  await controller.search(first)
  assert.equal(controller.getSnapshot().drafts[1], undefined)
  assert.equal(controller.getSnapshot().drafts[2].confirmed, true)
})

test('twenty selected limit never silently drops earlier pages and removal frees capacity', async () => {
  const controller = make()
  for (let n = 1; n <= 4; n++) { await controller.loadPage(n); for (const item of controller.getSnapshot().data.results) choose(controller, item) }
  await controller.loadPage(5); choose(controller)
  assert.equal(Object.keys(controller.getSnapshot().drafts).length, 20)
  assert.match(controller.getSnapshot().message, /最多暂存 20/)
  controller.remove(1); choose(controller)
  assert.equal(Object.keys(controller.getSnapshot().drafts).length, 20)
})

test('rapid submissions share one POST and successful response removes only acknowledged drafts', async () => {
  const write = deferred(); let calls = 0, payload
  const controller = make({ confirmIdentities: body => { calls++; payload = body; return write.promise } })
  await controller.loadPage(); choose(controller); choose(controller, controller.getSnapshot().data.results[1])
  controller.confirm(2, false)
  const pending = controller.submit(), repeated = controller.submit(); assert.equal(pending, repeated)
  await Promise.resolve(); assert.equal(calls, 1); assert.equal(payload.items.length, 1)
  assert.equal(controller.getSnapshot().mayHaveChanged, true)
  write.resolve(result(payload)); await pending
  assert.equal(controller.getSnapshot().drafts[1], undefined)
  assert.ok(controller.getSnapshot().drafts[2]); assert.equal(calls, 1)
})

test('uncertain response freezes edits and retries EXACT same batch, including UUID and ticket', async () => {
  const bodies = []; let fail = true
  const controller = make({ confirmIdentities: async payload => { bodies.push(payload); if (fail) throw Error('offline'); return result(payload) } })
  await controller.loadPage(); const first = choose(controller)
  await controller.submit(); assert.equal(controller.getSnapshot().batch.status, 'uncertain')
  controller.clear(); controller.confirm(first.record_id, false); controller.remove(first.record_id)
  assert.equal(controller.getSnapshot().drafts[1].confirmed, true)
  fail = false; await controller.submit()
  assert.equal(bodies[0], bodies[1]); assert.equal(controller.getSnapshot().drafts[1], undefined)
})

test('partial conflict stays unconfirmed; retryable results replay original batch with successes too', async () => {
  let retry = true, bodies = []
  const controller = make({ confirmIdentities: async payload => { bodies.push(payload); return result(payload, retry ? { 2: 'retryable' } : {}) } })
  await controller.loadPage(); choose(controller); choose(controller, controller.getSnapshot().data.results[1])
  await controller.submit(); assert.equal(controller.getSnapshot().batch.status, 'retryable')
  assert.equal(controller.getSnapshot().drafts[1], undefined)
  retry = false; await controller.submit(); assert.equal(bodies[0], bodies[1])
  const conflict = make({ confirmIdentities: async payload => result(payload, { 1: 'needs_review' }) })
  await conflict.loadPage(); choose(conflict); await conflict.submit()
  assert.equal(conflict.getSnapshot().drafts[1].confirmed, false)
  conflict.confirm(1, true); assert.equal(conflict.getSnapshot().drafts[1].confirmed, false)
})

test('malformed, duplicated, foreign or incomplete acknowledgement never claims success', () => {
  const payload = { batch_id: 'x', items: [{ record_id: 1, bangumi_id: 101 }] }, good = result(payload)
  for (const bad of [{ ...good, batch_id: 'other' }, { ...good, success_count: 0 }, { ...good, results: [] },
    { ...good, results: [{ ...good.results[0], bangumi_id: 999 }] },
    { ...good, results: [{ ...good.results[0], version: -1 }] }]) assert.throws(() => validateIdentityBatch(bad, payload))
})

test('paging cancels old row searches; late callbacks and unmount cannot replace new context', async () => {
  const old = deferred(); let signal
  const controller = make({ identities: (options, supplied) => {
    if (options.record_id) { signal = supplied; return old.promise }
    return Promise.resolve(page(options.page))
  } })
  await controller.loadPage(); const searching = controller.search(controller.getSnapshot().data.results[0])
  await controller.loadPage(2); assert.equal(signal.aborted, true)
  old.resolve(page(1, [{ ...row(1), title: 'OLD' }])); await searching
  assert.equal(controller.getSnapshot().data.page, 2)
  assert.deepEqual(controller.getSnapshot().searches, {})
  const write = deferred(); let captured
  const unmounted = make({ confirmIdentities: async payload => { captured = payload; return write.promise } })
  await unmounted.loadPage(); choose(unmounted); const pending = unmounted.submit(); await Promise.resolve()
  unmounted.dispose(); write.resolve(result(captured)); await pending
  assert.notEqual(unmounted.getSnapshot().batch.status, 'result')
})

test('failed cross-page draft can be located by exact record ID without changing other choices', async () => {
  const calls = []
  const controller = make({ identities: async options => { calls.push(options); return options.record_id
    ? page(1, [row(options.record_id)]) : page(options.page) } })
  await controller.loadPage(); choose(controller)
  await controller.loadPage(2); choose(controller)
  await controller.loadPage(1, 6)
  assert.equal(calls.at(-1).record_id, 6); assert.equal(controller.getSnapshot().focusedId, 6)
  assert.equal(controller.getSnapshot().data.results.length, 1)
  assert.equal(Object.keys(controller.getSnapshot().drafts).length, 2)
  await controller.loadPage(1); assert.equal(controller.getSnapshot().focusedId, null)
})
