import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreferenceEditor, mergePreferenceDraft } from '../src/utils/recommendationPreferences.js'
import { loadRecommendationSnapshot } from '../src/utils/recommendationSnapshot.js'
import { notifyRecommendationChange, subscribeRecommendationReload } from '../src/utils/recommendationReload.js'
import { createWantController } from '../src/utils/recommendationWant.js'

const available = ['日常', '音乐', '科幻', '治愈', ...Array.from({ length: 22 }, (_, i) => `题材${i}`)]
const pref = (tags = ['日常'], version = 1) => ({ contract_version: 'recommendations.3a.v1', tags, version, available_tags: available })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

test('choosing observations, toggling and clearing are drafts only; closing writes nothing', () => {
  let calls = 0
  const editor = createPreferenceEditor({ savePreferences() { calls += 1 } })
  const initial = pref()
  editor.open(initial); editor.setTag('音乐', true)
  assert.deepEqual(editor.getSnapshot().draftTags, ['日常', '音乐'])
  assert.deepEqual(initial.tags, ['日常'])
  editor.clearDraft(); assert.deepEqual(editor.getSnapshot().draftTags, [])
  editor.close(); assert.equal(calls, 0)
  editor.open(initial); assert.deepEqual(editor.getSnapshot().draftTags, ['日常'])
})

test('draft enforces max twenty and ignores unsupported topics without guessing', () => {
  const editor = createPreferenceEditor({})
  editor.open(pref(available.slice(0, 20)))
  editor.setTag(available[20], true); editor.setTag('二刷', true)
  assert.equal(editor.getSnapshot().draftTags.length, 20)
  assert.match(editor.getSnapshot().error, /最多选择 20/)
})

test('one validated acknowledgement triggers one refresh; retries use version and retain failed draft', async () => {
  const pending = deferred(), calls = [], confirmed = []
  const editor = createPreferenceEditor({ savePreferences(changes, signal) { calls.push({ changes, signal }); return pending.promise } }, data => confirmed.push(data))
  editor.open(pref()); editor.setTag('音乐', true)
  const one = editor.save(), two = editor.save()
  assert.equal(one, two); assert.equal(editor.close(), false)
  await Promise.resolve()
  assert.deepEqual(calls[0].changes, { tags: ['日常', '音乐'], expected_version: 1 })
  pending.reject(new Error('offline')); await one
  assert.equal(editor.getSnapshot().status, 'error')
  assert.deepEqual(editor.getSnapshot().draftTags, ['日常', '音乐'])
  assert.equal(confirmed.length, 0)
  const success = createPreferenceEditor({ savePreferences: async () => pref(['音乐', '日常'], 2) }, data => confirmed.push(data))
  success.open(pref()); success.setTag('音乐', true)
  await success.save()
  assert.equal(success.getSnapshot().status, 'closed'); assert.equal(confirmed.length, 1)
})

test('clear saves only tags and expected_version with no journal or profile fields', async () => {
  let payload
  const editor = createPreferenceEditor({ savePreferences: async changes => { payload = changes; return pref([], 2) } })
  editor.open(pref()); editor.clearDraft(); await editor.save()
  assert.deepEqual(payload, { tags: [], expected_version: 1 })
})

test('409 preserves original draft; explicit read/merge retains another tab additions and never autosaves', async () => {
  let writes = 0, reads = 0
  const editor = createPreferenceEditor({ savePreferences: async () => { writes += 1; throw { response: { status: 409 } } },
    preferences: async () => { reads += 1; return pref(['日常', '科幻'], 2) } })
  editor.open(pref()); editor.setTag('日常', false); editor.setTag('音乐', true)
  await editor.save(); assert.equal(editor.getSnapshot().status, 'conflict')
  await editor.save(); assert.equal(writes, 1)
  await editor.reloadLatest()
  assert.equal(editor.getSnapshot().status, 'conflict_review')
  assert.deepEqual(editor.getSnapshot().draftTags, ['音乐'])
  editor.resolveConflict(true)
  assert.deepEqual(editor.getSnapshot().draftTags, ['科幻', '音乐'])
  assert.equal(editor.getSnapshot().preferences.version, 2)
  assert.equal(writes, 1); assert.equal(reads, 1)
  assert.match(editor.getSnapshot().message, /尚未保存/)
  assert.deepEqual(mergePreferenceDraft(['日常'], ['日常', '音乐'], ['日常', '科幻']), ['日常', '科幻', '音乐'])
})

