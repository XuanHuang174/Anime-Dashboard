import test from 'node:test'
import assert from 'node:assert/strict'
import { createFeedbackController, createHiddenFeedbackController, createRecommendationWriteGate,
  validateFeedbackResult, validateHiddenFeedback } from '../src/utils/recommendationFeedback.js'
import { createWantController } from '../src/utils/recommendationWant.js'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { loadRecommendationSnapshot } from '../src/utils/recommendationSnapshot.js'
import { notifyFeedbackChange, subscribeFeedbackChanges } from '../src/utils/recommendationFeedbackSync.js'
import { mapRecommendationItem } from '../src/utils/recommendations.js'
import { recommendationSessionScope } from '../src/utils/recommendationSession.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

const item = { bangumiId: 101, title: '合成作品', watchStatus: null }
const uuid = () => '00000000-0000-4000-8000-000000000001'
const date = '2026-10-16T12:00:00Z'
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const tick = () => new Promise(resolve => setImmediate(resolve))
const ack = (body, action = 'hide', extra = {}) => ({ contract_version: 'recommendations.feedback.v1', action,
  operation_id: body.operation_id, changed: true, replayed: false, feedback_version: body.expected_feedback_version + 1,
  operation_feedback_version: body.expected_feedback_version + 1,
  feedback: { bangumi_id: body.bangumi_id, is_active: action === 'hide', feedback_version: body.expected_feedback_version + 1,
    hidden_at: date, updated_at: date }, ...extra })
const hidden = (version = 0, extra = {}) => ({ contract_version: 'recommendations.feedback.v1', feedback_version: version,
  count: 0, page: 1, page_size: 12, next_page: null, results: [], ...extra })

test('8B one pending hide across all surfaces, one acknowledgement and no early invalidation', async () => {
  const post = deferred(); let body, calls = 0, confirmations = 0
  const feedback = createFeedbackController({ hideFeedback: data => { body = data; calls++; return post.promise } },
    () => confirmations++, { uuid })
  feedback.observeVersion(0)
  const tasks = Array.from({ length: 20 }, () => feedback.hide(item))
  assert.ok(tasks.every(task => task === tasks[0]))
  await tick(); assert.equal(calls, 1); assert.equal(confirmations, 0)
  assert.equal(feedback.getSnapshot().operations[101].status, 'saving')
  assert.equal(await feedback.hide({ ...item, bangumiId: 102 }), null)
  post.resolve(ack(body)); await Promise.all(tasks)
  assert.equal(confirmations, 1); assert.equal(feedback.getSnapshot().version, 1)
  assert.equal(feedback.getSnapshot().toast.kind, 'hidden')
})

test('8B timeout retry reuses exact body/UUID even after another tab advances version', async () => {
  const bodies = []; let fail = true, confirmations = 0
  const feedback = createFeedbackController({ hideFeedback: async body => {
    bodies.push(body); if (fail) throw new Error('timeout')
    return ack(body, 'hide', { changed: false, replayed: true, feedback_version: 2,
      operation_feedback_version: 1, feedback: { bangumi_id: 101, is_active: false, feedback_version: 2, hidden_at: date, updated_at: date } })
  } }, () => confirmations++, { uuid })
  feedback.observeVersion(0); await feedback.hide(item)
  assert.equal(feedback.getSnapshot().uncertain, true); assert.equal(confirmations, 0)
  assert.equal(await feedback.undo(item, 1), null)
  feedback.observeVersion(2); fail = false; await feedback.retry()
  assert.equal(bodies[0], bodies[1]); assert.equal(bodies[1].expected_feedback_version, 0)
  assert.equal(feedback.getSnapshot().toast.kind, 'undone')
  assert.equal(feedback.getSnapshot().version, 2); assert.equal(confirmations, 1)
})

