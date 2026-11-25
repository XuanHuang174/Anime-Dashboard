import test from 'node:test'
import assert from 'node:assert/strict'
import { buildWatchCommand, createWatchHistoryController, datePart, eventWatchDate, formatWatchDate, initialWatchForm, validWatchDate, validWatchHistory, watchEventSummary } from '../src/utils/watchHistory.js'

const round = (patch = {}) => ({ number: 1, is_current: true, voided: false, origin: 'baseline', status: 'watching', watched_eps: 2, total_eps: 12, started_on: '', completed_on: '', rating: null, rating_origin: '', note: '', ...patch })
const state = row => ({ record: { watch_status: row.status, watched_eps: row.watched_eps, total_eps: row.total_eps }, round: row })
const history = (version = 0) => {
  const row = round()
  return { contract_version: 'journal.watch-history.v1', version, rounds: [row], completed_rounds: 0, rewatch_count: 0, count: version, page: 1, page_size: 10,
    events: Array.from({ length: version }, (_, i) => ({ revision: version - i, kind: 'note', source: 'history_command', note: '合成随记', occurred_on: '', before: state(row), after: state(row), recorded_at: '2026-11-25T08:00:00Z' })),
    record: { id: 1, watch_revision: version, watch_status: 'watching', watched_eps: 2, total_eps: 12, personal_score: '9.0' } }
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function env(client) {
  let session = 'owner', serial = 0
  const committed = []
  return { committed, switch: () => { session = 'other' }, engine: createWatchHistoryController({ client, recordId: 1, getSession: () => session, onCommitted: row => committed.push(row), uuid: () => `operation-${++serial}` }) }
}

test('watch dates retain unknown/year/month/day precision and Gregorian validity', () => {
  assert.equal(formatWatchDate(''), '日期未记录')
  assert.equal(formatWatchDate('2020'), '2020年')
  assert.equal(formatWatchDate('2020-02'), '2020年2月')
  assert.equal(formatWatchDate('2020-02-29'), '2020年2月29日')
  for (const value of ['2019-02-29', '2020-00', '2020-2', '2020-04-31', 'today', '0000']) assert.equal(validWatchDate(value), false)
  assert.deepEqual(datePart('2020'), { precision: 'year', value: '2020' })
  assert.deepEqual(datePart(), { precision: 'unknown', value: '' })
})
test('forms only submit action-owned fields, never a work score or inferred date', () => {
  const row = round(), form = initialWatchForm('transition', row, 3, 'completed')
  assert.deepEqual(buildWatchCommand(form), { action: 'transition', version: 3, watch_status: 'completed', watched_eps: 2, total_eps: 12, occurred_on: '', note: '' })
  assert.throws(() => buildWatchCommand({ ...form, watched_eps: '13' }), /总集数/)
  assert.throws(() => buildWatchCommand({ ...form, watched_eps: '' }), /非负整数/)
  assert.throws(() => buildWatchCommand({ ...form, occurred_on: { precision: 'month', value: '2020' } }), /日期/)
  const correction = buildWatchCommand({ ...initialWatchForm('update-round', row, 3), rating: '0', started_on: datePart('2010') })
  assert.equal(correction.rating, 0); assert.equal(correction.started_on, '2010'); assert.equal(correction.completed_on, '')
  assert.equal(Object.hasOwn(correction, 'personal_score'), false)
  assert.throws(() => buildWatchCommand({ ...initialWatchForm('memory', row, 3), note: '回忆' }), /大致年月/)
})
test('response validation rejects broken contracts, dates, duplicate rounds and malformed events', () => {
  assert.equal(validWatchHistory(history()), true)
  assert.equal(validWatchHistory(history(1)), true)
  for (const data of [{ ...history(), contract_version: 'unknown' }, { ...history(), rounds: [] }, { ...history(), rounds: [round(), round()] }, { ...history(), rounds: [round({ started_on: '2020-13' })] }, { ...history(1), events: [{}] }]) assert.equal(Boolean(validWatchHistory(data)), false)
  assert.match(watchEventSummary({ before: state(round()), after: state(round({ status: 'completed', watched_eps: 12 })), kind: 'transition' }), /在看 → 看过/)
  const rewatch = { kind: 'start-rewatch', occurred_on: '', after: state(round({ started_on: '2019-01' })) }
  assert.equal(eventWatchDate(rewatch), '2019-01')
  assert.equal(eventWatchDate({ ...rewatch, kind: 'note' }), '')
  assert.match(watchEventSummary({ before: state(round()), after: state(round({ started_on: '2019-01', rating: '0.0' })), kind: 'update-round' }), /日期未记录 → 2019年1月/)
})
test('newer reads win and a disposed/changed account cannot receive late data', async () => {
  const first = deferred(), second = deferred(), requests = []
  const e = env({ get: (_path, options) => { requests.push(options); return requests.length === 1 ? first.promise : second.promise } })
  const old = e.engine.read(), fresh = e.engine.read(2)
  assert.equal(requests[0].signal.aborted, true)
  second.resolve({ data: history(2) }); await fresh
  first.resolve({ data: history(1) }); await old
  assert.equal(e.engine.getSnapshot().data.version, 2)
  e.switch(); await e.engine.read(); assert.equal(requests.length, 2)
  e.engine.dispose()
})
test('duplicate clicks issue one mutation and commit the canonical server record', async () => {
  const write = deferred(), posts = []
  const e = env({ get: async () => ({ data: history() }), post: (path, body) => { posts.push({ path, body }); return write.promise } })
  await e.engine.read()
  const first = e.engine.submit({ action: 'note', note: '第一条', version: 0 })
  assert.equal(await e.engine.submit({ action: 'note', note: '第二条', version: 0 }), false)
  write.resolve({ data: history(1) }); assert.equal(await first, true)
  assert.equal(posts.length, 1); assert.equal(e.committed[0].watch_revision, 1)
  assert.equal(e.engine.getSnapshot().pending, null)
})
test('unknown write outcome is never automatically replayed and explicit retry retains its UUID and version', async () => {
  const posts = []; let failed = true, readVersion = 0
  const e = env({ get: async () => ({ data: history(readVersion) }), post: async (_path, body) => {
    posts.push(body); if (failed) { failed = false; throw { response: { status: 500, data: { detail: '结果未确认' } } } }
    return { data: { ...history(2), replayed: true } }
  } })
  await e.engine.read(); await e.engine.submit({ action: 'note', note: '原随记', version: 0 })
  assert.equal(posts.length, 1); assert.equal(e.engine.getSnapshot().pending.note, '原随记')
  readVersion = 1; await e.engine.read()
  assert.equal(await e.engine.submit({ action: 'note', note: '不能创建', version: 1 }), false)
  assert.equal(await e.engine.submit(null, true), true)
  assert.deepEqual(posts[1], posts[0]); assert.equal(e.committed[0].watch_revision, 2)
})
test('conflicts require explicit reread and known validation failures permit corrected submissions', async () => {
  const posts = []; let code = 409
  const e = env({ get: async () => ({ data: history(2) }), post: async (_path, body) => { posts.push(body); throw { response: { status: code, data: { detail: '请核对' } } } } })
  await e.engine.read(); await e.engine.submit({ action: 'note', note: '草稿', version: 0 })
  assert.equal(e.engine.getSnapshot().conflict, true)
  assert.equal(await e.engine.submit({ action: 'note', note: '草稿', version: 2 }), false)
  await e.engine.read(); code = 400; await e.engine.submit({ action: 'note', note: '修正', version: 2 })
  assert.equal(e.engine.getSnapshot().status, 'ready'); assert.equal(e.engine.getSnapshot().pending, null)
  code = 429; await e.engine.submit({ action: 'note', note: '修正', version: 2 })
  assert.equal(posts.length, 3)
})
test('late write acknowledgements after an account switch never update the new account', async () => {
  const write = deferred()
  const e = env({ get: async () => ({ data: history() }), post: () => write.promise })
  await e.engine.read(); const pending = e.engine.submit({ action: 'note', note: '旧账号', version: 0 })
  e.switch(); write.resolve({ data: history(1) }); assert.equal(await pending, false)
  assert.deepEqual(e.committed, [])
})
test('incomplete write acknowledgements retain the original operation for safe verification', async () => {
  const e = env({ get: async () => ({ data: history() }), post: async () => ({ data: { ...history(1), record: undefined } }) })
  await e.engine.read(); await e.engine.submit({ action: 'note', note: '待确认', version: 0 })
  assert.equal(e.engine.getSnapshot().pending.note, '待确认'); assert.deepEqual(e.committed, [])
})
