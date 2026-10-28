import test from 'node:test'
import assert from 'node:assert/strict'
import { createCreditsWorkspace } from '../src/utils/creditsWorkspace.js'
import { CREDIT_DEFAULTS, validateCreditsDetail } from '../src/utils/recommendationCredits.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { creditsPage, creditsDetail } from './fixtures/creditsContract.js'
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const page = () => creditsPage()
const row = () => page().results[0]
const result = (status = 'plan') => ({ contract_version: 'recommendations.want.v1', bangumi_id: 100,
  anime_id: 99, record_id: 1, created: true, outcome: 'created', content_rating: 'non_adult', watch_status: status })
const api = overrides => ({ creditsWorks: async () => page(), creditsDetail: async () => creditsDetail(row()),
  creditsWant: async () => result(), ...overrides })
const create = (client = api(), options = {}) => createCreditsWorkspace({ client, kind: 'voice_actor', person: 101, ...options })

test('detail membership, contract and scope reject unrelated or malformed items', () => {
  const data = creditsDetail(row())
  assert.equal(validateCreditsDetail(data, 'voice_actor', 101, row(), 'safe').title, row().title_zh)
  for (const change of [d => d.item.bangumi_id++, d => d.person_id++, d => d.kind = 'studio',
    d => d.item.synopsis = null, d => d.item.content_rating = 'adult', d => d.item.watch_status = 'other']) {
    const invalid = creditsDetail(row()); change(invalid)
    assert.throws(() => validateCreditsDetail(invalid, 'voice_actor', 101, row(), 'safe'))
  }
})
test('late detail cannot replace a different selected item or reopen a closed modal', async () => {
  const requests = [deferred(), deferred()]; let calls = 0
  const controller = create(api({ creditsDetail: () => requests[calls++].promise }))
  await controller.load()
  const first = controller.select(100), second = controller.select(101)
  requests[1].resolve(creditsDetail(page().results[1])); await second
  requests[0].resolve(creditsDetail(row())); await first
  assert.equal(controller.getSnapshot().detail.target, 101)
  const pending = deferred(); const c = create(api({ creditsDetail: () => pending.promise })); await c.load()
  const detail = c.select(100); c.closeDetail(); pending.resolve(creditsDetail(row())); await detail
  assert.equal(c.getSnapshot().detail, null)
})
test('card and detail share a single POST and reconcile only confirmed acknowledgements', async () => {
  const pending = deferred(), calls = []; let confirmed = 0
  let controller
  controller = create(api({ creditsWant: (...args) => { calls.push(args); return pending.promise } }), {
    onConfirmed: () => { confirmed++; assert.equal(controller.isNotifying(), true); throw Error('notification failed') } })
  await controller.load(); await controller.select(100)
  const first = controller.want(100), repeated = controller.want(100)
  assert.equal(first, repeated)
  await controller.load() // Cannot replace the snapshot while a write is pending.
  assert.equal(controller.getSnapshot().operations[100].status, 'adding')
  pending.resolve(result()); await first
  assert.equal(calls.length, 1); assert.equal(calls[0][3], row().action_context)
  assert.equal(confirmed, 1); assert.equal(controller.isNotifying(), false)
  assert.equal(controller.getSnapshot().detail.item.watchStatus, 'plan')
  assert.equal(controller.getSnapshot().data.results[0].watch_status, 'plan')
  assert.equal(controller.getSnapshot().operations[100].status, 'ready')
  assert.equal(controller.getSnapshot().stale, true)
  assert.equal(await controller.more(), false)
})
test('unconfirmed request permits manual retry with the same proof, never automatically retries', async () => {
  let calls = 0
  const controller = create(api({ creditsWant: async () => { if (++calls === 1) throw Error('lost acknowledgement'); return result('completed') } }))
  await controller.load(); await controller.want(100)
  assert.equal(calls, 1); assert.equal(controller.getSnapshot().operations[100].error.code, 'retry')
  assert.equal(controller.getSnapshot().data.results[0].watch_status, null)
  await controller.want(100)
  assert.equal(calls, 2); assert.equal(controller.getSnapshot().operations[100].watchStatus, 'completed')
})
test('server-reconciled existing status prevents overwrite and exposes updated status', async () => {
  let writes = 0
  const controller = create(api({ creditsDetail: async () => creditsDetail({ ...row(), watch_status: 'watching' }), creditsWant: () => writes++ }))
  await controller.load(); await controller.select(100); await controller.want(100)
  assert.equal(writes, 0); assert.equal(controller.getSnapshot().data.results[0].watch_status, 'watching')
  assert.equal(controller.getSnapshot().stale, true)
})
test('scope/account changes, expiry and invalidation prevent late writes from publishing', async () => {
  const pending = deferred(); let current = true, confirmed = 0, clock = Date.now(), writes = 0
  const controller = create(api({ creditsWant: () => { writes++; return pending.promise } }), {
    isCurrent: () => current, onConfirmed: () => confirmed++, now: () => clock })
  await controller.load(); const write = controller.want(100); await Promise.resolve()
  current = false; controller.dispose(); pending.resolve(result()); await write
  assert.equal(confirmed, 0); assert.equal(controller.getSnapshot().operations[100]?.status, undefined)
  current = true; controller.activate(); clock += 901000; await controller.want(100)
  assert.equal(writes, 1)
})
test('write conflicts preserve scope and require identity review; revoked scope clears details', async () => {
  const errors = [
    { response: { status: 409, data: { code: 'identity_confirmation_required' } } },
    { response: { status: 403, data: { code: 'capacity_exceeded' } } },
  ]
  for (const error of errors) {
    const controller = create(api({ creditsWant: async () => { throw error } })); await controller.load(); await controller.want(100)
    assert.equal(controller.getSnapshot().stale, false)
    assert.equal(controller.getSnapshot().operations[100].error.code, error.response.data.code)
  }
  const controller = create(api({ creditsWant: async () => { throw { response: { status: 403 } } } }))
  await controller.load(); await controller.select(100); await controller.want(100)
  assert.equal(controller.getSnapshot().data, null); assert.equal(controller.getSnapshot().detail, null)
})
test('adult client distinguishes business conflicts from actual scope revocation', async () => {
  let lost = 0, failure
  const transport = { post: async () => { throw failure } }
  const client = createRecommendationClient(transport, 'include_adult', () => lost++)
  for (const code of ['identity_confirmation_required', 'public_identity_conflict', 'journal_field_conflict', 'identity_changed', 'credits_snapshot_changed']) {
    failure = { response: { status: 409, data: { code } } }
    await assert.rejects(client.creditsWant('studio', 201, 1, 'proof'))
  }
  failure = { response: { status: 403, data: { code: 'capacity_exceeded' } } }
  await assert.rejects(client.creditsWant('studio', 201, 1, 'proof')); assert.equal(lost, 0)
  failure = { response: { status: 403 } }
  await assert.rejects(client.creditsWant('studio', 201, 1, 'proof')); assert.equal(lost, 1)
})
test('completed query carries explicit hide_recorded=false and validates the returned conditions', async () => {
  const options = { ...CREDIT_DEFAULTS, view: 'completed', hide_recorded: false }, calls = []
  const controller = create(api({ creditsWorks: async (_, __, params) => {
    calls.push(params); const data = creditsPage({ count: 1, options }); data.results[0].watch_status = 'completed'; return data
  } }))
  await controller.load(options)
  assert.deepEqual(calls, [options]); assert.equal(controller.getSnapshot().data.results[0].watch_status, 'completed')
})
test('cancelled POST settlement cannot release a newer pending POST for the same work', async () => {
  const old = deferred(), newer = deferred(); let calls = 0
  const controller = create(api({ creditsWant: () => ++calls === 1 ? old.promise : newer.promise }))
  await controller.load(); const a = controller.want(100); await Promise.resolve()
  controller.invalidate(); await controller.load(); const b = controller.want(100); await Promise.resolve()
  old.resolve(result()); await a
  assert.equal(controller.want(100), b)
  newer.resolve(result()); await b
  assert.equal(calls, 2)
})
