import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreferenceEditor } from '../src/utils/recommendationPreferences.js'
import { profileScore, samplingPreferences, validateProfilePreview } from '../src/utils/recommendationSampling.js'
import { validateRecommendationPreferences, validateRecommendationProfile, validateRecommendationSnapshot, mapRecommendationPage } from '../src/utils/recommendations.js'
import { recommendationEvidence } from '../src/utils/recommendationExplanation.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { loadRecommendationSnapshot } from '../src/utils/recommendationSnapshot.js'
import { createBrowseController } from '../src/utils/recommendationBrowseSession.js'
import { preferences, previewData, profileData, pageData, itemData, choices } from './fixtures/profileSampling.js'

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { for (let n = 0; n < 10; n++) await Promise.resolve() }
function timers() {
  let id = 0; const tasks = new Map()
  return { setTimeout(fn) { tasks.set(++id, fn); return id }, clearTimeout(key) { tasks.delete(key) },
    run() { for (const [key, fn] of [...tasks]) { tasks.delete(key); fn() } }, get size() { return tasks.size } }
}

test('P2 exact nine values, legacy compatibility and partial sampling metadata fail closed', () => {
  for (const score of choices) assert.equal(profileScore(score), score)
  for (const score of [null, false, true, '', ' ', [], {}, NaN, Infinity, '7.3', '5.5', '10.5']) assert.equal(profileScore(score), null)
  const legacy = { contract_version: 'recommendations.3a.v1', tags: [], version: 1, available_tags: [] }
  assert.equal(samplingPreferences(legacy), null)
  assert.equal(validateRecommendationPreferences(legacy), legacy)
  for (const changes of [{ profile_min_score: '7.3' }, { effective_profile_min_score: '9.0' },
    { profile_policy_version: 'unknown' }, { profile_min_score_control: undefined },
    { profile_min_score_control: { ...preferences().profile_min_score_control, step: '1.0' } }]) {
    assert.throws(() => validateRecommendationPreferences({ ...preferences(), ...changes }))
  }
})

test('P2 preview client sends only score and AbortSignal via GET', async () => {
  const calls = [], signal = new AbortController().signal
  const client = createRecommendationClient({ get: async (...args) => { calls.push(args); return { data: previewData('7.0') } } })
  await client.previewProfile('7.0', signal)
  assert.deepEqual(calls, [['recommendations/profile/preview/', { params: { profile_min_score: '7.0' }, signal }]])
})

test('P2 score changes are debounced drafts; repeated value, cancel and clear tags never write', async () => {
  const time = timers(); let reads = 0, writes = 0
  const editor = createPreferenceEditor({ previewProfile: async score => { reads++; return previewData(score) },
    savePreferences() { writes++ } }, () => {}, time)
  const original = preferences()
  editor.open(original)
  assert.equal(reads, 0)
  editor.setMinScore('7.0'); editor.setMinScore('6.0'); editor.setMinScore('6.0')
  assert.equal(time.size, 1); assert.equal(reads, 0)
  assert.equal(editor.getSnapshot().preview.status, 'debouncing')
  time.run(); await flush()
  assert.equal(reads, 1); assert.equal(editor.getSnapshot().preview.data.profile_min_score, '6.0')
  editor.clearDraft(); assert.equal(editor.getSnapshot().draftMinScore, '6.0')
  assert.deepEqual(original.tags, ['日常']); assert.equal(original.profile_min_score, '8.5')
  editor.close(); assert.equal(writes, 0)
})

test('P2 reset is a bound-safe draft action and cancels preview without saving', async () => {
  const time = timers(), pending = deferred(); let signal
  const editor = createPreferenceEditor({ previewProfile: (_, value) => { signal = value; return pending.promise } }, () => {}, time)
  editor.open(preferences()); editor.setMinScore('7.0'); time.run(); await flush()
  const reset = editor.resetMinScore; reset()
  assert.equal(signal.aborted, true); assert.equal(editor.getSnapshot().draftMinScore, '8.5')
  pending.resolve(previewData('7.0')); await flush()
  assert.equal(editor.getSnapshot().preview.status, 'idle')
})

test('P2 late previews, errors and finally cannot cross scores, close, reopen or StrictMode', async () => {
  const time = timers(), calls = []
  const editor = createPreferenceEditor({ previewProfile: (score, signal) => {
    const task = deferred(); calls.push({ score, signal, ...task }); return task.promise
  } }, () => {}, time)
  editor.open(preferences()); editor.setMinScore('7.0'); time.run(); await flush()
  editor.setMinScore('6.0'); time.run(); await flush()
  assert.equal(calls[0].signal.aborted, true)
  calls[1].resolve(previewData('6.0')); await flush()
  calls[0].reject(new Error('late')); await flush()
  assert.equal(editor.getSnapshot().preview.data.profile_min_score, '6.0')
  editor.setMinScore('7.5'); time.run(); await flush()
  editor.close(); editor.open(preferences('9.0', 4)); calls[2].resolve(previewData('7.5')); await flush()
  assert.equal(editor.getSnapshot().draftMinScore, '9.0'); assert.equal(editor.getSnapshot().preview.status, 'idle')
  editor.setMinScore('7.0'); time.run(); await flush(); editor.dispose(); editor.activate(); editor.open(preferences())
  calls[3].reject(new Error('old owner')); await flush()
  assert.equal(editor.getSnapshot().preview.status, 'idle')
})

