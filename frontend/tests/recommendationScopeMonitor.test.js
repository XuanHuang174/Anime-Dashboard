import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { watchRecommendationScope } from '../src/utils/recommendationScopeMonitor.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve() }
function clock() {
  let time = 0, serial = 0
  const target = new EventTarget(), document = new EventTarget(), timers = new Map()
  document.visibilityState = 'visible'
  target.setTimeout = (fn, ms) => { const id = ++serial; timers.set(id, { at: time + ms, fn }); return id }
  target.clearTimeout = id => timers.delete(id)
  return { target, document, now: () => time, timers,
    async advance(ms) {
      const end = time + ms
      while (true) {
        const next = [...timers].filter(([, value]) => value.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0]
        if (!next) break
        time = next[1].at; timers.delete(next[0]); next[1].fn(); await flush()
      }
      time = end; await flush()
    } }
}
function setup(changes = {}) {
  const runtime = clock(), task = deferred(), calls = [], losses = [], contexts = []
  const monitor = watchRecommendationScope({ ...runtime, deadline: 40000,
    probe: signal => { calls.push(signal); return task.promise }, onLost: () => losses.push(runtime.now()),
    onContext: data => contexts.push(data), ...changes })
  return { ...runtime, task, calls, losses, contexts, monitor }
}

test('expiry clears access at the exact clock boundary even if the probe never resolves', async () => {
  const state = setup()
  await state.advance(30000); assert.equal(state.calls.length, 1)
  await state.advance(10000); assert.deepEqual(state.losses, [40000]); assert.ok(state.calls[0].aborted)
  state.task.resolve({ stale: true }); await flush(); assert.deepEqual(state.contexts, [])
  await state.advance(300000); assert.equal(state.losses.length, 1); state.monitor.dispose()
})

test('a bounded slow probe cancels and retries without declaring offline as revoked', async () => {
  const state = setup({ deadline: 90000 })
  await state.advance(50000); assert.ok(state.calls[0].aborted); assert.equal(state.losses.length, 0)
  await state.advance(30000); assert.equal(state.calls.length, 2)
  await state.advance(10000); assert.deepEqual(state.losses, [90000]); state.monitor.dispose()
})

test('offline/503 uncertainty preserves access only until its independent deadline', async () => {
  const state = setup()
  await state.advance(30000); state.task.reject({ response: { status: 503 } }); await flush()
  assert.deepEqual(state.losses, []); await state.advance(10000); assert.deepEqual(state.losses, [40000])
  state.monitor.dispose()
})

test('hiding aborts in-flight probes, stops background polling but never pauses expiry', async () => {
  const state = setup()
  await state.advance(30000)
  state.document.visibilityState = 'hidden'; state.document.dispatchEvent(new Event('visibilitychange'))
  assert.ok(state.calls[0].aborted)
  await state.advance(10000); assert.deepEqual(state.losses, [40000]); assert.equal(state.calls.length, 1)
  state.monitor.dispose()
})

test('foreground/focus bursts coalesce, disposal cancels all timers and stale 403 cannot cross sessions', async () => {
  const state = setup({ deadline: 300000 })
  state.document.dispatchEvent(new Event('visibilitychange')); state.target.dispatchEvent(new Event('focus')); await flush()
  assert.equal(state.calls.length, 1)
  state.monitor.dispose(); assert.ok(state.calls[0].aborted); assert.equal(state.timers.size, 0)
  state.task.reject({ response: { status: 403 } }); await flush(); assert.equal(state.losses.length, 0)
  state.target.dispatchEvent(new Event('focus')); await state.advance(500000); assert.equal(state.calls.length, 1)
})

test('confirmed authorization conflicts close access immediately once', async () => {
  for (const status of [403, 409]) {
    const state = setup(); await state.advance(30000)
    state.task.reject({ response: { status } }); await flush()
    assert.deepEqual(state.losses, [30000]); await state.advance(100000); assert.equal(state.losses.length, 1)
    state.monitor.dispose()
  }
})

test('already expired contracts never start a request; long timers do not overflow browser limits', async () => {
  const state = setup({ deadline: 0 }); assert.deepEqual(state.losses, [0]); assert.equal(state.calls.length, 0); state.monitor.dispose()
  const future = setup({ deadline: 2592000000 })
  assert.ok([...future.timers.values()].every(timer => timer.at <= 2147483647)); future.monitor.dispose()
})

const scope = revision => ({ content_scope_context: { content_scope: 'include_adult', content_scope_contract_version: 'recommendations.content-scope.n2-2.v1',
  content_scope_policy_version: 'content-scope.controlled.m2.v1', eligibility_version: 2, content_review_version: 1,
  serving_control_version: 1, release_revision: 'a'.repeat(64), scope_revision: revision.repeat(64),
  scope_valid_until: new Date(Date.now() + 3600000).toISOString() } })

test('aborted late success cannot poison scoped-client revision or turn the next good probe into a false revocation', async () => {
  const late = deferred(); let lost = 0
  const api = createRecommendationClient({ get: async path => path.includes('/12/') ? late.promise : { data: scope('c') } })
    .forScope('include_adult', () => lost++)
  const abort = new AbortController(), old = api.detail(12, abort.signal); abort.abort()
  await api.context(); late.resolve({ data: scope('b') }); await assert.rejects(old, { name: 'AbortError' })
  await api.context(); assert.equal(lost, 0)
})

test('an older different revision is discarded, while same-revision concurrent reads remain usable', async () => {
  const late = deferred(); let lost = 0
  const api = createRecommendationClient({ get: async path => path.includes('/12/') ? late.promise : { data: scope('c') } })
    .forScope('include_adult', () => lost++)
  const old = api.detail(12); await api.context(); late.resolve({ data: scope('b') })
  await assert.rejects(old, { name: 'AbortError' }); await api.context(); assert.equal(lost, 0)
  const same = deferred(), good = createRecommendationClient({ get: async path => path.includes('/12/') ? same.promise : { data: scope('c') } }).forScope('include_adult')
  const first = good.detail(12); await good.context(); same.resolve({ data: scope('c') }); assert.ok(await first)
})

test('scope supervision is wired only to explicit controlled browsing; ordinary focus does not start this monitor', () => {
  const page = readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8')
  assert.match(page, /if \(!filters.includeAdult\) return undefined/)
  assert.match(page, /watchRecommendationScope\(\{ deadline/)
  assert.match(page, /return \(\) => monitor.dispose\(\)/)
})
