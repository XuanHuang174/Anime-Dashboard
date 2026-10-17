import test from 'node:test'
import assert from 'node:assert/strict'
import { browsePage } from './fixtures/browseContract.js'
import { createBrowseController } from '../src/utils/recommendationBrowseSession.js'
import { mapBrowsePage, DEFAULT_BROWSE_FILTERS } from '../src/utils/recommendationBrowse.js'
import { mapRecommendationItem } from '../src/utils/recommendations.js'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

const recipe = { contract_version: 'recommendations.execution.v1', algorithm_version: 'content.v2',
  ranking_policy_version: 'balanced-rerank.v3', recall_policy_version: 'source-pattern-recall.v2',
  runtime_version: 'pattern-prefix-runtime.v1', control_policy_version: 'browse-controls.v2',
  candidate_limit: 400, result_limit: 120, batch_size: 24, default_page_size: 12,
  recipe_revision: 'a'.repeat(64), control_policy_sha256: 'b'.repeat(64), serving_enabled: false }
const context = { algorithm_version: 'content.v2', profile_version: 'a'.repeat(64), preference_version: 0,
  feedback_version: 0, ranking_policy_version: recipe.ranking_policy_version, recall_policy_version: recipe.recall_policy_version,
  runtime_version: recipe.runtime_version, control_policy_version: recipe.control_policy_version,
  recipe_revision: recipe.recipe_revision, browse_context_version: 'c'.repeat(64), catalog_feature_version: 'f'.repeat(64) }
const preparedPage = options => {
  const page = browsePage({ context, ...options })
  return { ...page, algorithm_version: 'content.v2', browse_contract_version: 'recommendations.browse.v2',
    control_policy_version: recipe.control_policy_version, execution_recipe: { ...recipe },
    results: page.results.map(row => ({ ...row, detail_context: `ticket-${row.bangumi_id}` })) }
}
const selection = { key: 'test', category: 'all', style: 'balanced', filters: { ...DEFAULT_BROWSE_FILTERS } }
const capability = { contract_version: 'recommendations.capabilities.v1', algorithm_version: 'content.v2',
  browse_enabled: true, rollout_revision: 'a'.repeat(64), browse_contract_version: 'recommendations.browse.v2', execution_recipe: recipe }
const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, cold_start: false,
  sample_policy: { minimum_score: '8.5' }, tag_summary: [] }
const prefs = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] }
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function service() {
  const client = { capabilities: async () => capability, profile: async () => profile, preferences: async () => prefs,
    browse: async ({ cursor }) => preparedPage({ offset: cursor ? Number(cursor.split('-').at(-1)) : 0 }),
    list: () => assert.fail('No silent legacy/mock fallback') }
  return { client, controller: createBrowseController(client) }
}

test('D2 recipe/context/ticket are mandatory as a unit; V1 cannot carry V2 metadata', () => {
  const raw = preparedPage(); assert.equal(mapBrowsePage(raw).items[0].detailContext, 'ticket-101')
  for (const changes of [{ execution_recipe: undefined }, { control_policy_version: 'browse-controls.v1' },
    { execution_recipe: { ...recipe, runtime_version: 'unknown' } }, { execution_recipe: { ...recipe, recipe_revision: 'wrong' } },
    { evidence_context: { ...context, recipe_revision: 'd'.repeat(64) } },
    { results: raw.results.map(row => ({ ...row, detail_context: undefined })) },
    { results: raw.results.map(row => ({ ...row, evidence_context: { ...context, browse_context_version: 'bad' } })) }]) {
    assert.throws(() => mapBrowsePage({ ...raw, ...changes }))
  }
  assert.throws(() => mapBrowsePage({ ...browsePage(), execution_recipe: recipe }))
})