test('8B conflict never retries a write silently; explicit new confirmation uses a new UUID', async () => {
  let count = 0, n = 0
  const bodies = []
  const feedback = createFeedbackController({ hideFeedback: async body => {
    bodies.push(body); if (!count++) throw { response: { status: 409 } }; return ack(body)
  } }, () => {}, { uuid: () => `${uuid().slice(0, -1)}${++n}` })
  feedback.observeVersion(0); await feedback.hide(item)
  assert.equal(count, 1); assert.equal(feedback.getSnapshot().operations[101].status, 'conflict')
  assert.equal(feedback.getSnapshot().toast, null)
  feedback.observeVersion(1); feedback.clearErrors(); await feedback.hide(item)
  assert.notEqual(bodies[0].operation_id, bodies[1].operation_id)
  assert.equal(bodies[1].expected_feedback_version, 1)
})

test('8B same-work want/feedback exclusion happens synchronously before React rerenders', async () => {
  const gate = createRecommendationWriteGate(), post = deferred(); let body, wants = 0
  const feedback = createFeedbackController({ hideFeedback: data => { body = data; return post.promise } }, () => {}, { writeGate: gate, uuid })
  const want = createWantController({ want: async () => { wants++ } }, () => {}, { writeGate: gate })
  feedback.observeVersion(0)
  const pending = feedback.hide(item)
  assert.equal(await want.add(item), null); assert.equal(wants, 0)
  await tick(); post.resolve(ack(body)); await pending
  const release = gate.acquire(101); assert.equal(typeof release, 'function'); release()
  const wantPost = deferred(); let hides = 0
  const want2 = createWantController({ want: () => wantPost.promise }, () => {}, { writeGate: gate })
  const feedback2 = createFeedbackController({ hideFeedback: () => { hides++ } }, () => {}, { writeGate: gate, uuid })
  feedback2.observeVersion(1); const adding = want2.add(item)
  assert.equal(await feedback2.hide(item), null); assert.equal(hides, 0)
  want2.dispose(); wantPost.resolve(null); await adding
})

test('8B failed hide leaves current detail and resource state intact; success closes the entire context', async () => {
  const raw = { bangumi_id: 101, title_zh: '作品', title_jp: '', score: 7, tags: [], content_rating: 'non_adult' }
  const detail = createRecommendationDetailController({ detail: async () => raw })
  await detail.open(mapRecommendationItem(raw)); detail.openResources()
  let fail = true
  const feedback = createFeedbackController({ hideFeedback: async body => {
    if (fail) throw { response: { status: 429 } }; return ack(body)
  } }, () => detail.close(), { uuid })
  feedback.observeVersion(0); await feedback.hide(item)
  assert.equal(detail.getSnapshot().status, 'ready'); assert.equal(detail.getSnapshot().resourceOpen, true)
  fail = false; await feedback.hide(item)
  assert.equal(detail.getSnapshot().status, 'closed'); assert.equal(detail.getSnapshot().item, undefined)
})

test('8B confirmed save followed by refresh rejection stays confirmed, never becomes failed save', async () => {
  const feedback = createFeedbackController({ hideFeedback: async body => ack(body) }, async () => { throw new Error('refresh failed') }, { uuid })
  feedback.observeVersion(0); const result = await feedback.hide(item); await tick()
  assert.ok(result); assert.equal(feedback.getSnapshot().operations[101].status, 'ready')
  assert.equal(feedback.getSnapshot().syncError, true); assert.equal(feedback.getSnapshot().toast.kind, 'hidden')
})

test('8B closing page cancels feedback and suppresses late success, error and toast', async () => {
  for (const reject of [false, true]) {
    const post = deferred(); let body, signal, callbacks = 0
    const feedback = createFeedbackController({ hideFeedback: (data, s) => { body = data; signal = s; return post.promise } }, () => callbacks++, { uuid })
    feedback.observeVersion(0); const pending = feedback.hide(item); await tick(); feedback.dispose()
    assert.ok(signal.aborted)
    if (reject) post.reject(new Error('late')); else post.resolve(ack(body))
    await pending; assert.equal(callbacks, 0); assert.equal(feedback.getSnapshot().toast, null)
  }
})

