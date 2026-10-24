import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { emptyTagDraft, tagDraftError, tagQuery, tagQueryPath, parseTagQuery } from '../src/utils/explorationTagQuery.js'
import { mapExplorationPage, explorationDetailAdapter, sameExplorationQuery } from '../src/utils/explorationResults.js'
import { createExplorationBrowseController } from '../src/utils/explorationSession.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { validateExplorationCapabilities } from '../src/utils/recommendationExploration.js'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { tagPage, tagDetail, deferred, scope, adultScope } from './fixtures/explorationContract.js'

const descriptor = JSON.parse(readFileSync(new URL('./fixtures/recommendationExplorationContract.json', import.meta.url)))
const tags = descriptor.available_tags
const draft = () => ({ ...emptyTagDraft(), include: ['音乐', '日常'], exclude: ['战斗'] })
const query = tagQuery(draft(), tags)

test('tag queries use canonical order, disjoint groups and strict public-only links', () => {
  assert.deepEqual(query.include_tags, ['日常', '音乐'])
  assert.equal(query.seed_bangumi_id, undefined)
  const path = tagQueryPath(query)
  assert.deepEqual(parseTagQuery(path.slice(path.indexOf('?')), tags).query, query)
  const filtered = { ...draft(), match: 'any', sort: 'score', filters: { type: 'OVA', minScore: 'lt6', yearFrom: '2008', yearTo: '2012', hideRecorded: false } }
  const q = tagQuery(filtered, tags), url = tagQueryPath(q)
  assert.deepEqual(parseTagQuery(url.slice(url.indexOf('?')), tags).query, q)
  for (const value of ['', '?scope=include_adult', '?include=音乐&token=private', '?include=音乐&cursor=private',
    '?include=私人标签', '?include=音乐&include=日常', '?include=音乐&exclude=音乐', '?include=音乐&match=ALL',
    '?include=音乐,,日常', '?include=音乐&minScore=NaN', '?include=音乐&type=garbage', '?include=音乐&hideRecorded=true', '?include=音乐&yearFrom=2012&yearTo=2008']) {
    const result = parseTagQuery(value, tags)
    assert.equal(result.query, null)
    if (value) assert.ok(result.error)
  }
  for (const changes of [{ include: [] }, { include: tags.slice(0, 6) }, { exclude: tags.slice(10, 16) }, { include: ['音乐', '音乐'] }, { include: ['私人标签'] }, { exclude: ['音乐'] }]) {
    assert.ok(tagDraftError({ ...draft(), ...changes }, tags))
  }
  assert.equal(validateExplorationCapabilities(descriptor.capabilities).navigation[2].available, true)
  assert.throws(() => validateExplorationCapabilities({ ...descriptor.capabilities, supported_intents: ['similar'] }))
})

test('transport serializes tag arrays as comma fields and omits empty exclusion; cursors stand alone', async () => {
  const calls = [], client = createRecommendationClient({ get: async (path, config) => { calls.push(config.params); return { data: {} } } })
  await client.explorationBrowse(query)
  await client.explorationBrowse({ ...query, exclude_tags: [] })
  await client.explorationBrowse({ cursor: 'signed', include_tags: ['ignored'] })
  assert.equal(calls[0].include_tags, '日常,音乐'); assert.equal(calls[0].exclude_tags, '战斗')
  assert.equal(calls[1].exclude_tags, undefined); assert.deepEqual(calls[2], { cursor: 'signed' })
})

