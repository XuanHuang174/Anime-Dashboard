import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { createWantController, validateWantResult, wantButtonState, wantFailure } from '../src/utils/recommendationWant.js'

const item = { bangumiId: 123, title: '作品', watchStatus: null }
const result = { contract_version: 'recommendations.want.v1', bangumi_id: 123, anime_id: 9, record_id: 7,
  created: true, outcome: 'created', watch_status: 'plan', content_rating: 'non_adult' }
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

test('only dedicated Bangumi URL with empty JSON is used, IDs stay separate', async () => {
  const calls = [], client = createRecommendationClient({ post: async (...args) => { calls.push(args); return { data: result } } })
  const signal = new AbortController().signal
  assert.equal(await client.want(123, signal), result)
  assert.equal(calls[0][0], 'recommendations/123/want/')
  assert.deepEqual(calls[0][1], {})
  assert.equal(calls[0][2].signal, signal)
  for (const id of ['123', '../user/1', 0, 2147483648, NaN]) await assert.rejects(client.want(id, signal))
  assert.equal(calls.length, 1)
})

test('hero, grid and detail repeated clicks share exactly one in-flight POST', async () => {
  const deferred = defer(); let calls = 0
  const controller = createWantController({ want: () => { calls += 1; return deferred.promise } })
  const first = controller.add(item), second = controller.add(item)
  assert.equal(first, second)
  assert.equal(wantButtonState(item, controller.getSnapshot()[123]).label, '正在加入…')
  await Promise.resolve(); assert.equal(calls, 1)
  deferred.resolve(result); await first
  assert.equal(wantButtonState(item, controller.getSnapshot()[123]).label, '已在想看')
  await controller.add(item); assert.equal(calls, 1)
})

test('existing completed status is preserved instead of optimistic plan', async () => {
  const controller = createWantController({ want: async () => ({ ...result, created: false, outcome: 'existing', watch_status: 'completed' }) })
  const returned = await controller.add(item)
  assert.equal(returned.watch_status, 'completed')
  assert.deepEqual(wantButtonState(item, controller.getSnapshot()[123]), { label: '已看过', disabled: true })
  for (const status of ['plan', 'watching', 'completed', 'on_hold']) {
    assert.equal(wantButtonState({ ...item, watchStatus: status }).disabled, true)
  }
})

test('lost response retries safely, no fake success or Mock fallback', async () => {
  let calls = 0
  const controller = createWantController({ want: async () => {
    if (++calls === 1) throw new Error('response lost after commit')
    return { ...result, created: false, outcome: 'existing' }
  } })
  assert.equal(await controller.add(item), null)
  assert.equal(wantButtonState(item, controller.getSnapshot()[123]).label, '重试加入想看')
  assert.equal(controller.getSnapshot()[123].watchStatus, undefined)
  assert.equal((await controller.add(item)).record_id, 7)
  assert.equal(calls, 2)
})

test('identity confirmation, field length and quota have explicit distinct states', () => {
  const identity = wantFailure({ response: { status: 409, data: { code: 'identity_confirmation_required' } } })
  assert.equal(wantButtonState(item, { error: identity }).confirm, true)
  for (const code of ['journal_field_conflict', 'public_identity_conflict', 'capacity_exceeded']) {
    const error = wantFailure({ response: { data: { code } } })
    assert.equal(wantButtonState(item, { error }).disabled, true)
    assert.ok(error.message)
  }
})

test('confirmation clears only failed operations, ready/adding states stay scoped', async () => {
  const controller = createWantController({ want: async id => {
    if (id === 124) throw { response: { data: { code: 'identity_confirmation_required' } } }
    return result
  } })
  await controller.add(item); await controller.add({ ...item, bangumiId: 124 })
  controller.resetFailures()
  assert.equal(controller.getSnapshot()[123].status, 'ready')
  assert.equal(controller.getSnapshot()[124], undefined)
})

test('unmount aborts and suppresses late success, StrictMode reactivation can retry', async () => {
  const old = defer(); let calls = 0, oldSignal
  const controller = createWantController({ want: (id, signal) => {
    calls += 1; if (calls === 1) { oldSignal = signal; return old.promise } return Promise.resolve(result)
  } })
  const pending = controller.add(item); await Promise.resolve()
  controller.dispose(); assert.equal(oldSignal.aborted, true)
  controller.activate()
  await controller.add(item)
  old.resolve({ ...result, record_id: 999 }); await pending
  assert.equal(controller.getSnapshot()[123].recordId, 7)
})

test('identity namespace or unsafe response mismatch cannot update button', () => {
  for (const delta of [{ bangumi_id: 9 }, { record_id: '7' }, { content_rating: null }, { content_rating: 'adult' },
    { watch_status: 'unknown' }, { created: false }, { anime_id: 0 }, { contract_version: 'fake' }]) {
    assert.throws(() => validateWantResult({ ...result, ...delta }, 123))
  }
})

test('different users/controllers and different works never share operation state', async () => {
  const first = createWantController({ want: async () => result }), second = createWantController({ want: async () => result })
  await first.add(item)
  assert.equal(second.getSnapshot()[123], undefined)
  assert.equal(first.getSnapshot()[124], undefined)
})
