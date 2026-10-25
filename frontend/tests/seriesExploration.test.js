import test from 'node:test'
import assert from 'node:assert/strict'
import { mapExplorationPage, requireExplorationTransition, explorationDetailAdapter } from '../src/utils/explorationResults.js'
import { createExplorationBrowseController } from '../src/utils/explorationSession.js'
import { validateRelated, validateAnnotations, relationText } from '../src/utils/seriesExploration.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { seriesExplorePage, seriesExploreDetail, annotations, related } from './fixtures/seriesExplorationContract.js'
import { page, query, scope, adultScope } from './fixtures/explorationContract.js'

test('12D uses an explicit independent result contract, never silently mixes legacy ranking', () => {
  const data = seriesExplorePage(), mapped = mapExplorationPage(data, query, 'safe', true)
  assert.equal(mapped.series_context.items.length, 2)
  assert.equal(mapped.items[0].seriesContext.policy_version, 'series-explore.direct.v1')
  assert.throws(() => mapExplorationPage(data, query, 'safe', false))
  assert.throws(() => mapExplorationPage(page(), query, 'safe', true))
  assert.equal(mapExplorationPage(page(), query, 'safe', false).items.length, 12)
  for (const patch of [{ policy_version: 'guess_titles' }, { seed_bangumi_id: 99 }, { direct_only: false }, { filtered_count: 13 }, { fingerprint: '' }]) {
    assert.throws(() => validateRelated({ ...related(), ...patch }, query))
  }
})

test('related block rejects overlap, weak graph merges, owned rows under hiding and wrong directions', () => {
  const data = seriesExplorePage()
  data.series_context.items[0].bangumi_id = data.results[0].bangumi_id
  Object.assign(data.series_context.items[0].facts[0], { target_id: data.results[0].bangumi_id })
  assert.throws(() => mapExplorationPage(data, query, 'safe', true))
  for (const change of [row => { row.watch_status = 'completed' }, row => { row.facts[0].source_id = 44 },
    row => Object.assign(row.facts[0], { kind: 'same_setting', relation_label: '相同世界观' })]) {
    const data = related(); change(data.items[0]); assert.throws(() => validateRelated(data, query))
  }
  const reverse = related(); Object.assign(reverse.items[0].facts[0], { source_id: 2, target_id: 1, kind: 'prequel', relation_label: '前传' })
  assert.equal(validateRelated(reverse, query), reverse)
  assert.match(relationText(reverse.items[0].facts[0], 2), /是本作的前传/)
})

test('relations share fixed batch boundaries but not the 120 genre-result count', () => {
  const first = mapExplorationPage(seriesExplorePage(), query, 'safe', true)
  const next = mapExplorationPage(seriesExplorePage({ part: 2 }), query, 'safe', true)
  assert.doesNotThrow(() => requireExplorationTransition(first, next, 'more', new Set(first.items.map(item => item.bangumiId))))
  next.series_context = { ...next.series_context, fingerprint: 'a'.repeat(64) }
  assert.throws(() => requireExplorationTransition(first, next, 'more', new Set()))
})

test('detail context retains series separation through strict and refreshed detail mapping', () => {
  const first = mapExplorationPage(seriesExplorePage(), query, 'safe', true), selected = first.items[0]
  const selection = { ...selected, ...explorationDetailAdapter.select(selected) }
  const raw = seriesExploreDetail(selected.bangumiId)
  const mapped = explorationDetailAdapter.map(raw, selection, { context: selected.evidenceContext, token: selected.evidenceToken })
  assert.equal(mapped.bangumiId, selected.bangumiId)
  assert.throws(() => explorationDetailAdapter.map({ ...raw, series_context: undefined }, selection))
  assert.throws(() => explorationDetailAdapter.map({ ...raw, series_context: { ...raw.series_context, fingerprint: 'a'.repeat(64) } }, selection))
})

test('controller negotiates partition only for similar; context change blocks old related actions', async () => {
  const calls = [], data = seriesExplorePage(), client = { seriesExploration: true,
    explorationBrowse: async (q, signal, series) => { calls.push(series); return q.cursor ? seriesExplorePage({ part: 2 }) : data },
    explorationContext: async (token, signal, series) => { calls.push(series); return { contract_version: 'recommendations.explore.context.v1', valid: true,
      snapshot_id: data.snapshot_id, snapshot_expires_at: data.snapshot_expires_at, query_fingerprint: data.evidence_context.query_fingerprint,
      intent: 'similar', selected_seed: { bangumi_id: 1 }, evidence_context: data.evidence_context, series_context: { ...data.series_context, fingerprint: 'a'.repeat(64) } } } }
  const controller = createExplorationBrowseController(client)
  await controller.start({ key: 'one', query }); await controller.loadMore(); await controller.verify()
  assert.deepEqual(calls, [true, true, true]); assert.equal(controller.getSnapshot().snapshotDirty, true)
  assert.equal(controller.getSnapshot().items[0].evidence, null)
  assert.equal(await controller.nextBatch(), false)
})

test('annotation batches accept only requested scoped identities and exact directed relation vocabulary', () => {
  assert.equal(validateAnnotations(annotations(), [10], 'safe').get(10).facts[0].relation_label, '续集')
  assert.throws(() => validateAnnotations(annotations([11]), [10], 'safe'))
  const malformed = annotations(); malformed.results[0].facts[0].kind = 'inferred_family'
  assert.throws(() => validateAnnotations(malformed, [10], 'safe'))
  const adult = { ...annotations(), content_scope_context: adultScope }
  assert.throws(() => validateAnnotations(adult, [10], 'safe'))
  assert.equal(validateAnnotations(adult, [10], 'include_adult').size, 1)
  assert.throws(() => validateAnnotations(adult, [10], 'include_adult', 2), error => error.seriesChanged === true)
  assert.throws(() => validateAnnotations({ ...adult, content_scope_context: { ...adultScope, scope_valid_until: '2000-01-01' } }, [10], 'include_adult'))
})

test('client retains NSFW range through negotiated browse, cursors, detail and metadata requests', async () => {
  const calls = [], transport = { get: async (path, config) => { calls.push([path, config.params]); return { data: { content_scope_context: adultScope } } } }
  const client = createRecommendationClient(transport).forSeries(true).forScope('include_adult')
  assert.equal(client.seriesExploration, true)
  await client.explorationBrowse(query, undefined, true)
  await client.explorationBrowse({ cursor: 'cursor' }, undefined, true)
  await client.explorationContext('context', undefined, true)
  await client.explorationDetail(10, 'context', undefined, undefined, true)
  await client.seriesAnnotations([10, 11])
  assert.deepEqual(calls.map(row => row[0]), ['recommendations/explore/series/', 'recommendations/explore/series/',
    'recommendations/explore/series/context/', 'recommendations/explore/series/10/', 'recommendations/series/annotations/'])
  assert.ok(calls.every(row => row[1].content_scope === 'include_adult'))
  assert.equal(calls.at(-1)[1].ids, '10,11')
  let lost = false
  const guarded = createRecommendationClient(transport, 'include_adult', () => { lost = true })
  transport.get = async () => { throw { response: { status: 409, data: { code: 'series_snapshot_changed' } } } }
  await assert.rejects(guarded.seriesAnnotations([10])); assert.equal(lost, false)
})
