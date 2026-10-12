import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { createLatestRequestGate, mapRecommendationItem, mapRecommendationPage, mergeRecommendationPages, validateRecommendationPreferences, validateRecommendationProfile } from '../src/utils/recommendations.js'

const row = { bangumi_id: 123, content_rating: 'non_adult', title_zh: '标题', title_jp: 'Title', score: 8.5,
  tags: ['音乐'], cover_url: 'https://outside.test/a.jpg', reason: '实际理由', season: '2026-4', status_type: 'TV' }
const page = { contract_version: 'recommendations.3b.v1', mode: 'personalized', count: 1, page: 1, page_size: 12,
  state_token: 'a'.repeat(64), next_page: null, results: [row] }

test('real mapper fails closed, preserves Bangumi identity and never loads external covers', () => {
  assert.equal(mapRecommendationItem(row).id, '123')
  assert.equal(mapRecommendationItem(row).coverUrl, '')
  for (const change of [{ content_rating: 'adult' }, { content_rating: null }, { bangumi_id: '123' }, { score: NaN }, { tags: null }]) {
    assert.throws(() => mapRecommendationItem({ ...row, ...change }))
  }
  assert.equal(mapRecommendationPage(page).items.length, 1)
  for (const change of [{ contract_version: 'fake' }, { next_page: 99 }, { state_token: 'bad' }, { count: 121 }]) {
    assert.throws(() => mapRecommendationPage({ ...page, ...change }))
  }
})

test('profile and preferences require compatible real contracts instead of invented summaries', () => {
  const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, cold_start: false,
    tag_summary: [{ tag: '音乐', sample_count: 3 }], sample_policy: { minimum_score: '8.5' } }
  const preferences = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] }
  assert.equal(validateRecommendationProfile(profile), profile)
  assert.equal(validateRecommendationPreferences(preferences), preferences)
  for (const bad of [{}, { ...profile, tag_summary: null }, { ...profile, contract_version: 'fake' }]) {
    assert.throws(() => validateRecommendationProfile(bad))
  }
  assert.throws(() => validateRecommendationPreferences({ ...preferences, tags: ['unknown'] }))
})

test('late request resolving after cancellation cannot commit or replace newer results', async () => {
  const gate = createLatestRequestGate()
  let resolveOld
  const deferred = new Promise((resolve) => { resolveOld = resolve })
  const old = gate.begin()
  const commits = []
  const pending = deferred.then((value) => { if (old.isCurrent()) commits.push(value) })
  const next = gate.begin()
  assert.ok(old.signal.aborted)
  if (next.isCurrent()) commits.push('new')
  resolveOld('stale')
  await pending
  assert.deepEqual(commits, ['new'])
  gate.cancel()
  assert.equal(next.isCurrent(), false)
})

test('pagination de-duplicates without replacing earlier hero or records', () => {
  const hero = mapRecommendationItem(row)
  const results = mergeRecommendationPages([hero], [hero, { ...hero, id: '124' }])
  assert.equal(results[0], hero)
  assert.deepEqual(results.map((r) => r.id), ['123', '124'])
})

test('request layer forwards abort and snapshot on fixed paths, no journal write endpoint', async () => {
  const calls = []
  const transport = { get: async (...args) => { calls.push(args); return { data: page } },
    patch: async (...args) => { calls.push(args); return { data: {} } } }
  const client = createRecommendationClient(transport)
  const signal = new AbortController().signal
  await client.list({ category: 'explore', filters: { type: 'TV', minScore: '8', hideRecorded: true }, page: 2, stateToken: page.state_token }, signal)
  assert.equal(calls[0][0], 'recommendations/')
  assert.equal(calls[0][1].signal, signal)
  assert.equal(calls[0][1].params.state_token, page.state_token)
  assert.equal(calls[0][1].params.page, 2)
  await client.detail(123, signal)
  assert.equal(calls[1][0], 'recommendations/123/')
  await assert.rejects(client.detail('../../users/1', signal))
  await client.savePreferences({ tags: ['音乐'], expected_version: 2 }, signal)
  assert.equal(calls[2][0], 'recommendations/preferences/')
  assert.equal(Object.keys(client).some((k) => /want|journal|add/i.test(k)), false)
})
