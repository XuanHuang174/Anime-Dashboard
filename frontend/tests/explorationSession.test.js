import test from 'node:test'
import assert from 'node:assert/strict'
import { mapExplorationPage, explorationDetailAdapter, similarQuery } from '../src/utils/explorationResults.js'
import { createExplorationBrowseController } from '../src/utils/explorationSession.js'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { validateExplorationCapabilities, parseSimilarSeed } from '../src/utils/recommendationExploration.js'
import { page, detail, deferred, query, adultScope } from './fixtures/explorationContract.js'
import { readFileSync } from 'node:fs'

test('deep links accept one external ID, reject duplicates and never interpret journal PK parameters', () => {
  assert.deepEqual(parseSimilarSeed('?seed=123'), { id: 123 })
  assert.deepEqual(parseSimilarSeed('?record_id=123'), { id: null })
  for (const value of ['?seed=0', '?seed=0012', '?seed=true', '?seed=12&seed=13', '?seed=2147483648', '?seed=12.0']) {
    assert.equal(parseSimilarSeed(value).id, null); assert.ok(parseSimilarSeed(value).error)
  }
})

test('11C capabilities expose similar only when enabled, with rolling 11B navigation accepted', () => {
  const descriptor = JSON.parse(readFileSync(new URL('./fixtures/recommendationExplorationContract.json', import.meta.url)))
  assert.equal(validateExplorationCapabilities(descriptor.capabilities).navigation[1].available, true)
  assert.throws(() => validateExplorationCapabilities({ ...descriptor.capabilities, enabled: false }))
  descriptor.capabilities.navigation[1].available = false
  assert.equal(validateExplorationCapabilities(descriptor.capabilities).navigation[1].available, false)
})

test('independent result contract preserves public source and never becomes a private score', () => {
  const mapped = mapExplorationPage(page(), query)
  assert.equal(mapped.items[0].evidence.kind, 'selected_work')
  assert.equal(mapped.items[0].evidence.source.score, undefined)
  assert.equal(mapped.selectedSeed.title, '合成参照')
  assert.equal(mapExplorationPage(page({ count: 0 }), query).items.length, 0)
  assert.deepEqual(similarQuery(2, { type: 'OVA', minScore: 'lt6', hideRecorded: false, yearFrom: '2008', yearTo: '2012' }),
    { intent: 'similar', seed_bangumi_id: 2, type: 'OVA', min_score: '0.0', max_score: '6.0', max_score_exclusive: true,
      hide_recorded: false, page_size: 12, year_from: 2008, year_to: 2012 })
})

test('reject cross-seed/private evidence, wrong filters, duplicate IDs, unknown scopes and broken cursor counts', () => {
  for (const mutate of [data => data.query.seed_bangumi_id = 2, data => data.results[0].evidence.kind = 'rated_work',
    data => data.results[0].evidence.source.bangumi_id = 8, data => data.results[0].evidence.shared_tags = ['悬疑'],
    data => data.results[0].evidence.shared_tags = ['日常'], data => data.results[0].evidence.is_probability = true,
    data => data.results[0].evidence.source.score = 9, data => data.results[0].evidence_context.profile_version = 'private',
    data => data.snapshot_expires_at = Math.floor(Date.now() / 1000) - 1,
    data => data.results[0].evidence_context.query_fingerprint = 'f'.repeat(64), data => data.results[1] = data.results[0],
    data => data.next_page_cursor = null, data => data.batch_count = 12, data => data.page = 3,
    data => data.content_scope_context.scope_revision = 'f'.repeat(64), data => data.results[0].watch_status = 'plan',
    data => data.query.include_tags = ['音乐'], data => data.results[0].content_rating = 'adult']) {
    const data = JSON.parse(JSON.stringify(page())); mutate(data); assert.throws(() => mapExplorationPage(data, query))
  }
})

test('permanent adult scope uses separate envelope, neutral covers and validates strict detail', () => {
  const data = structuredClone(page()); data.content_scope_context = adultScope
  data.results[0].content_rating = 'adult'
  const mapped = mapExplorationPage(data, query, 'include_adult')
  assert.equal(mapped.items[0].coverUrl, '')
  assert.throws(() => mapExplorationPage(data, query))
  data.results[0].cover_url = 'https://media.xh-anime.com/covers/10.jpg'
  assert.throws(() => mapExplorationPage(data, query, 'include_adult'))
})

