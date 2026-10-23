import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { EXPLORATION_NAVIGATION, EXPLORATION_VERSIONS, explorationNavigationLabel,
  normalizeExplorationSearch, validateExplorationCapabilities, validateExplorationContract, validateExplorationCatalog,
  createExplorationSearchController } from '../src/utils/recommendationExploration.js'
import { mapRecommendationPage } from '../src/utils/recommendations.js'

const safeScope = { content_scope: 'safe', scope_revision: 'b'.repeat(64) }
const adultScope = { ...safeScope, content_scope: 'include_adult',
  content_scope_contract_version: 'recommendations.content-scope.n2-2.v1',
  content_scope_policy_version: 'content-scope.general-adult.v1',
  content_release_policy_version: 'release-review.general-adult.v2', eligibility_version: 0,
  content_review_version: 1, serving_control_version: 1, release_revision: 'a'.repeat(64), scope_valid_until: null }
const row = (changes = {}) => ({ bangumi_id: 12, title_zh: '选择参照', title_jp: '選択',
  year: 2026, season: '2026-01', type: 'TV', score: 8, tags: ['音乐'],
  can_seed: true, unavailable_reason: null, content_rating: 'non_adult', cover_url: '',
  feature_revision: 'c'.repeat(64), ...changes })
const result = (q = '选择', changes = {}) => ({ contract_version: EXPLORATION_VERSIONS.catalog,
  taxonomy_version: 'genres.v8', feature_rule_version: 'journal-genres.v3', identity_namespace: 'bangumi',
  query: normalizeExplorationSearch(q), limit: 15, has_more: false, results: [row()],
  content_scope_context: safeScope, ...changes })
const caps = (enabled = false) => ({ contract_version: EXPLORATION_VERSIONS.capabilities,
  enabled, catalog_search_enabled: enabled, query_execution_enabled: false,
  ...Object.fromEntries(['query', 'catalog', 'evidence', 'result'].map(key => [`${key}_contract_version`, EXPLORATION_VERSIONS[key]])),
  navigation: EXPLORATION_NAVIGATION.map(item => ({ ...item, available: item.id === 'personal' })) })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

test('approved navigation names and current marker are distinct from personal categories', () => {
  assert.equal(explorationNavigationLabel('personal', 'personal'), '个性化推荐 · 当前')
  assert.equal(explorationNavigationLabel('similar', 'personal'), '单番相似')
  assert.equal(explorationNavigationLabel('tags', 'tags'), '按标签找 · 当前')
  assert.throws(() => explorationNavigationLabel('all', 'personal'))
  assert.equal(validateExplorationCapabilities(caps()).enabled, false)
  assert.equal(validateExplorationCapabilities(caps(true)).catalog_search_enabled, true)
  for (const data of [{}, { ...caps(), query_execution_enabled: true },
    { ...caps(), catalog_contract_version: 'recommendations.3b.v1' }, { ...caps(), enabled: 'true' },
    { ...caps(), navigation: EXPLORATION_NAVIGATION.map(item => ({ ...item, available: true })) }]) {
    assert.throws(() => validateExplorationCapabilities(data))
  }
})

test('Python/JavaScript share the versioned descriptor and reject private profile evidence contracts', () => {
  const descriptor = JSON.parse(readFileSync(new URL('./fixtures/recommendationExplorationContract.json', import.meta.url)))
  assert.equal(validateExplorationContract(descriptor).capabilities.catalog_search_enabled, true)
  for (const mutate of [data => data.query.requires_profile = true,
    data => data.evidence.kinds.similar = 'rated_work', data => data.evidence.is_probability = true,
    data => data.evidence.contract_version = 'recommendations.evidence.v1']) {
    const data = structuredClone(descriptor); mutate(data)
    assert.throws(() => validateExplorationContract(data))
  }
})

test('search normalization accepts authoritative Bangumi links only and never guesses another ID namespace', () => {
  for (const q of ['123', '１２３', 'https://bgm.tv/subject/123/', 'http://bangumi.tv/subject/123', 'https://chii.in/subject/123']) {
    assert.deepEqual(normalizeExplorationSearch(q), { kind: 'bangumi_id', value: '123' })
  }
  assert.deepEqual(normalizeExplorationSearch(' 选择 '), { kind: 'title', value: '选择' })
  for (const q of ['', 'a', '0', '0012', '2147483648', true, 'x'.repeat(121), '选择\0作品',
    'https://anilist.co/anime/12', 'https://bgm.tv/subject/12?token=anything']) assert.throws(() => normalizeExplorationSearch(q))
})

test('catalog seed adapter accepts empty topics explicitly and strips arbitrary covers', () => {
  const mapped = validateExplorationCatalog(result('选择', { results: [row({ tags: [], can_seed: false,
    unavailable_reason: 'no_valid_topics', cover_url: 'https://untrusted.invalid/image.jpg' })] }), normalizeExplorationSearch('选择'))
  assert.equal(mapped.items[0].canSeed, false)
  assert.equal(mapped.items[0].coverUrl, '')
  assert.equal(mapped.items[0].featureRevision, 'c'.repeat(64))
  assert.equal(mapped.items[0].watchStatus, undefined)
  assert.throws(() => mapRecommendationPage(result())) // Selection is not a personal recommendation envelope.
})

test('wrong namespace, old query, malformed scope, duplicate IDs and seed state fail closed', () => {
  for (const data of [result('其他'), result('选择', { identity_namespace: 'anilist' }),
    result('选择', { content_scope_context: undefined }), result('选择', { results: [row(), row()] }),
    result('选择', { results: [row({ can_seed: false })] }), result('选择', { results: [row({ feature_revision: 'old' })] }),
    result('选择', { results: [row({ content_rating: 'adult' })] }), result('选择', { results: [row({ score: NaN })] }),
    result('选择', { has_more: true }), result('13')]) {
    assert.throws(() => validateExplorationCatalog(data, normalizeExplorationSearch(data.query.kind === 'bangumi_id' ? '13' : '选择')))
  }
})

