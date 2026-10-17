import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { BROWSE_UPDATED, DEFAULT_BROWSE_FILTERS, mapBrowsePage, filterDraftError, filterChips } from '../src/utils/recommendationBrowse.js'
import { createBrowseController } from '../src/utils/recommendationBrowseSession.js'
import { browsePage } from './fixtures/browseContract.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const selection = (changes = {}) => ({ key: 'initial', category: 'all', style: 'balanced', filters: { ...DEFAULT_BROWSE_FILTERS }, ...changes })
const prefs = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] }
const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, cold_start: false,
  sample_policy: { minimum_score: '8.5' }, tag_summary: [] }
function service() {
  const calls = [], behavior = { request: null }
  const client = { profile: async () => profile, preferences: async () => prefs,
    list: () => { throw new Error('Legacy fallback forbidden') },
    browse: (options, signal) => {
      calls.push({ options, signal })
      return behavior.request ? behavior.request(options, signal) : Promise.resolve(browsePage({
        ...options, offset: options.cursor ? Number(options.cursor.split('-').at(-1)) : 0 }))
    } }
  return { controller: createBrowseController(client), calls, behavior, client }
}

test('9B transport sends explicit browse controls, omits blank years, and continues with cursor only', async () => {
  const calls = [], api = createRecommendationClient({ get: async (path, config) => { calls.push({ path, ...config }); return { data: {} } } })
  const signal = new AbortController().signal
  await api.browse(selection(), signal)
  assert.deepEqual(calls[0].params, { browse: true, category: 'all', style: 'balanced', type: 'all', min_score: '0', hide_recorded: true, page_size: 12 })
  await api.browse({ ...selection(), filters: { ...DEFAULT_BROWSE_FILTERS, yearFrom: '2015', yearTo: '2026' } }, signal)
  assert.equal(calls[1].params.year_from, '2015')
  await api.browse({ cursor: 'signed-cursor', category: 'explore' }, signal)
  assert.deepEqual(calls[2].params, { cursor: 'signed-cursor' })
  assert.equal(calls[2].signal, signal)
  assert.ok(calls.every(call => !('algorithm_version' in call.params) && !('user_id' in call.params)))
})

test('9B strict contract rejects legacy pagination, wrong counts, mixed evidence, unsafe and duplicate rows', () => {
  const raw = browsePage()
  assert.equal(mapBrowsePage(raw).items.length, 12)
  for (const changes of [{ algorithm_version: 'content.v2' }, { state_token: 'a'.repeat(64) }, { next_page: 2 },
    { batch: 7 }, { batch_count: 12 }, { count: 121 }, { exhausted: true }, { next_page_cursor: null },
    { snapshot_id: 'invalid' }, { page_size: 25 }, { results: raw.results.slice(1) },
    { results: [raw.results[0], raw.results[0], ...raw.results.slice(2)] },
    { results: [{ ...raw.results[0], content_rating: 'adult' }, ...raw.results.slice(1)] },
    { results: [{ ...raw.results[0], evidence_context: { algorithm_version: 'content.v2' } }, ...raw.results.slice(1)] }]) {
    assert.throws(() => mapBrowsePage({ ...raw, ...changes }))
  }
})

test('9B append stays within batch, switching replaces cards, and round ends without a loop', async () => {
  const { controller } = service(); await controller.start(selection())
  const seen = controller.getSnapshot().items.map(item => item.bangumiId)
  await controller.loadMore()
  assert.equal(controller.getSnapshot().items.length, 24)
  await controller.nextBatch()
  assert.equal(controller.getSnapshot().items.length, 12)
  assert.equal(controller.getSnapshot().data.batch, 2)
  assert.ok(controller.getSnapshot().items.every(item => !seen.includes(item.bangumiId)))
  await controller.loadMore(); await controller.nextBatch()
  assert.equal(controller.getSnapshot().items.length, 2)
  assert.equal(controller.getSnapshot().data.exhausted, true)
  assert.equal(await controller.nextBatch(), false)
})

test('9B rapid clicks across both actions produce exactly one continuation request', async () => {
  const { controller, behavior, calls } = service(); await controller.start(selection())
  const held = deferred(); behavior.request = () => held.promise
  const more = controller.loadMore()
  await Promise.all([controller.loadMore(), controller.nextBatch(), controller.loadMore()])
  assert.equal(calls.length, 2)
  held.resolve(browsePage({ offset: 12 })); await more
  assert.equal(controller.getSnapshot().items.length, 24)
})

test('9B a failed next batch keeps old items and retries precisely the original cursor', async () => {
  const { controller, behavior, calls } = service(); await controller.start(selection())
  const before = controller.getSnapshot().items
  behavior.request = async () => { throw new Error('offline') }
  await controller.nextBatch()
  assert.equal(controller.getSnapshot().items, before)
  assert.ok(controller.getSnapshot().batchError)
  assert.equal(controller.getSnapshot().data.batch, 1)
  behavior.request = async () => browsePage({ offset: 24 })
  await controller.nextBatch()
  assert.equal(calls.at(-2).options.cursor, calls.at(-1).options.cursor)
  assert.equal(controller.getSnapshot().data.batch, 2)
})