test('continuations use cursor only, append current batch, replace next batch and retain on return', async () => {
  const requests = []
  const first = page(), second = page({ part: 2 }), third = page({ batch: 2 })
  second.snapshot_expires_at = third.snapshot_expires_at = first.snapshot_expires_at
  second.snapshot_context = 'same-snapshot-resigned-in-another-second'
  const controller = createExplorationBrowseController({ explorationBrowse: async params => {
    requests.push(params); return params.cursor === 'page-two' ? second : params.cursor ? third : first
  }, explorationContext: async () => { throw new Error('offline') } })
  await controller.start({ key: 'first', query }); assert.equal(controller.getSnapshot().items.length, 12)
  await controller.loadMore(); assert.equal(controller.getSnapshot().items.length, 24)
  await controller.nextBatch(); assert.equal(controller.getSnapshot().items[0].bangumiId, 34)
  assert.deepEqual(requests.slice(1), [{ cursor: 'page-two' }, { cursor: 'batch-two' }])
  controller.dispose(); controller.activate(); await controller.resume({ key: 'first', query })
  assert.equal(controller.getSnapshot().items[0].bangumiId, 34); assert.equal(requests.length, 3)
})

test('seed/filter switches and disposed requests cannot publish a late response', async () => {
  const a = deferred(), b = deferred(); let calls = 0
  const controller = createExplorationBrowseController({ explorationBrowse: () => ++calls === 1 ? a.promise : b.promise })
  const first = controller.start({ key: 'old', query }); const second = controller.start({ key: 'new', query })
  b.resolve(page()); await second; a.resolve(page()); await first
  assert.equal(controller.getSnapshot().key, 'new')
  const pending = deferred(), closed = createExplorationBrowseController({ explorationBrowse: () => pending.promise })
  const task = closed.start({ key: 'closed', query }); closed.dispose(); pending.resolve(page()); await task
  assert.equal(closed.getSnapshot().status, 'loading')
})

test('changed/mixed pages discard evidence, keep position and never silently rerank', async () => {
  let calls = 0
  const next = structuredClone(page({ part: 2 })); next.snapshot_id = '2'.repeat(32)
  const controller = createExplorationBrowseController({ explorationBrowse: async () => ++calls === 1 ? page() : next })
  await controller.start({ key: 'one', query }); await controller.loadMore()
  assert.equal(controller.getSnapshot().snapshotDirty, true); assert.equal(controller.getSnapshot().items.length, 12)
  assert.equal(controller.getSnapshot().items[0].evidence, null)
  assert.equal(await controller.nextBatch(), false); assert.equal(calls, 2)
})

test('foreground 409 expires only exploration context while offline leaves a valid list intact', async () => {
  let failure = new Error('offline')
  const controller = createExplorationBrowseController({ explorationBrowse: async () => page(), explorationContext: async () => { throw failure } })
  await controller.start({ key: 'one', query }); await controller.verify()
  assert.equal(controller.getSnapshot().snapshotDirty, undefined)
  failure = { response: { status: 409, data: { code: 'exploration_snapshot_changed' } } }
  await controller.verify(); assert.equal(controller.getSnapshot().snapshotDirty, true)
})

test('exploration detail checks full seed/query/evidence and refreshes the whole item only after want ack', async () => {
  const mapped = mapExplorationPage(page(), query), pending = deferred(), requests = []
  const controller = createRecommendationDetailController({ explorationDetail: async (...args) => {
    requests.push(args); return requests.length === 1 ? detail() : pending.promise
  } }, explorationDetailAdapter)
  await controller.open(mapped.items[0]); assert.equal(controller.getSnapshot().status, 'ready')
  controller.openResources(); assert.equal(controller.getSnapshot().resourceOpen, true)
  const refreshed = controller.afterWant({ contract_version: 'recommendations.want.v1', content_rating: 'non_adult', bangumi_id: 10, record_id: 4, anime_id: 9, created: true, outcome: 'created', watch_status: 'plan' })
  assert.equal(controller.getSnapshot().status, 'refreshing'); assert.equal(controller.getSnapshot().item, undefined)
  assert.equal(requests[1][3], undefined)
  pending.resolve({ ...detail(), watch_status: 'plan', evidence_token: 'f'.repeat(64) }); await refreshed
  assert.equal(controller.getSnapshot().item.watchStatus, 'plan'); assert.equal(controller.getSnapshot().resourceOpen, false)
  const raw = structuredClone(detail()); raw.query.seed_bangumi_id = 2
  assert.throws(() => explorationDetailAdapter.map(raw, { explorationQuery: query, explorationScope: 'safe' }))
})

test('late detail/resource requests cannot reopen a closed or newer selection', async () => {
  const pending = deferred(), controller = createRecommendationDetailController({ explorationDetail: () => pending.promise }, explorationDetailAdapter)
  const task = controller.open(mapExplorationPage(page(), query).items[0]); controller.close()
  pending.resolve(detail()); await task; assert.deepEqual(controller.getSnapshot(), { status: 'closed' })
})
