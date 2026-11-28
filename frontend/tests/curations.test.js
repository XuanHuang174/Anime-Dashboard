import test from 'node:test'
import assert from 'node:assert/strict'
import { changedCurationFields, createCurationController, loadCompleteCuration, moveCuration, validCurationPage, validCurationReceipt } from '../src/utils/curations.js'

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const record = n => ({ id: n, title: `合成作品${n}`, japanese_title: '', cover: '', period: '2026-01', watch_status: 'on_hold', watched_eps: 3, total_eps: 12, watch_revision: 0, personal_score: null })
const collection = (n = 1, count = 0) => ({ id: id(n), title: `合成片单${n}`, description: '', pinned: false, archived: false, position: n, entry_count: count, unavailable_count: 0, cover_entry_id: null, cover: count ? record(1) : null })
const entry = n => ({ id: id(n+1000), position: n-1, title_snapshot: { title: `合成作品${n}`, original_title: '', period: '2026-01' }, reason: '', unavailable: false, record: record(n) })
function page(params = {}, version = 1, total = 1) {
  const p = params.page || 1, size = params.page_size || 12
  return { contract_version: 'journal.curations.v1', version, page: p, page_size: size, count: total, next_page: p*size < total ? p+1 : null,
    ...(params.listId ? { collection: collection(1, total) } : {}), results: Array.from({ length: Math.min(size, Math.max(0,total-(p-1)*size)) }, (_, i) => params.listId ? entry((p-1)*size+i+1) : collection((p-1)*size+i+1)) }
}
const ack = body => ({ contract_version: 'journal.curations.v1', version: body.version+1, replayed: false, list_id: body.list_id || id(1), collection: body.action === 'delete' ? null : collection() })
const deferred = () => { let resolve; return { promise: new Promise(r => { resolve = r }), resolve: v => resolve(v) } }

test('curation pages require complete counts, scoped ids, current records and placeholder semantics', () => {
  const query = { listId: id(1), page: 1, page_size: 12 }, good = page(query)
  assert.equal(validCurationPage(good, query), true)
  for (const change of [d => d.collection.id = id(2), d => d.results.pop(), d => d.results[0].unavailable = true, d => d.results[0].record.personal_score = '20', d => d.version = -1, d => d.count = 501]) {
    const bad = structuredClone(good); change(bad); assert.equal(validCurationPage(bad, query), false)
  }
  good.results[0].record = null; good.results[0].unavailable = true; good.collection.unavailable_count = 1
  assert.equal(validCurationPage(good, query), true)
  good.results[0].reason = '🌸'.repeat(2000); assert.equal(validCurationPage(good, query), true)
  good.results[0].reason += '🌸'; assert.equal(validCurationPage(good, query), false)
})

test('complete curation load follows all pages under one original version and rejects drift/duplicates/cancel', async () => {
  const calls = [], client = { get: async (path, { params }) => { calls.push(params); return { data: page({ ...params, listId: id(1) }, 3, 101) } } }
  const data = await loadCompleteCuration(client, id(1), 3, new AbortController().signal)
  assert.equal(data.rows.length, 101); assert.equal(calls.length, 2); assert.ok(calls.every(q => q.version === 3))
  await assert.rejects(loadCompleteCuration({ get: async (path, { params }) => ({ data: page({ ...params, listId: id(1) }, params.page === 2 ? 4 : 3, 101) }) }, id(1), 3, new AbortController().signal), /不完整/)
  await assert.rejects(loadCompleteCuration({ get: async (path, { params }) => { const data = page({ ...params, listId: id(1) }, 3, 101); if (params.page === 2) data.results[0] = entry(1); return { data } } }, id(1), 3, new AbortController().signal), /重复/)
  const stop = new AbortController(); stop.abort()
  await assert.rejects(loadCompleteCuration(client, id(1), 3, stop.signal), /取消/)
})

test('manual moves and changed fields preserve inputs and translate only changed cover choices', () => {
  const original = ['one','two','three']
  assert.deepEqual(moveCuration(original, 'three', 0), ['three','one','two']); assert.deepEqual(original, ['one','two','three'])
  assert.deepEqual(moveCuration(original, 'three', 99), original)
  assert.deepEqual(changedCurationFields({ title: '原名', cover_entry_id: id(1) }, { title: '新名', cover_entry_id: id(1) }), { title: '新名' })
  assert.deepEqual(changedCurationFields({ cover_entry_id: id(1) }, { cover_entry_id: '' }), { cover_entry_id: null })
})

