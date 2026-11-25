import test from 'node:test'
import assert from 'node:assert/strict'
import { buildPlanCommand, createPlanController, loadCompletePlanGroup, movePlan, planDateLabel, planForm, plannedTimePassed, validPlanDetail, validPlanOrder, validPlanQueue } from '../src/utils/watchPlans.js'

const plan = { version: 1, priority: 2, position: 0, planned_for: '', note: '', purpose: 'first', state: 'pending', target_round_number: 1, anchor_round_number: 1, history_base: 0, enrolled_at: '2026-11-25T00:00:00Z' }
const record = { id: 1, title: '合成计划', japanese_title: '', cover: '', watch_status: 'plan', watch_revision: 0, watched_eps: 0, total_eps: 12 }
const detail = (version = 0) => ({ contract_version: 'journal.watch-plan.v1', version, record, plan, canonical_record: { id: 1, watch_revision: 0, watch_status: 'plan', watched_eps: 0, total_eps: 12, anime: { chinese_title: '合成计划', japanese_title: '' }, review: '' } })
const queue = (version = 0) => ({ contract_version: 'journal.watch-plans.v1', version, count: 1, page: 1, page_size: 20, counts_scope: 'owner_all_plans', counts: { pending: 1, ongoing: 0, completed: 0, archived: 0 }, results: [{ record, plan }] })
const order = version => ({ contract_version: 'journal.watch-plan-order.v1', version, priority: 2, record_ids: [1], replayed: false })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function env(client, id = 1) {
  let session = 'owner', serial = 0
  const committed = []
  return { committed, switch: () => { session = 'other' }, engine: createPlanController({ client, recordId: id, getSession: () => session, onCommitted: (...data) => committed.push(data), uuid: () => `operation-${++serial}` }) }
}
test('plan dates retain precision and only mark a whole elapsed range for rescheduling', () => {
  assert.equal(planDateLabel(''), '时间未定')
  assert.equal(planDateLabel('2030-02'), '2030年2月')
  assert.equal(plannedTimePassed('2030', new Date(2030, 7, 1)), false)
  assert.equal(plannedTimePassed('2030-02', new Date(2030, 1, 28, 12)), false)
  assert.equal(plannedTimePassed('2030-02', new Date(2030, 2, 1)), true)
  assert.equal(plannedTimePassed('', new Date(2030, 2, 1)), false)
  const form = planForm(null, 4)
  assert.deepEqual(buildPlanCommand(form), { action: 'enroll', version: 4, priority: 2, planned_for: '', note: '' })
  assert.throws(() => buildPlanCommand({ ...form, planned: { precision: 'day', value: '2021-02-29' } }), /日期/)
  assert.throws(() => buildPlanCommand({ ...form, note: 'x'.repeat(501) }), /500/)
  assert.equal(planForm({ ...plan, state: 'completed' }, 4).action, 'enroll')
})
test('plan contracts reject incomplete records, nested states and duplicate queue rows', () => {
  assert.equal(validPlanDetail(detail(), 1), true)
  assert.equal(validPlanQueue(queue()), true)
  assert.equal(validPlanOrder(order(1)), true)
  for (const value of [{ ...detail(), record: { id: 1 } }, { ...detail(), plan: { ...plan, planned_for: '2020-13' } }, { ...detail(), plan: { ...plan, state: 'unknown' } }]) assert.equal(validPlanDetail(value, 1), false)
  assert.equal(validPlanQueue({ ...queue(), count: 2, results: [...queue().results, ...queue().results] }), false)
  assert.equal(validPlanOrder({ ...order(1), record_ids: [1, 1] }), false)
  assert.equal(validPlanDetail(detail(), 9), false)
})
test('manual moves preserve membership and do not mutate the original order', () => {
  const ids = [1, 2, 3]
  assert.deepEqual(movePlan(ids, 3, 1), [3, 1, 2])
  assert.deepEqual(movePlan(ids, 1, 3), [2, 3, 1])
  assert.deepEqual(movePlan(ids, 99, 1), ids)
  assert.deepEqual(ids, [1, 2, 3])
})
test('complete order loading spans pages and refuses a mixed-version or partial group', async () => {
  const pages = [0, 1].map(index => ({ ...queue(4), count: 2, page: index + 1, page_size: 1, results: [{ record: { ...record, id: index + 1 }, plan }] }))
  let calls = 0
  const client = { get: async () => ({ data: pages[calls++] }) }
  assert.deepEqual((await loadCompletePlanGroup(client, 2)).rows.map(row => row.record.id), [1, 2])
  calls = 0; pages[1].version = 5
  await assert.rejects(loadCompletePlanGroup(client, 2), /变化/)
  calls = 0; pages[1].version = 4; pages[1].results[0].plan = { ...plan, priority: 1 }
  await assert.rejects(loadCompletePlanGroup(client, 2), /不完整/)
})
test('latest reads win, abort previous reads, and ignore another account', async () => {
  const first = deferred(), second = deferred(), calls = []
  const e = env({ get: (_path, options) => { calls.push(options); return calls.length === 1 ? first.promise : second.promise } })
  const old = e.engine.read(), fresh = e.engine.read()
  assert.equal(calls[0].signal.aborted, true)
  second.resolve({ data: detail(2) }); await fresh
  first.resolve({ data: detail(1) }); await old
  assert.equal(e.engine.getSnapshot().data.version, 2)
  e.switch(); await e.engine.read(); assert.equal(calls.length, 2)
})
test('one submit gate commits canonical state once and retains the action identity', async () => {
  const write = deferred(), posts = []
  const e = env({ get: async () => ({ data: detail() }), post: (_path, body) => { posts.push(body); return write.promise } })
  await e.engine.read(); const first = e.engine.submit({ action: 'start', version: 0, watch_version: 0 })
  assert.equal(await e.engine.submit({ action: 'start', version: 0, watch_version: 0 }), false)
  write.resolve({ data: detail(1) }); assert.equal(await first, true)
  assert.equal(posts.length, 1); assert.equal(e.committed[0][1].action, 'start')
})
test('unknown writes retain exact UUID and body through a successful read and explicit retry', async () => {
  const posts = []; let failed = false
  const e = env({ get: async () => ({ data: detail(2) }), post: async (_path, body) => { posts.push(body); if (!failed) { failed = true; throw { response: { status: 500 } } } return { data: detail(3) } } })
  await e.engine.read(); await e.engine.submit({ action: 'update', version: 2, note: '草稿' })
  await e.engine.read(); assert.equal(await e.engine.submit({ action: 'remove', version: 2 }), false)
  assert.equal(await e.engine.submit(null, true), true); assert.deepEqual(posts[0], posts[1])
})
test('conflicts need reread, while known validation errors allow corrected explicit submissions', async () => {
  let code = 409, posts = 0
  const e = env({ get: async () => ({ data: detail(2) }), post: async () => { posts++; throw { response: { status: code, data: { detail: '请核对' } } } } })
  await e.engine.read(); await e.engine.submit({ action: 'update', version: 1 })
  assert.equal(e.engine.getSnapshot().conflict, true)
  assert.equal(await e.engine.submit({ action: 'update', version: 2 }), false)
  await e.engine.read(); code = 400; await e.engine.submit({ action: 'update', version: 2 })
  assert.equal(e.engine.getSnapshot().pending, null); assert.equal(posts, 2)
})
test('a confirmed reorder with a failed display refresh is never reported as an unknown write', async () => {
  let fail = false
  const e = env({ get: async () => { if (fail) throw new Error('read failed'); return { data: queue() } }, post: async () => ({ data: order(1) }) }, null)
  await e.engine.read(); assert.equal(await e.engine.submit({ version: 0, priority: 2, record_ids: [1] }), true)
  assert.equal(e.engine.getSnapshot().needsRead, true)
  fail = true; await e.engine.read()
  assert.equal(e.engine.getSnapshot().pending, null)
  assert.equal(await e.engine.submit({ version: 1, priority: 2, record_ids: [1] }), false)
})
test('incomplete acknowledgement and late account writes cannot corrupt the next screen', async () => {
  const e = env({ get: async () => ({ data: detail() }), post: async () => ({ data: { ...detail(1), canonical_record: undefined } }) })
  await e.engine.read(); await e.engine.submit({ action: 'enroll', version: 0 })
  assert.equal(e.engine.getSnapshot().pending.body.action, 'enroll')
  const write = deferred(), other = env({ get: async () => ({ data: detail() }), post: () => write.promise })
  await other.engine.read(); const action = other.engine.submit({ action: 'start', version: 0, watch_version: 0 })
  other.switch(); write.resolve({ data: detail(1) }); assert.equal(await action, false); assert.equal(other.committed.length, 0)
})

test('a stale read cannot roll back a confirmed mutation or enable an old queue write', async () => {
  let readVersion = 0
  const e = env({ get: async () => ({ data: detail(readVersion) }), post: async () => ({ data: detail(1) }) })
  await e.engine.read(); assert.equal(await e.engine.submit({ action: 'update', version: 0, note: 'new' }), true)
  await e.engine.read(); assert.equal(e.engine.getSnapshot().status, 'error')
  assert.equal(e.engine.getSnapshot().data.version, 1); assert.equal(e.engine.getSnapshot().pending, null)
  readVersion = 1; await e.engine.read(); assert.equal(e.engine.getSnapshot().status, 'ready')
})
