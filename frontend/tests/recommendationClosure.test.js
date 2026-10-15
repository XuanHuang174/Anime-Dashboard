import test from 'node:test'
import assert from 'node:assert/strict'
import { createWantController, wantButtonState } from '../src/utils/recommendationWant.js'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { createResourceSearchSession } from '../src/utils/resourceSearchSession.js'
import { mapRecommendationItem } from '../src/utils/recommendations.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const raw = (id = 101, changes = {}) => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: `作品${id}`,
  title_jp: 'Original', score: 7.5, season: '2026-04', tags: ['音乐'], evidence: null, reason: '',
  evidence_token: 'a'.repeat(64), watch_status: null, ...changes })
const ack = (id = 101) => ({ contract_version: 'recommendations.want.v1', bangumi_id: id, anime_id: 9,
  record_id: 7, content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' })

test('7C hero/grid/detail copies share exactly one pending want and identical disabled/confirmed states', async () => {
  const post = deferred(); let posts = 0, confirmations = 0
  const controller = createWantController({ want: () => { posts++; return post.promise } }, () => confirmations++)
  const surfaces = [mapRecommendationItem(raw()), mapRecommendationItem(raw()), mapRecommendationItem(raw())]
  const promises = Array.from({ length: 30 }, (_, index) => controller.add(surfaces[index % 3]))
  assert.ok(promises.every(promise => promise === promises[0]))
  await Promise.resolve()
  assert.equal(posts, 1)
  for (const item of surfaces) assert.deepEqual(wantButtonState(item, controller.getSnapshot()[101]), { label: '正在加入…', disabled: true })
  post.resolve(ack()); await Promise.all(promises)
  assert.equal(confirmations, 1)
  for (const item of surfaces) assert.deepEqual(wantButtonState(item, controller.getSnapshot()[101]), { label: '已在想看', disabled: true })
  await controller.add(surfaces[0]); assert.equal(posts, 1)
})

test('7C known want acknowledgement discards whole detail and its resource session before refresh completes', async () => {
  const post = deferred(), refresh = deferred(), resourceRead = deferred(); let detailReads = 0, toasts = 0
  const detail = createRecommendationDetailController({ detail: async () => ++detailReads === 1 ? raw() : refresh.promise })
  const resources = createResourceSearchSession({ get: () => resourceRead.promise },
    { hasSession: () => true, copyText: async () => {}, onCopied: () => toasts++ })
  // Same lifecycle contract as mounted resource component cleanup.
  detail.subscribe(() => { if (!detail.getSnapshot().resourceOpen) resources.close() })
  await detail.open(mapRecommendationItem(raw())); detail.openResources()
  const pendingSearch = resources.open('作品101')
  let reloading
  const want = createWantController({ want: () => post.promise }, result => { reloading = detail.afterWant(result) })
  const adding = want.add(mapRecommendationItem(raw())); post.resolve(ack()); await adding
  assert.equal(detail.getSnapshot().status, 'refreshing')
  assert.equal(detail.getSnapshot().item, undefined)
  assert.equal(detail.getSnapshot().resourceOpen, false)
  resourceRead.reject(new Error('late offline')); await pendingSearch
  assert.equal(resources.getSnapshot().error, ''); assert.equal(toasts, 0)
  refresh.resolve(raw(101, { watch_status: 'plan', evidence_token: 'b'.repeat(64), season: '2026-07' })); await reloading
  assert.equal(detail.getSnapshot().item.season, '2026-07')
  assert.equal(detail.getSnapshot().item.evidenceToken, 'b'.repeat(64))
})

test('7C route disposal aborts pending want and suppresses late confirmation/detail reopening', async () => {
  const post = deferred(); let signal, confirmations = 0
  const want = createWantController({ want: (id, currentSignal) => { signal = currentSignal; return post.promise } }, () => confirmations++)
  const pending = want.add(mapRecommendationItem(raw())); await Promise.resolve()
  want.dispose(); assert.ok(signal.aborted)
  post.resolve(ack()); assert.equal(await pending, null)
  assert.equal(confirmations, 0)
})

test('7C resource timeout and detail stale context remain independent from a failed want', async () => {
  const detail = createRecommendationDetailController({ detail: async () => raw() })
  await detail.open(mapRecommendationItem(raw())); detail.openResources()
  const want = createWantController({ want: async () => { throw { response: { status: 503 } } } }, () => assert.fail('unconfirmed'))
  await want.add(mapRecommendationItem(raw()))
  assert.equal(detail.getSnapshot().status, 'ready'); assert.equal(detail.getSnapshot().resourceOpen, true)
  const resource = createResourceSearchSession({ get: async () => { throw { code: 'ECONNABORTED' } } },
    { hasSession: () => true, copyText: async () => {} })
  await resource.open('作品101')
  assert.match(resource.getSnapshot().error, /稍后再试/)
  assert.equal(detail.getSnapshot().status, 'ready')
  assert.equal(want.getSnapshot()[101].error.code, 'retry')
})