test('general adult scope accepts explicit permanent review deadline but refuses cover or missing authorization', () => {
  const data = result('选择', { content_scope_context: adultScope, results: [row({ content_rating: 'adult' })] })
  assert.equal(validateExplorationCatalog(data, normalizeExplorationSearch('选择'), 'include_adult').items[0].contentRating, 'adult')
  assert.throws(() => validateExplorationCatalog({ ...data, content_scope_context: { ...adultScope, scope_valid_until: undefined } }, normalizeExplorationSearch('选择'), 'include_adult'))
  assert.throws(() => validateExplorationCatalog({ ...data, results: [row({ content_rating: 'adult', cover_url: 'https://untrusted.invalid/image.jpg' })] }, normalizeExplorationSearch('选择'), 'include_adult'))
})

test('exploration transport shares explicit scope checks and uses only GET', async () => {
  const calls = [], transport = { get: async (path, config) => {
    calls.push({ path, config }); return { data: { content_scope_context: adultScope } }
  } }
  const client = createRecommendationClient(transport).forScope('include_adult')
  await client.explorationCatalog('选择'); await client.explorationContract()
  assert.deepEqual(calls[0].config.params, { q: '选择', content_scope: 'include_adult' })
  assert.deepEqual(calls[1].config.params, { content_scope: 'include_adult' })
  let lost = 0
  const invalid = createRecommendationClient({ get: async () => ({ data: {} }) }).forScope('include_adult', () => lost++)
  await assert.rejects(invalid.explorationCatalog('选择')); assert.equal(lost, 1)
})

test('11B execution/continuation/detail use separate scoped routes and reject bad identity/context', async () => {
  const calls = [], transport = { get: async (path, config) => {
    calls.push({ path, config }); return { data: { content_scope_context: adultScope } }
  } }
  const client = createRecommendationClient(transport).forScope('include_adult')
  await client.explorationBrowse({ intent: 'similar', seed_bangumi_id: 12 })
  await client.explorationBrowse({ cursor: 'signed-cursor' })
  await client.explorationContext('signed-context')
  await client.explorationDetail(13, 'signed-detail', undefined, 'd'.repeat(64))
  assert.ok(calls.every(call => call.config.params.content_scope === 'include_adult'))
  assert.deepEqual(calls[1].config.params, { cursor: 'signed-cursor', content_scope: 'include_adult' })
  assert.equal(calls[3].path, 'recommendations/explore/13/')
  assert.equal(calls[3].config.params.evidence_token, 'd'.repeat(64))
  await assert.rejects(client.explorationDetail(2147483648, 'signed-detail'))
  await assert.rejects(client.explorationDetail(13, ''))
  await assert.rejects(client.explorationContext('x'.repeat(8193)))
  assert.equal(calls.length, 4)
})

test('11B snapshot changes preserve a valid adult selection; actual scope failures still notify', async () => {
  let lost = 0, data = { code: 'exploration_snapshot_changed' }
  const client = createRecommendationClient({ get: async () => { throw { response: { status: 409, data } } } })
    .forScope('include_adult', () => lost++)
  await assert.rejects(client.explorationBrowse({ cursor: 'old' }))
  await assert.rejects(client.explorationDetail(12, 'old'))
  assert.equal(lost, 0)
  data = { detail: '内容范围已变化' }
  await assert.rejects(client.explorationBrowse({ cursor: 'old' })); assert.equal(lost, 1)
})

test('late query responses cannot overwrite new results, even when transport ignores abort', async () => {
  const first = deferred(), second = deferred(), calls = []
  const controller = createExplorationSearchController({ explorationCatalog(q, signal) {
    calls.push({ q, signal }); return calls.length === 1 ? first.promise : second.promise
  } })
  const a = controller.search('第一次'), b = controller.search('第二次')
  assert.equal(calls[0].signal.aborted, true)
  second.resolve(result('第二次', { results: [row({ bangumi_id: 20 })] })); await b
  first.resolve(result('第一次')); await a
  assert.equal(controller.getSnapshot().items[0].bangumiId, 20)
  assert.equal(controller.getSnapshot().query.value, '第二次')
})

test('clear, dispose, blank and invalid inputs do not issue writes or resurrect prior results', async () => {
  const pending = deferred(); let calls = 0
  const controller = createExplorationSearchController({ explorationCatalog() { calls++; return pending.promise } })
  await controller.search(' '); assert.equal(calls, 0)
  await controller.search('0'); assert.equal(calls, 0)
  const request = controller.search('选择'); controller.clear()
  pending.resolve(result()); await request
  assert.equal(controller.getSnapshot().status, 'idle')
  controller.dispose(); await controller.search('选择'); assert.equal(calls, 1)
})

test('empty, disabled, authentication and network errors have distinct states', async () => {
  const empty = createExplorationSearchController({ explorationCatalog: async () => result('选择', { results: [] }) })
  await empty.search('选择'); assert.equal(empty.getSnapshot().status, 'empty')
  for (const [status, code, expected] of [[503, 'exploration_disabled', 'unavailable'], [503, '', 'error'], [401, '', 'error'],
    [409, '', 'error'], [429, '', 'error'], [0, '', 'error']]) {
    const controller = createExplorationSearchController({ explorationCatalog: async () => { throw { response: { status, data: { code } } } } })
    await controller.search('选择'); assert.equal(controller.getSnapshot().status, expected)
    assert.equal(controller.getSnapshot().items.length, 0)
  }
})