test('conflict can explicitly discard draft; overflow and failed reads preserve it', async () => {
  let fail = true
  const editor = createPreferenceEditor({ savePreferences: async () => { throw { response: { status: 409 } } },
    preferences: async () => { if (fail) throw new Error(); return pref(available.slice(0, 20), 2) } })
  editor.open(pref([])); editor.setTag(available[20], true); await editor.save()
  await editor.reloadLatest(); assert.equal(editor.getSnapshot().status, 'conflict')
  assert.deepEqual(editor.getSnapshot().draftTags, [available[20]])
  fail = false; await editor.reloadLatest(); editor.resolveConflict(true)
  assert.equal(editor.getSnapshot().status, 'conflict_review'); assert.match(editor.getSnapshot().error, /超出上限/)
  editor.resolveConflict(false)
  assert.deepEqual(editor.getSnapshot().draftTags, available.slice(0, 20))
})

test('malformed acknowledgement cannot claim success or submit again until re-read', async () => {
  let refreshes = 0, writes = 0
  const editor = createPreferenceEditor({ savePreferences: async () => { writes += 1; return { version: 2, tags: ['音乐'] } } }, () => refreshes++)
  editor.open(pref()); await editor.save()
  assert.equal(editor.getSnapshot().status, 'uncertain')
  await editor.save(); assert.equal(writes, 1); assert.equal(refreshes, 0)
})

test('refresh initiation failure is already saved, whereas a changed draft requires a new acknowledged version', async () => {
  const editor = createPreferenceEditor({ savePreferences: async () => pref(['音乐'], 2) }, () => { throw new Error('refresh start') })
  editor.open(pref()); editor.setTag('日常', false); editor.setTag('音乐', true)
  const saved = await editor.save()
  assert.equal(saved.version, 2); assert.equal(editor.getSnapshot().status, 'saved_refresh_failed')
  assert.match(editor.getSnapshot().error, /偏好已保存，推荐更新失败/)
  const stale = createPreferenceEditor({ savePreferences: async () => pref(['音乐'], 1) })
  stale.open(pref()); stale.setTag('日常', false); stale.setTag('音乐', true); await stale.save()
  assert.equal(stale.getSnapshot().status, 'uncertain')
})

test('unmount aborts and suppresses late acknowledgement; StrictMode activation can open again', async () => {
  const pending = deferred(); let signal, refreshes = 0
  const editor = createPreferenceEditor({ savePreferences: (_, value) => { signal = value; return pending.promise } }, () => refreshes++)
  editor.open(pref()); const saving = editor.save(); await Promise.resolve()
  editor.dispose(); assert.equal(signal.aborted, true)
  editor.activate(); editor.open(pref(['音乐'], 3)); pending.resolve(pref(['日常'], 2)); await saving
  assert.equal(refreshes, 0); assert.deepEqual(editor.getSnapshot().draftTags, ['音乐'])
})

const snapshotClient = (preferences, overrides = {}) => ({ preferences: async () => preferences,
  profile: async () => ({ contract_version: 'recommendations.3a.v1', eligible_sample_count: 0, cold_start: true,
    tag_summary: [], sample_policy: { minimum_score: '8.5' } }),
  list: async () => ({ contract_version: 'recommendations.3b.v1', mode: 'preference_based', count: 0,
    page: 1, page_size: 12, next_page: null, state_token: 'a'.repeat(64), results: [] }), ...overrides })
const options = { category: 'all', filters: {}, signal: new AbortController().signal }

test('save success followed by refresh failure stays acknowledged and retry only reads', async () => {
  let writes = 0, acknowledged, refreshFailed
  const editor = createPreferenceEditor({ savePreferences: async () => { writes++; return pref(['音乐'], 2) } }, data => { acknowledged = data })
  editor.open(pref()); editor.setTag('日常', false); editor.setTag('音乐', true); await editor.save()
  await assert.rejects(loadRecommendationSnapshot(snapshotClient(acknowledged, { list: async () => { throw new Error('offline') } }),
    { ...options, confirmedPreferences: acknowledged }))
  refreshFailed = true
  assert.equal(editor.getSnapshot().status, 'closed'); assert.equal(refreshFailed, true)
  const loaded = await loadRecommendationSnapshot(snapshotClient(acknowledged), { ...options, confirmedPreferences: acknowledged })
  assert.deepEqual(loaded.preferences.tags, ['音乐']); assert.equal(writes, 1)
})