test('D2 only server capabilities authorize recipe; no query chooses V2', async () => {
  const env = service(); await env.controller.start(selection)
  assert.equal(env.controller.getSnapshot().status, 'ready')
  await env.controller.loadMore(); assert.equal(env.controller.getSnapshot().items.length, 24)
  await env.controller.nextBatch(); assert.equal(env.controller.getSnapshot().data.batch, 2)
  env.client.capabilities = async () => ({ ...capability, execution_recipe: { ...recipe, recipe_revision: 'd'.repeat(64) } })
  await env.controller.retry(); assert.equal(env.controller.getSnapshot().status, 'error')
  const calls = [], api = createRecommendationClient({ get: async (path, config) => { calls.push(config); return { data: {} } } })
  await api.browse(selection)
  assert.equal(calls[0].params.algorithm_version, undefined)
  await api.detail(101, undefined, 'a'.repeat(64), 'ticket-101')
  assert.deepEqual(calls[1].params, { evidence_token: 'a'.repeat(64), detail_context: 'ticket-101' })
  await assert.rejects(api.detail(101, undefined, undefined, '../bad'))
})

test('D2 changed recipe/rollback on continuation discards evidence and requires a fresh first page', async () => {
  for (const response of [() => ({ ...preparedPage({ offset: 12 }), execution_recipe: { ...recipe, recipe_revision: 'e'.repeat(64) } }),
    () => browsePage({ offset: 12 }), () => { throw { response: { status: 400 } } },
    () => { throw { response: { status: 409 } } }]) {
    const env = service(); await env.controller.start(selection)
    env.client.browse = async () => response()
    await env.controller.loadMore()
    assert.equal(env.controller.getSnapshot().snapshotExpired, true)
    assert.ok(env.controller.getSnapshot().items.every(item => !item.evidenceToken && !item.detailContext))
    env.client.capabilities = async () => ({ contract_version: 'recommendations.capabilities.v1', algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) })
    env.client.browse = async () => browsePage()
    await env.controller.retry()
    assert.equal(env.controller.getSnapshot().data.algorithm_version, 'content.v1')
    assert.equal(env.controller.getSnapshot().data.batch, 1)
    assert.ok(env.controller.getSnapshot().items.every(item => item.detailContext === undefined))
  }
})

test('D2 late V2 continuation cannot pollute a new V1 session', async () => {
  const env = service(); await env.controller.start(selection)
  const pending = deferred(); let signal
  env.client.browse = (options, current) => { signal = current; return pending.promise }
  const old = env.controller.loadMore()
  env.client.capabilities = async () => ({ contract_version: 'recommendations.capabilities.v1', algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) })
  env.client.browse = async () => browsePage()
  await env.controller.retry(); assert.ok(signal.aborted)
  pending.resolve(preparedPage({ offset: 12 })); await old
  assert.equal(env.controller.getSnapshot().data.algorithm_version, 'content.v1')
  assert.equal(env.controller.getSnapshot().items.length, 12)
})

test('D2 detail retains signed scope, removes whole old evidence after want, and never falls back to V1', async () => {
  const row = preparedPage().results[0], calls = [], refresh = deferred()
  const controller = createRecommendationDetailController({ detail: async (id, signal, token, ticket) => {
    calls.push({ id, token, ticket }); return calls.length === 1 ? row : refresh.promise
  } })
  await controller.open(mapRecommendationItem(row)); controller.openResources()
  const updating = controller.afterWant({ contract_version: 'recommendations.want.v1', bangumi_id: 101,
    anime_id: 9, record_id: 7, content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' })
  assert.equal(controller.getSnapshot().item, undefined); assert.equal(controller.getSnapshot().resourceOpen, false)
  assert.equal(calls[1].token, undefined); assert.equal(calls[1].ticket, row.detail_context)
  refresh.resolve({ ...row, detail_context: 'new-ticket-101', evidence_token: 'e'.repeat(64),
    evidence_context: { ...context, profile_version: 'd'.repeat(64), browse_context_version: 'e'.repeat(64) }, watch_status: 'plan' })
  await updating
  assert.equal(controller.getSnapshot().item.detailContext, 'new-ticket-101')
  assert.equal(controller.getSnapshot().item.watchStatus, 'plan')
  const other = createRecommendationDetailController({ detail: async () => browsePage().results[0] })
  await other.open(mapRecommendationItem(row))
  assert.equal(other.getSnapshot().status, 'error'); assert.equal(other.getSnapshot().snapshotChanged, true)
})
