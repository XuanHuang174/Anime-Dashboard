import test from 'node:test'
import assert from 'node:assert/strict'
import { CREDIT_DEFAULTS, validateCreditsCapabilities, validateCreditsSearch, validateCreditsSummary, validateCreditsWorks,
  validateCreditsRecord, validateCreditConditions, creditsRoute, creditsBackground, creditsNavigationState, creditItem } from '../src/utils/recommendationCredits.js'
import { createCreditsWorkspace } from '../src/utils/creditsWorkspace.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { creditsPage, creditsSearch, creditsSummary, creditsCapability, creditsCommon } from './fixtures/creditsContract.js'
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const conflict = () => ({ response: { status: 409, data: { code: 'credits_snapshot_changed' } } })
const api = overrides => ({ creditsWorks: async () => creditsPage(), creditsSearch: async kind => creditsSearch(kind), ...overrides })
const workspace = (client = api(), extra = {}) => createCreditsWorkspace({ client, kind: 'voice_actor', person: 101, ...extra })

test('credits contracts distinguish namespace, role, source coverage, duplicates and current scope', () => {
  assert.equal(validateCreditsCapabilities(creditsCapability), creditsCapability)
  assert.throws(() => validateCreditsCapabilities({ ...creditsCapability, studio: 'true' }))
  assert.equal(validateCreditsSummary(creditsSummary(), 1, 'safe').sections.voice_actor.has_more, true)
  assert.equal(validateCreditsSummary(creditsSummary(1, true), 1, 'safe').sections.voice_actor.count, 61)
  for (const change of [data => data.sections.voice_actor.results[0].evidence.position_or_cast_type = 5,
    data => data.sections.voice_actor.results[0].evidence.subject_id = 2,
    data => data.sections.voice_actor.count = 50, data => data.sections.voice_actor.results[1] = data.sections.voice_actor.results[0],
    data => data.sections.studio.status = '__proto__']) {
    const data = creditsSummary(); change(data); assert.throws(() => validateCreditsSummary(data, 1, 'safe'))
  }
  const search = creditsSearch(); search.results.push({ ...search.results[0], person_id: 102 })
  assert.equal(validateCreditsSearch(search, 'voice_actor', 'safe').results.length, 2)
  assert.throws(() => validateCreditsSearch(search, 'studio', 'safe'))
})
test('works validate bounds, adult covers, score semantics, exact condition and evidence identities', () => {
  assert.equal(validateCreditsWorks(creditsPage(), 'voice_actor', 101, CREDIT_DEFAULTS, 'safe').results.length, 12)
  for (const change of [d => d.count = 100, d => d.expires_at = 1, d => d.results[0].evidence.person_id = 999,
    d => d.results[0].content_rating = 'adult', d => d.conditions.hide_recorded = false,
    d => d.results[1] = d.results[0], d => d.results[0].watch_status = 'unseen']) {
    const data = creditsPage(); change(data); assert.throws(() => validateCreditsWorks(data, 'voice_actor', 101, CREDIT_DEFAULTS, 'safe'))
  }
  const adult = creditsPage({ contentScope: 'include_adult', offset: 24, count: 26 })
  assert.equal(validateCreditsWorks(adult, 'voice_actor', 101, CREDIT_DEFAULTS, 'include_adult').results[1].content_rating, 'adult')
  adult.results[1].cover_url = 'https://example.org/adult.jpg'
  assert.throws(() => validateCreditsWorks(adult, 'voice_actor', 101, CREDIT_DEFAULTS, 'include_adult'))
  assert.equal(creditItem(adult.results[1]).coverUrl, '')
  assert.throws(() => validateCreditConditions({ ...CREDIT_DEFAULTS, view: 'completed' }))
  assert.throws(() => validateCreditConditions({ ...CREDIT_DEFAULTS, year_from: 2027, year_to: 2026 }))
})
test('record references only accept own confirmed identities and explicit unresolved states', () => {
  const data = { ...creditsCommon('record'), record_id: 9, identity_state: 'linked', bangumi_id: 1 }
  assert.equal(validateCreditsRecord(data, 9, 'safe'), data)
  assert.throws(() => validateCreditsRecord(data, 10, 'safe'))
  assert.throws(() => validateCreditsRecord({ ...data, identity_state: 'stale_link' }, 9, 'safe'))
})
test('routes preserve approved background and scope without following external locations', () => {
  assert.deepEqual(creditsRoute('/recommendations/credits/studio/201'), { kind: 'studio', person: 201 })
  for (const path of ['/recommendations/credits/person/1', '/recommendations/credits/studio/0', '/recommendations/credits/studio/2147483648']) assert.equal(creditsRoute(path), null)
  const original = { pathname: '/recommendations/tags', search: '?include=音乐' }
  const next = { pathname: '/recommendations/credits', state: { creditsBackground: original } }
  assert.equal(creditsNavigationState(next, 'include_adult').creditsBackground, original)
  assert.equal(creditsNavigationState(next, 'safe').creditsFrom, '/recommendations/credits')
  assert.equal(creditsBackground({ pathname: 'https://example.com/dashboard' }), null)
})
test('adapter adds adult scope, guards IDs and distinguishes source revisions from scope revocation', async () => {
  const calls = []; let lost = 0
  const transport = { get: async (path, config) => { calls.push([path, config]); return { data: creditsSearch('studio', 'include_adult') } } }
  const client = createRecommendationClient(transport, 'include_adult', () => lost++)
  await client.creditsSearch('studio', '星河')
  assert.equal(calls[0][1].params.content_scope, 'include_adult')
  assert.throws(() => client.creditsWorks('__proto__', 1, {}))
  assert.throws(() => client.creditsSummary(0, false))
  transport.get = async () => { throw conflict() }
  await assert.rejects(client.creditsWorks('voice_actor', 101, CREDIT_DEFAULTS)); assert.equal(lost, 0)
  transport.get = async () => { throw { response: { status: 503, data: { code: 'credits_disabled' } } } }
  await assert.rejects(client.creditsWorks('voice_actor', 101, CREDIT_DEFAULTS)); assert.equal(lost, 0)
  transport.get = async () => { throw { response: { status: 403 } } }
  await assert.rejects(client.creditsWorks('voice_actor', 101, CREDIT_DEFAULTS)); assert.equal(lost, 1)
})
test('stable paging merges once and rejects duplicate or mismatched versions', async () => {
  const expiry = Math.floor(Date.now() / 1000) + 900
  let moreCalls = 0
  const client = api({ creditsWorks: async (_, __, query) => {
    const offset = query.cursor ? Number(query.cursor.split(':')[1]) : 0
    if (offset) moreCalls++
    return creditsPage({ offset, expires: expiry })
  } })
  const controller = workspace(client)
  await controller.load(); await Promise.all([controller.more(), controller.more()]); await controller.more()
  assert.equal(controller.getSnapshot().data.results.length, 25)
  assert.equal(moreCalls, 2)
  const bad = workspace(api({ creditsWorks: async () => creditsPage({ expires: expiry }) }))
  await bad.load(); await bad.more()
  assert.equal(bad.getSnapshot().stale, true)
  assert.equal(bad.getSnapshot().data.results.length, 12)
})
test('late result, closed route and account change cannot refill visible state', async () => {
  const a = deferred(), b = deferred(); let calls = 0, current = true
  const controller = workspace(api({ creditsWorks: () => ++calls === 1 ? a.promise : b.promise }), { isCurrent: () => current })
  const first = controller.load(), second = controller.load()
  b.resolve(creditsPage()); await second
  a.resolve(creditsPage({ count: 0 })); await first
  assert.equal(controller.getSnapshot().data.count, 25)
  const pending = deferred(), other = workspace(api({ creditsWorks: () => pending.promise }), { isCurrent: () => current })
  const read = other.load(); current = false; pending.resolve(creditsPage()); await read
  assert.equal(other.getSnapshot().data, null)
  other.dispose(); controller.dispose()
})
test('React-owned return cache retains pages without a new list read and disposal is reversible', async () => {
  let calls = 0
  const cache = {}, client = api({ creditsWorks: async () => { calls++; return creditsPage() } })
  const controller = workspace(client, { cache }); await controller.load(); controller.dispose()
  const returned = workspace(client, { cache }); await returned.ensure(CREDIT_DEFAULTS)
  assert.equal(calls, 1)
  returned.dispose(); returned.activate(); await returned.ensure({ ...CREDIT_DEFAULTS, hide_recorded: false })
  assert.equal(calls, 2) // incompatible response is deliberately rejected
  assert.equal(returned.getSnapshot().stale, true)
})
test('expired snapshots and journal changes block pagination without polling relations', async () => {
  let clock = Date.now(), calls = 0
  const controller = workspace(api({ creditsWorks: async () => { calls++; return creditsPage() } }), { now: () => clock })
  await controller.load(); clock += 901000; await controller.more()
  assert.equal(calls, 1); assert.equal(controller.getSnapshot().stale, true)
  assert.equal(controller.getSnapshot().data.results.length, 12)
  controller.invalidate(false, '手账变化'); assert.equal(controller.getSnapshot().message, '手账变化')
})
test('network failures retain cards; loss of permission or module closure clears them', async () => {
  const client = api(), controller = workspace(client)
  await controller.load()
  client.creditsWorks = async () => { throw Error('timeout') }
  await controller.load(); assert.equal(controller.getSnapshot().data.results.length, 12)
  client.creditsWorks = async () => { throw conflict() }
  await controller.load(); assert.equal(controller.getSnapshot().stale, true)
  client.creditsWorks = async () => { throw { response: { status: 503, data: { code: 'credits_disabled' } } } }
  await controller.load(); assert.equal(controller.getSnapshot().data, null)
})
