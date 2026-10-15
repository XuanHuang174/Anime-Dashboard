// Exercise production request/snapshot/epoch functions together, without real users or networking.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createLatestRequestGate, mapRecommendationPage, recommendationResultState, mergeRecommendationPages } from '../src/utils/recommendations.js'
import { loadRecommendationSnapshot } from '../src/utils/recommendationSnapshot.js'
import { requireNextRecommendationEpoch, sameEvidenceContext } from '../src/utils/recommendationEpoch.js'
import { validateDetailEvidence } from '../src/utils/recommendationExplanation.js'
import { subscribeRecommendationReload, notifyRecommendationChange } from '../src/utils/recommendationReload.js'

const context = { algorithm_version: 'content.v1', profile_version: 'b'.repeat(64), preference_version: 0 }
const row = id => ({ content_rating: 'non_adult', bangumi_id: id, title_zh: `作品${id}`, title_jp: '',
  score: 7.5, tags: ['音乐'], evidence: null, reason: '', evidence_context: context, evidence_token: 'c'.repeat(64) })
const raw = (changes = {}) => ({ contract_version: 'recommendations.3b.v1', mode: 'personalized', count: 4,
  page: 1, page_size: 2, next_page: 2, state_token: 'a'.repeat(64), results: [row(1), row(2)],
  algorithm_version: 'content.v1', taxonomy_version: 'genres.v4', feature_version: 'genres-idf.v4',
  evidence_contract_version: 'recommendations.evidence.v1', evidence_context: context, ...changes })
const nextRaw = () => raw({ page: 2, next_page: null, results: [row(3), row(4)] })
const preferences = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] }
const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, cold_start: false,
  sample_policy: { minimum_score: '8.5' }, tag_summary: [{ tag: '音乐', sample_count: 3 }] }
const options = { category: 'all', filters: {}, signal: new AbortController().signal }
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const client = (list, p = profile) => ({ profile: async () => p, preferences: async () => preferences, list })

test('same-token responses cannot mix algorithm, evidence epoch, page size or sequence', () => {
  const current = mapRecommendationPage(raw()), next = mapRecommendationPage(nextRaw())
  requireNextRecommendationEpoch(current, next)
  assert.deepEqual(mergeRecommendationPages(current.items, next.items).map(item => item.id), ['1', '2', '3', '4'])
  for (const changed of [{ state_token: 'd'.repeat(64) }, { algorithm_version: 'content.v2' },
    { page: 3 }, { page_size: 12 }, { mode: 'cold_start' }, { count: 1 }, { feature_version: 'new' }, { taxonomy_version: 'new' },
    { evidence_contract_version: 'new' }, { evidence_context: { ...context, preference_version: 1 } },
    { evidence_context: undefined }]) {
    assert.throws(() => requireNextRecommendationEpoch(current, { ...next, ...changed }), error => error.snapshotChanged === true)
  }
  assert.ok(sameEvidenceContext(context, { preference_version: 0, profile_version: 'b'.repeat(64), algorithm_version: 'content.v1' }))
})

test('first page rejects per-card epochs and algorithm mismatch rather than displaying mixed evidence', () => {
  assert.throws(() => mapRecommendationPage(raw({ results: [{ ...row(1), evidence_context: { ...context, preference_version: 9 } }] })))
  assert.throws(() => mapRecommendationPage(raw({ results: [{ ...row(1), evidence_context: undefined }] })))
  assert.throws(() => mapRecommendationPage(raw({ algorithm_version: 'content.v2' })))
})

test('unapproved production switching is never requested; isolated V2/rollback labels invalidate old client pages', () => {
  const v1 = mapRecommendationPage(raw())
  const v2context = { ...context, algorithm_version: 'content.v2' }
  const v2 = mapRecommendationPage(nextRaw())
  v2.algorithm_version = 'content.v2'; v2.evidence_context = v2context
  assert.throws(() => requireNextRecommendationEpoch(v1, v2))
  assert.throws(() => requireNextRecommendationEpoch(v2, { ...v1, page: 3 }))
  assert.equal(mapRecommendationPage(raw()).algorithm_version, 'content.v1')
})