test('tag result and detail contracts check actual hits, hard exclusions and disjoint evidence', () => {
  const raw = tagPage(query), mapped = mapExplorationPage(raw, structuredClone(query))
  assert.equal(mapped.selectedSeed, null); assert.equal(mapped.items[0].evidence.kind, 'selected_tags')
  assert.ok(sameExplorationQuery(mapped.query, structuredClone(query)))
  assert.ok(!sameExplorationQuery(query, { ...query, include_tags: ['音乐', '日常'] }))
  const selection = { explorationQuery: query, explorationScope: 'safe' }
  const full = tagDetail(query)
  assert.equal(explorationDetailAdapter.map(full, selection).evidence.source, null)
  full.watch_status = 'plan' // Confirmed own write can refresh the whole detail.
  assert.equal(explorationDetailAdapter.map(full, selection).watchStatus, 'plan')
  for (const mutate of [data => data.selected_seed = {}, data => data.query.seed_bangumi_id = 1,
    data => data.results[0].evidence.source = {}, data => data.results[0].evidence.kind = 'rated_work',
    data => data.results[0].tags.push('战斗'), data => data.results[0].tags = ['音乐'],
    data => data.results[0].evidence.shared_tags = ['音乐'], data => data.results[0].evidence.is_probability = true,
    data => data.evidence_context.ranking_policy_version = 'similarity.v1', data => data.results[0].watch_status = 'plan']) {
    const data = structuredClone(raw); mutate(data); assert.throws(() => mapExplorationPage(data, query))
  }
  const any = { ...query, match: 'any' }, partial = tagPage(any)
  partial.results[0].tags = ['音乐']; partial.results[0].evidence.shared_tags = ['音乐']
  assert.equal(mapExplorationPage(partial, any).items[0].evidence.shared_tags.length, 1)
  const adult = tagPage(query); adult.content_scope_context = adultScope; adult.results[0].content_rating = 'adult'
  assert.equal(mapExplorationPage(adult, query, 'include_adult').items[0].coverUrl, '')
  assert.throws(() => mapExplorationPage(adult, query))
})

test('tag browse restores batches with deep arrays, rejects changed context and cancels late mode responses', async () => {
  const first = tagPage(query), second = tagPage(query, { part: 2 }), third = tagPage(query, { batch: 2 })
  second.snapshot_expires_at = third.snapshot_expires_at = first.snapshot_expires_at
  const calls = [], client = { explorationBrowse: async params => { calls.push(params); return params.cursor === 'page-two' ? second : params.cursor ? third : first },
    explorationContext: async () => ({ contract_version: 'recommendations.explore.context.v1', intent: 'tags', valid: true,
      snapshot_id: first.snapshot_id, snapshot_expires_at: first.snapshot_expires_at, query_fingerprint: first.evidence_context.query_fingerprint,
      evidence_context: first.evidence_context, selected_seed: null, content_scope_context: scope }) }
  const controller = createExplorationBrowseController(client)
  await controller.start({ key: 'tags', query }); await controller.loadMore(); await controller.nextBatch()
  assert.equal(controller.getSnapshot().items[0].bangumiId, 34)
  controller.dispose(); controller.activate(); await controller.resume({ key: 'tags', query: structuredClone(query) })
  assert.equal(calls.length, 3); assert.equal(controller.getSnapshot().snapshotDirty, undefined)
  client.explorationContext = async () => ({})
  await controller.verify(); assert.equal(controller.getSnapshot().snapshotDirty, true)
  assert.equal(controller.getSnapshot().items[0].evidence, null)
  const pending = deferred(); client.explorationBrowse = () => pending.promise
  const request = controller.start({ key: 'new', query }); controller.dispose(); pending.resolve(first); await request
  assert.equal(controller.getSnapshot().status, 'loading')
})

test('tag detail discards the entire old item/resources on confirmed want and ignores a late reopen', async () => {
  const item = mapExplorationPage(tagPage(query), query).items[0], pending = deferred()
  const controller = createRecommendationDetailController({ explorationDetail: async (_, __, ___, expected) => expected ? tagDetail(query) : pending.promise }, explorationDetailAdapter)
  await controller.open(item); controller.openResources()
  assert.equal(controller.getSnapshot().resourceOpen, true)
  const result = { contract_version: 'recommendations.want.v1', content_rating: 'non_adult', bangumi_id: 10,
    record_id: 20, anime_id: 30, created: true, outcome: 'created', watch_status: 'plan' }
  const refreshed = controller.afterWant(result)
  assert.equal(controller.getSnapshot().status, 'refreshing')
  assert.equal(controller.getSnapshot().item, undefined); assert.equal(controller.getSnapshot().resourceOpen, false)
  const full = tagDetail(query); full.watch_status = 'plan'; full.evidence_token = 'f'.repeat(64)
  pending.resolve(full); await refreshed
  assert.equal(controller.getSnapshot().item.watchStatus, 'plan')
  assert.equal(controller.getSnapshot().item.evidenceToken, 'f'.repeat(64))
  controller.close(); controller.dispose()
  assert.equal(await controller.open(item), null)
})