test('9B append failure is local and retry does not duplicate existing cards', async () => {
  const { controller, behavior } = service(); await controller.start(selection())
  behavior.request = async () => { throw { response: { status: 503 } } }
  await controller.loadMore()
  assert.equal(controller.getSnapshot().status, 'ready')
  assert.equal(controller.getSnapshot().items.length, 12)
  assert.ok(controller.getSnapshot().moreError)
  behavior.request = async () => browsePage({ offset: 12 }); await controller.loadMore()
  assert.equal(new Set(controller.getSnapshot().items.map(item => item.id)).size, 24)
})

test('9B 409 does not auto-retry or silently mix snapshots and leaves one explicit recovery', async () => {
  const { controller, behavior, calls } = service(); await controller.start(selection())
  behavior.request = async () => { throw { response: { status: 409 } } }; await controller.loadMore()
  assert.equal(controller.getSnapshot().error, BROWSE_UPDATED)
  assert.equal(controller.getSnapshot().snapshotExpired, true)
  assert.equal(controller.getSnapshot().items.length, 12)
  assert.ok(controller.getSnapshot().items.every(item => item.evidence === null && item.evidenceToken === undefined && item.reason === ''))
  assert.equal(calls.length, 2)
  await controller.nextBatch(); assert.equal(calls.length, 2)
  behavior.request = async () => browsePage(); await controller.retry()
  assert.equal(calls.at(-1).options.cursor, undefined)
  assert.equal(controller.getSnapshot().snapshotExpired, undefined)
})

test('9B any changed epoch, cursor sequence or duplicate across batches invalidates the old stream', async () => {
  for (const changes of [{ snapshot_id: 'b'.repeat(32) }, { snapshot_expires_at: 2000000001 },
    { evidence_context: { algorithm_version: 'content.v1', profile_version: 'b'.repeat(64) } }]) {
    const { controller, behavior } = service(); await controller.start(selection())
    behavior.request = async () => ({ ...browsePage({ offset: 12 }), ...changes })
    await controller.loadMore(); assert.equal(controller.getSnapshot().snapshotExpired, true)
    assert.equal(controller.getSnapshot().items.length, 12)
  }
  const { controller, behavior } = service(); await controller.start(selection())
  behavior.request = async () => browsePage({ offset: 24, ids: Array.from({ length: 50 }, (_, n) => 101 + n % 24) })
  await controller.nextBatch(); assert.equal(controller.getSnapshot().snapshotExpired, true)
})

test('9B late success and errors cannot write after control changes, refresh, dispose or StrictMode reactivation', async () => {
  const { controller, behavior, calls } = service(); await controller.start(selection())
  const held = deferred(); behavior.request = () => held.promise
  const stale = controller.loadMore(), oldSignal = calls.at(-1).signal
  behavior.request = async options => browsePage({ ...options, ids: Array.from({ length: 30 }, (_, n) => 301 + n) })
  await controller.start(selection({ key: 'new', category: 'explore', style: 'open' }))
  held.resolve(browsePage({ offset: 12 })); await stale
  assert.equal(oldSignal.aborted, true)
  assert.equal(controller.getSnapshot().items[0].bangumiId, 301)
  const late = deferred(); behavior.request = () => late.promise
  const abandoned = controller.nextBatch(); controller.dispose()
  late.reject(new Error('late failure')); await abandoned
  assert.equal(controller.getSnapshot().batchError, '')
  controller.activate(); behavior.request = async () => browsePage(); await controller.start(selection())
  assert.equal(controller.getSnapshot().status, 'ready')
})

test('9B mutation refresh clears cursors, preserves acknowledged write semantics, and never posts again', async () => {
  const { controller, behavior, calls } = service(); await controller.start(selection()); await controller.nextBatch()
  controller.recordChanged(101, 'plan')
  behavior.request = async () => { throw new Error('offline') }
  await controller.retry({ confirmedFeedback: { version: 2 } })
  assert.equal(controller.getSnapshot().confirmedFeedback.version, 2)
  assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(calls.at(-1).options.cursor, undefined)
  behavior.request = async () => browsePage({ context: { algorithm_version: 'content.v1', profile_version: 'b'.repeat(64), preference_version: 0, feedback_version: 2 } })
  await controller.retry()
  assert.equal(controller.getSnapshot().data.batch, 1)
  assert.equal(controller.getSnapshot().confirmedFeedback, undefined)
})

test('9B request failure is neither cold start nor empty and never falls back to a legacy or mock list', async () => {
  const { controller, behavior } = service()
  behavior.request = async () => { throw new Error('offline') }; await controller.start(selection())
  assert.equal(controller.getSnapshot().status, 'error')
  behavior.request = async () => browsePage({ ids: [] }); await controller.retry()
  assert.equal(controller.getSnapshot().status, 'empty')
})

test('9B year drafts and chip counts are deterministic without changing explicit preference data', () => {
  assert.equal(filterDraftError({ yearFrom: '2026', yearTo: '2015' }), '年份起点不能大于终点。')
  assert.ok(filterDraftError({ yearFrom: '2026x', yearTo: '' }))
  assert.equal(filterDraftError({ yearFrom: '', yearTo: '' }), '')
  assert.equal(filterChips(DEFAULT_BROWSE_FILTERS).length, 1)
  assert.equal(filterChips({ ...DEFAULT_BROWSE_FILTERS, type: 'TV', minScore: '7', yearFrom: '2015', yearTo: '2026' }).length, 4)
})