test('8B hidden pagination carries version, clears old rows, rejects races and retries first page', async () => {
  const calls = [], next = deferred()
  const controller = createHiddenFeedbackController({ hiddenFeedback: async (params, signal) => {
    calls.push({ params, signal }); return params.page === 1 ? hidden(3, { next_page: 2 }) : next.promise
  } })
  await controller.open(); const pending = controller.load(2)
  assert.deepEqual(calls[1].params, { page: 2, page_size: 12, feedback_version: 3 })
  assert.equal(controller.getSnapshot().data, undefined)
  controller.close(); next.resolve(hidden(3, { page: 2 })); await pending
  assert.ok(calls[1].signal.aborted); assert.notEqual(controller.getSnapshot().status, 'ready')
  await controller.open(); assert.equal(controller.getSnapshot().status, 'ready')
})

test('8B malformed feedback and hidden rows fail closed, never infer identity or rating', () => {
  const body = { bangumi_id: 101, expected_feedback_version: 0, operation_id: uuid() }
  for (const changes of [{ feedback_version: -1 }, { operation_id: 'foreign' }, { feedback: { bangumi_id: 999 } },
    { changed: true, replayed: true }, { feedback_version: 8 }]) {
    assert.throws(() => validateFeedbackResult(ack(body, 'hide', changes), 'hide', body))
  }
  assert.throws(() => validateHiddenFeedback(hidden(0, { results: [{ bangumi_id: 1, is_active: true }] })))
  assert.equal(validateHiddenFeedback(hidden()).feedback_version, 0)
})

test('8B snapshot reads must reach the confirmed feedback version, not silently accept old results', async () => {
  const prefs = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: [] }
  const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 0, cold_start: true,
    sample_policy: { minimum_score: 8.5 }, tag_summary: [] }
  const page = version => ({ contract_version: 'recommendations.3b.v1', mode: 'cold_start', count: 0, results: [],
    page: 1, page_size: 12, next_page: null, algorithm_version: 'content.v1', evidence_context: { algorithm_version: 'content.v1', feedback_version: version } })
  const client = { profile: async () => profile, preferences: async () => prefs, list: async () => page(1) }
  await assert.rejects(loadRecommendationSnapshot(client, { minimumFeedbackVersion: 2 }), /反馈已确认/)
  client.list = async () => page(2)
  assert.equal((await loadRecommendationSnapshot(client, { minimumFeedbackVersion: 2 })).data.evidence_context.feedback_version, 2)
})