test('latest read wins and late account results never become visible', async () => {
  let owner = 1; const first = deferred(), second = deferred(), pending = [first, second]
  const engine = createCurationController({ client: { get: () => pending.shift().promise }, getSession: () => owner })
  const a = engine.read({ search: 'a' }), b = engine.read({ search: 'b' })
  second.resolve({ data: page({}, 2) }); await b; first.resolve({ data: page({}, 1) }); await a
  assert.equal(engine.getSnapshot().data.version, 2)
  const last = deferred(), other = createCurationController({ client: { get: () => last.promise }, getSession: () => owner })
  const read = other.read(); owner = 2; last.resolve({ data: page() }); await read
  assert.equal(other.getSnapshot().data, null)
})

test('a conflicting page gets one coherent fresh first-page read with a notice', async () => {
  const queries = [], engine = createCurationController({ getSession: () => 1, client: { get: async (path, { params }) => {
    queries.push(params); if (queries.length === 1) throw { response: { status: 409 } }; return { data: page(params, 4, 15) }
  } } })
  assert.equal(await engine.read({ page: 2, version: 2 }), true)
  assert.equal(queries.length, 2); assert.equal(queries[1].page, 1); assert.equal(queries[1].version, undefined)
  assert.match(engine.getSnapshot().notice, /最新第一页/)
})

test('lost acknowledgement keeps the exact UUID/body, blocks new writes and confirms canonical retry', async () => {
  const writes = [], engine = createCurationController({ getSession: () => 1, uuid: () => id(9), client: { get: async () => ({ data: page() }), post: async (path, body) => {
    writes.push(body); if (writes.length === 1) throw new Error('connection lost'); return { data: { ...ack(body), replayed: true } }
  } } })
  await engine.read(); const body = { action: 'create', title: '雨天', version: 1 }
  assert.equal(await engine.submit(body), null); assert.deepEqual(engine.getSnapshot().pending, { ...body, operation_id: id(9) })
  assert.equal(await engine.submit(body), null); assert.equal(writes.length, 1)
  const result = await engine.submit(null, true)
  assert.equal(result.data.replayed, true); assert.deepEqual(writes[0], writes[1]); assert.equal(engine.getSnapshot().pending, null)
  assert.equal(engine.getSnapshot().needsRead, true)
})

test('version conflict requires rereading, while malformed success retains original operation for retry', async () => {
  let malformed = false
  const engine = createCurationController({ getSession: () => 1, uuid: () => id(9), client: { get: async () => ({ data: page() }), post: async () => { if (malformed) return { data: {} }; throw { response: { status: 409, data: { detail: '更新冲突' } } } } } })
  await engine.read(); await engine.submit({ action: 'create', title: 'x', version: 1 })
  assert.equal(engine.getSnapshot().conflict, true); assert.equal(engine.getSnapshot().pending, null)
  assert.equal(await engine.submit({}), null)
  await engine.read(); malformed = true; await engine.submit({ action: 'create', title: 'x', version: 1 })
  assert.equal(engine.getSnapshot().pending.operation_id, id(9))
})

test('receipts cannot change target collections, roll back versions or silently omit a live collection', () => {
  const body = { action: 'update', list_id: id(1), version: 3 }, good = ack(body)
  assert.equal(validCurationReceipt(good, body), true)
  assert.equal(validCurationReceipt({ ...good, version: 3 }, body), false)
  assert.equal(validCurationReceipt({ ...good, list_id: id(2) }, body), false)
  assert.equal(validCurationReceipt({ ...good, collection: null }, body), false)
  assert.equal(validCurationReceipt({ ...good, collection: null, replayed: true }, body), true)
})

test('a formerly populated later page returns to page one when the filtered collection becomes empty', async () => {
  const queries = [], engine = createCurationController({ getSession: () => 1, client: { get: async (path, { params }) => { queries.push(params); return { data: page(params, 1, 0) } } } })
  assert.equal(await engine.read({ page: 2, version: 1 }), true)
  assert.equal(engine.getSnapshot().data.page, 1); assert.equal(engine.getSnapshot().data.count, 0)
  assert.equal(queries.length, 2)
})