test('post-save reads reject stale versions or mismatched confirmation, accept newer authoritative settings', async () => {
  for (const stale of [pref(['音乐'], 1), pref(['科幻'], 2)]) {
    await assert.rejects(loadRecommendationSnapshot(snapshotClient(stale), { ...options, confirmedPreferences: pref(['音乐'], 2) }))
  }
  const loaded = await loadRecommendationSnapshot(snapshotClient(pref(['科幻'], 3)), { ...options, confirmedPreferences: pref(['音乐'], 2) })
  assert.deepEqual(loaded.preferences.tags, ['科幻'])
})

function events() {
  const target = new EventTarget(), document = new EventTarget(), scheduled = new Map(), stored = []
  let index = 0
  document.visibilityState = 'visible'
  target.setTimeout = fn => { scheduled.set(++index, fn); return index }
  target.clearTimeout = id => scheduled.delete(id)
  target.localStorage = { setItem: (...args) => stored.push(args) }
  return { target, document, stored, run() { for (const [id, fn] of [...scheduled]) { scheduled.delete(id); fn() } } }
}

test('return-to-foreground and known journal events coalesce, defer during editing, and clean up', () => {
  const env = events(); let calls = 0, blocked = true
  const sync = subscribeRecommendationReload({ ...env, canReload: () => !blocked, onReload: () => calls++ })
  notifyRecommendationChange({}, env.target)
  env.target.dispatchEvent(new Event('focus')); env.document.dispatchEvent(new Event('visibilitychange'))
  env.run(); assert.equal(calls, 0)
  blocked = false; sync.flush(); env.run(); assert.equal(calls, 1)
  env.document.visibilityState = 'hidden'; notifyRecommendationChange({}, env.target); env.run(); assert.equal(calls, 1)
  env.document.visibilityState = 'visible'; env.document.dispatchEvent(new Event('visibilitychange')); env.run(); assert.equal(calls, 2)
  env.target.dispatchEvent(new Event('focus')); sync.dispose(); env.run(); assert.equal(calls, 2)
  assert.ok(env.stored.every(([,value]) => /^\d+-0\.\d+$/.test(value))) // opaque nonce only
})

test('cross-tab changes only invalidate; unrelated storage and normal pageshow do not poll', () => {
  const env = events(); let calls = 0
  const sync = subscribeRecommendationReload({ ...env, onReload: () => calls++ })
  const emit = (name, attrs) => { const event = new Event(name); Object.assign(event, attrs); env.target.dispatchEvent(event) }
  emit('storage', { key: 'other' }); emit('pageshow', { persisted: false }); env.run(); assert.equal(calls, 0)
  emit('storage', { key: 'anime-dashboard:recommendation-invalidate:v1' }); emit('pageshow', { persisted: true })
  env.run(); assert.equal(calls, 1)
  notifyRecommendationChange({ local: false }, env.target); env.run(); assert.equal(calls, 1)
  sync.dispose()
})

test('an explicit reload consumes a deferred notification without a redundant follow-up request', () => {
  const env = events(); let calls = 0
  const sync = subscribeRecommendationReload({ ...env, onReload: () => calls++ })
  notifyRecommendationChange({}, env.target)
  sync.acknowledge(); env.run(); assert.equal(calls, 0)
  env.target.dispatchEvent(new Event('focus')); env.run(); assert.equal(calls, 0)
  sync.dispose()
})

test('authoritative journal refresh reconciles ready want status after deletion/change without cancelling POST', async () => {
  let writes = 0
  const result = { contract_version: 'recommendations.want.v1', bangumi_id: 123, anime_id: 3, record_id: 9,
    content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' }
  const controller = createWantController({ want: async () => { writes++; return result } })
  const item = { bangumiId: 123, watchStatus: null }
  await controller.add(item)
  controller.reconcile([{ ...item, watchStatus: 'completed' }])
  assert.equal(controller.getSnapshot()[123].watchStatus, 'completed')
  controller.reconcile([item]); assert.equal(controller.getSnapshot()[123], undefined)
  await controller.add(item); assert.equal(writes, 2)
  const pending = deferred(); let signal
  const busy = createWantController({ want: (_, value) => { signal = value; return pending.promise } })
  const saving = busy.add(item); await Promise.resolve(); busy.reconcile([item])
  assert.equal(busy.getSnapshot()[123].status, 'adding'); assert.equal(signal.aborted, false)
  pending.resolve(result); await saving
  assert.equal(busy.getSnapshot()[123].status, 'ready')
})