function surface() {
  const listeners = new Map(), timers = new Map(), writes = [], channels = []; let n = 0
  const target = { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
    setTimeout: fn => { timers.set(++n, fn); return n }, clearTimeout: id => timers.delete(id),
    localStorage: { setItem: (...args) => writes.push(args) },
    BroadcastChannel: class { constructor(name) { this.name = name; channels.push(this) } postMessage(data) { this.data = data } close() { this.closed = true } } }
  const doc = { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  return { target, doc, writes, channels, listeners, fire: name => listeners.get(name)?.({ key: writes[0]?.[0] }),
    run: async () => { for (const [id, fn] of [...timers]) { timers.delete(id); await fn() } } }
}

test('8B cross-tab signals contain only a nonce; receiver verifies own server version and ignores lower versions', async () => {
  const env = surface(); let version = 0, loads = 0
  notifyFeedbackChange(env.target)
  assert.equal(typeof env.writes[0][1], 'string'); assert.equal(env.channels[0].data, 'invalidate')
  assert.doesNotMatch(JSON.stringify(env.writes), /bangumi|title|token|user|score/)
  const dispose = subscribeFeedbackChanges({ client: { hiddenFeedback: async () => hidden(2) },
    getVersion: () => version, onChanged: value => { version = value; loads++ }, target: env.target, document: env.doc })
  env.fire('storage'); await env.run(); assert.equal(version, 2); assert.equal(loads, 1)
  env.fire('focus'); await env.run(); assert.equal(loads, 1)
  version = 3; env.fire('focus'); await env.run(); assert.equal(version, 3); assert.equal(loads, 1)
  dispose(); assert.equal(env.listeners.size, 0)
})

test('8B cross-tab late verification and errors cannot mutate a disposed or superseded session', async () => {
  const env = surface(), deferredRead = deferred(); let loads = 0, signal
  notifyFeedbackChange(env.target)
  const dispose = subscribeFeedbackChanges({ client: { hiddenFeedback: (params, s) => { signal = s; return deferredRead.promise } },
    getVersion: () => 0, onChanged: () => loads++, target: env.target, document: env.doc })
  env.fire('storage'); const running = env.run(); await tick(); dispose()
  assert.ok(signal.aborted); deferredRead.resolve(hidden(2)); await running; assert.equal(loads, 0)
})

test('8B account change blocks old writes and late acknowledgements; token refresh keeps same owner scope', async () => {
  const token = (owner, nonce) => `header.${btoa(JSON.stringify({ user_id: owner, nonce }))}.signature`
  assert.equal(recommendationSessionScope(token(1, 'old')), recommendationSessionScope(token(1, 'refreshed')))
  assert.notEqual(recommendationSessionScope(token(1, 'old')), recommendationSessionScope(token(2, 'other')))
  let session = 'A', count = 0, callbacks = 0, body
  const post = deferred(), gate = createRecommendationWriteGate({ isCurrent: () => session === 'A' })
  const feedback = createFeedbackController({ hideFeedback: data => { body = data; count++; return post.promise } },
    () => callbacks++, { writeGate: gate, uuid })
  feedback.observeVersion(0); const pending = feedback.hide(item); await tick()
  session = 'B'; post.resolve(ack(body)); await pending
  assert.equal(callbacks, 0); assert.equal(feedback.getSnapshot().toast, null)
  await feedback.hide(item); assert.equal(count, 1)
})

test('8B feedback API uses only fixed feedback paths and carries signal/versions, never journal payloads', async () => {
  const calls = [], signal = new AbortController().signal
  const client = createRecommendationClient({ post: async (...args) => { calls.push(args); return { data: {} } },
    get: async (...args) => { calls.push(args); return { data: hidden() } } })
  const body = { bangumi_id: 101, expected_feedback_version: 0, operation_id: uuid() }
  await client.hideFeedback(body, signal)
  await client.undoFeedback({ ...body, target_feedback_version: 1 }, signal)
  await client.hiddenFeedback({ page: 2, feedback_version: 1 }, signal)
  assert.deepEqual(calls.map(call => call[0]), ['recommendations/feedback/', 'recommendations/feedback/undo/', 'recommendations/feedback/hidden/'])
  assert.equal(calls[0][2].signal, signal); assert.equal(calls[2][1].params.feedback_version, 1)
  assert.deepEqual(Object.keys(calls[0][1]).sort(), ['bangumi_id', 'expected_feedback_version', 'operation_id'])
})

test('8B hidden list cannot display a version older than the current feedback context', async () => {
  let version = 3
  const controller = createHiddenFeedbackController({ hiddenFeedback: async () => hidden(2) }, () => {}, () => version)
  await controller.open(); assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(controller.getSnapshot().data, undefined)
  version = 2; await controller.load(); assert.equal(controller.getSnapshot().status, 'ready')
})

test('8B UUID generation failure releases the write gate without a request', async () => {
  const gate = createRecommendationWriteGate(); let calls = 0
  const feedback = createFeedbackController({ hideFeedback: () => calls++ }, () => {},
    { writeGate: gate, uuid: () => { throw new Error('unavailable') } })
  feedback.observeVersion(0); assert.equal(await feedback.hide(item), null); assert.equal(calls, 0)
  const release = gate.acquire(101); assert.equal(typeof release, 'function'); release()
})