test('P2 preview failure is local and retry only reads; wrong score/version/save flag is not accepted', async () => {
  let reads = 0, writes = 0, fail = true
  const time = timers(), editor = createPreferenceEditor({ previewProfile: async score => {
    reads++; if (fail) throw new Error('offline'); return previewData(score)
  }, savePreferences() { writes++ } }, () => {}, time)
  editor.open(preferences()); editor.setMinScore('7.0'); time.run(); await flush()
  assert.equal(editor.getSnapshot().status, 'editing'); assert.equal(editor.getSnapshot().preview.status, 'error')
  fail = false; await editor.retryPreview()
  assert.equal(editor.getSnapshot().preview.data.usable_sample_count, 3); assert.equal(reads, 2); assert.equal(writes, 0)
  for (const changes of [{ saved: true }, { preference_version: 2 }, { profile_min_score: '6.0' }, { usable_sample_count: -1 },
    { diagnostics: undefined }, { diagnostics: { pending_identity_count: -1 } }]) {
    assert.throws(() => validateProfilePreview({ ...previewData('7.0'), ...changes }, '7.0', preferences()))
  }
})

test('P2 disabled or old server never permits draft threshold changes or sends hidden score writes', async () => {
  let payload
  const initial = preferences('7.0', 1, ['日常'], false)
  const editor = createPreferenceEditor({ savePreferences: async changes => {
    payload = changes; return preferences('7.0', 2, changes.tags, false)
  } })
  editor.open(initial); editor.setMinScore('6.0'); editor.resetMinScore(); editor.clearDraft(); await editor.save()
  assert.deepEqual(payload, { tags: [], expected_version: 1 })
  assert.equal(editor.getSnapshot().preferences.profile_min_score, '7.0')
  assert.equal(editor.getSnapshot().preferences.effective_profile_min_score, '8.5')
})

test('P2 save joins tags and threshold in one versioned write, cancels preview and coalesces clicks', async () => {
  const time = timers(), preview = deferred(), save = deferred(), calls = []; let signal, acknowledged = 0
  const editor = createPreferenceEditor({ previewProfile: (_, value) => { signal = value; return preview.promise },
    savePreferences: changes => { calls.push(changes); return save.promise } }, () => { acknowledged++ }, time)
  editor.open(preferences()); editor.setTag('音乐', true); editor.setMinScore('7.0'); time.run(); await flush()
  const one = editor.save(), two = editor.save(); assert.equal(one, two); await flush()
  assert.equal(signal.aborted, true); assert.equal(editor.close(), false)
  assert.deepEqual(calls, [{ tags: ['日常', '音乐'], expected_version: 1, profile_min_score: '7.0' }])
  save.resolve(preferences('7.0', 2, ['日常', '音乐'])); await one
  preview.resolve(previewData('7.0')); await flush()
  assert.equal(acknowledged, 1); assert.equal(editor.getSnapshot().status, 'closed')
  assert.equal(editor.getSnapshot().preview.status, 'idle')
})

test('P2 save error keeps both drafts; uncertain threshold acknowledgement cannot trigger refresh or repeat', async () => {
  const time = timers(); let calls = 0
  const editor = createPreferenceEditor({ savePreferences: async () => { calls++; throw new Error('offline') } }, () => {}, time)
  editor.open(preferences()); editor.setMinScore('7.0'); editor.setTag('音乐', true); await editor.save()
  assert.equal(editor.getSnapshot().draftMinScore, '7.0'); assert.deepEqual(editor.getSnapshot().draftTags, ['日常', '音乐'])
  assert.equal(editor.getSnapshot().status, 'error'); assert.equal(time.size, 0)
  let refreshes = 0
  const uncertain = createPreferenceEditor({ savePreferences: async () => preferences('8.5', 2) }, () => refreshes++, time)
  uncertain.open(preferences()); uncertain.setMinScore('7.0'); await uncertain.save()
  assert.equal(uncertain.getSnapshot().status, 'uncertain'); await uncertain.save(); assert.equal(refreshes, 0)
})

test('P2 concurrent scalar edits require an explicit choice; local/remote choices never auto-save', async () => {
  for (const choice of ['local', 'remote']) {
    const time = timers(); let writes = 0
    const editor = createPreferenceEditor({ savePreferences: async () => { writes++; throw { response: { status: 409 } } },
      preferences: async () => preferences('9.0', 2, ['日常', '科幻']) }, () => {}, time)
    editor.open(preferences()); editor.setMinScore('7.0'); editor.setTag('音乐', true); await editor.save(); await editor.reloadLatest()
    assert.equal(editor.getSnapshot().scoreConflict, true)
    editor.resolveConflict(true); assert.equal(editor.getSnapshot().status, 'conflict_review')
    editor.resolveConflict(true, choice)
    assert.equal(editor.getSnapshot().draftMinScore, choice === 'local' ? '7.0' : '9.0')
    assert.deepEqual(editor.getSnapshot().draftTags, ['日常', '科幻', '音乐'])
    assert.equal(editor.getSnapshot().preferences.version, 2); assert.equal(writes, 1)
    editor.close(); assert.equal(time.size, 0)
  }
})