test('detail refuses changed context even if a faulty transport repeats the previous token', () => {
  const card = mapRecommendationPage(raw()).items[0]
  assert.equal(validateDetailEvidence(card, card.evidenceToken, context), card)
  assert.throws(() => validateDetailEvidence({ ...card, evidenceContext: { ...context, preference_version: 1 } },
    card.evidenceToken, context), /重新加载推荐/)
})

test('quick category switches discard late snapshots even if the transport ignores abort', async () => {
  const gate = createLatestRequestGate(), old = deferred(), committed = []
  const a = gate.begin()
  const oldRead = loadRecommendationSnapshot(client(() => old.promise), { ...options, signal: a.signal })
    .then(value => { if (a.isCurrent()) committed.push(value) })
  const b = gate.begin()
  const latest = await loadRecommendationSnapshot(client(async () => raw({ results: [row(8)] })),
    { ...options, category: 'explore', signal: b.signal })
  if (b.isCurrent()) committed.push(latest)
  old.resolve(raw()); await oldRead
  assert.ok(a.signal.aborted)
  assert.equal(committed.length, 1)
  assert.equal(committed[0].items[0].bangumiId, 8)
  gate.cancel()
})

test('a pending page and rapid detail clicks cannot commit after a newer generation', async () => {
  const gate = createLatestRequestGate(), pending = deferred(), seen = []
  const old = gate.begin()
  const read = pending.promise.then(value => { if (old.isCurrent()) seen.push(value) })
  const current = gate.begin()
  if (current.isCurrent()) seen.push('new-card-or-category')
  pending.resolve('old-page-or-detail'); await read
  assert.deepEqual(seen, ['new-card-or-category'])
  gate.cancel(); assert.equal(current.isCurrent(), false)
})

test('network failure is not cold start or mock fallback; explicit retry rereads all three endpoints', async () => {
  let reads = 0, failed = true
  const transport = client(async () => { reads++; if (failed) throw new Error('offline'); return raw() })
  await assert.rejects(loadRecommendationSnapshot(transport, options), /offline/)
  failed = false
  const loaded = await loadRecommendationSnapshot(transport, options)
  assert.equal(recommendationResultState(loaded.data), 'ready')
  assert.equal(reads, 2)
  const empty = await loadRecommendationSnapshot(client(async () => raw({ results: [], count: 0, next_page: null })), options)
  assert.equal(recommendationResultState(empty.data), 'empty')
  const coldProfile = { ...profile, eligible_sample_count: 0, cold_start: true, tag_summary: [] }
  const cold = await loadRecommendationSnapshot(client(async () => raw({ mode: 'cold_start', state_token: null,
    results: [], count: 0, next_page: null }), coldProfile), options)
  assert.equal(recommendationResultState(cold.data), 'cold')
})

test('journal changes and foreground return trigger fresh reads, coalesce and never post private data', async () => {
  const target = new EventTarget(), document = new EventTarget(), timers = new Map(), stored = []
  document.visibilityState = 'visible'
  let timerId = 0, revision = 0, reads = 0, lastRead
  target.setTimeout = fn => { timers.set(++timerId, fn); return timerId }
  target.clearTimeout = id => timers.delete(id)
  target.localStorage = { setItem: (key, value) => stored.push([key, value]) }
  const transport = client(async () => { reads++; return raw({ state_token: (revision ? 'd' : 'a').repeat(64) }) })
  const subscription = subscribeRecommendationReload({ target, document,
    onReload: () => { lastRead = loadRecommendationSnapshot(transport, options) } })
  revision = 1 // The server fixture changed, not the user's actual journal.
  notifyRecommendationChange({}, target)
  target.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange'))
  for (const [id, fn] of [...timers]) { timers.delete(id); fn() }
  const refreshed = await lastRead
  assert.equal(reads, 1); assert.equal(refreshed.data.state_token, 'd'.repeat(64))
  assert.ok(stored.every(([, value]) => /^\d+-0\.\d+$/.test(value)))
  subscription.dispose()
})