test('P2 remote-only scalar changes are preserved and rollback never silently drops a local score draft', async () => {
  const time = timers()
  let remote = preferences('7.0', 2, ['科幻'])
  const editor = createPreferenceEditor({ savePreferences: async () => { throw { response: { status: 409 } } },
    preferences: async () => remote }, () => {}, time)
  editor.open(preferences()); editor.setTag('音乐', true); await editor.save(); await editor.reloadLatest(); editor.resolveConflict(true)
  assert.equal(editor.getSnapshot().draftMinScore, '7.0')
  editor.close(); editor.open(preferences()); editor.setMinScore('6.0'); await editor.save()
  remote = { contract_version: 'recommendations.3a.v1', tags: ['科幻'], version: 2, available_tags: preferences().available_tags }
  await editor.reloadLatest(); editor.resolveConflict(true)
  assert.equal(editor.getSnapshot().status, 'conflict_review'); assert.equal(editor.getSnapshot().draftMinScore, '6.0')
  editor.resolveConflict(false); assert.equal(editor.getSnapshot().draftMinScore, null)
})

test('P2 lower personal evidence scores require matching complete server policy, never a draft or guessed cutoff', () => {
  for (const score of ['6.0', '7.0', '8.0', '8.5', '10.0']) assert.equal(recommendationEvidence(itemData(score)).source.score, Number(score))
  const row = itemData('7.0')
  for (const versions of [{ ...row.evidence.versions, profile_min_score: undefined },
    { ...row.evidence.versions, profile_policy_version: 'unknown' }, { ...row.evidence.versions, profile_min_score: '7.3' }]) {
    assert.equal(recommendationEvidence({ ...row, evidence: { ...row.evidence, versions }, evidenceContext: versions }), null)
  }
  assert.equal(recommendationEvidence({ ...row, evidenceContext: { ...row.evidenceContext, profile_min_score: '8.5' } }), null)
  assert.equal(recommendationEvidence(itemData('7.0', '8.5')), null)
  assert.equal(recommendationEvidence(itemData('0.0', '6.0')), null)
  const old = itemData('7.0'); delete old.evidence.versions.profile_min_score; delete old.evidence.versions.profile_policy_version
  assert.equal(recommendationEvidence(old), null)
})

test('P2 profile/preferences/list and post-save acknowledgements must agree on effective policy', async () => {
  const prefs = preferences('7.0'), profile = profileData(prefs), page = mapRecommendationPage(pageData(prefs))
  validateRecommendationProfile(profile); validateRecommendationSnapshot(profile, prefs, page)
  for (const mismatch of [preferences('8.5'), preferences('7.0', 1, ['日常'], false)]) {
    assert.throws(() => validateRecommendationSnapshot(profile, mismatch, page))
  }
  const client = { profile: async () => profileData(preferences('8.5', 2)), preferences: async () => preferences('8.5', 2),
    list: async () => pageData(preferences('8.5', 2)) }
  await assert.rejects(loadRecommendationSnapshot(client, { category: 'all', filters: {}, confirmedPreferences: preferences('7.0', 2) }))
})

test('P2 saved-but-refresh-failed stays acknowledged and recovery only reads a fresh policy', async () => {
  let saved = preferences(), fail = false, writes = 0
  const client = { profile: async () => profileData(saved), preferences: async () => saved,
    list: async () => { if (fail) throw new Error('offline'); return pageData(saved) },
    capabilities: async () => ({ contract_version: 'recommendations.capabilities.v1', algorithm_version: 'content.v1',
      browse_enabled: false, rollout_revision: 'e'.repeat(64) }),
    savePreferences: async changes => { writes++; saved = preferences(changes.profile_min_score, 2, changes.tags); return saved } }
  const browse = createBrowseController(client)
  await browse.start({ key: 'one', category: 'all', filters: {}, style: 'balanced' })
  const editor = createPreferenceEditor(client, confirmedPreferences => browse.retry({ confirmedPreferences }), timers())
  editor.open(saved); editor.setMinScore('7.0'); fail = true; await editor.save()
  assert.equal(editor.getSnapshot().status, 'closed'); assert.equal(browse.getSnapshot().status, 'error')
  assert.equal(browse.getSnapshot().confirmedPreferences.profile_min_score, '7.0')
  assert.deepEqual(browse.getSnapshot().items, [])
  fail = false; await browse.retry()
  assert.equal(browse.getSnapshot().profile.sample_policy.minimum_score, '7.0'); assert.equal(writes, 1)
})
