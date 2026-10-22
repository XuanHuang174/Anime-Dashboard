import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPublicationClient, createPublicationController, validatePublicationList, PUBLICATION_VERSION } from '../src/utils/showcasePublication.js'

const item = { record_id: 7, anime_id: 9, bangumi_id: 331445, title: '少女手工', season: '2022-10',
  requires_confirmation: true, ticket: 'signed-owner-token', detail: '需要确认' }
const list = (items = [item]) => ({ contract_version: PUBLICATION_VERSION, can_publish: true, count: items.length, results: items, next_page: null })
const ack = { contract_version: PUBLICATION_VERSION, record_id: 7, anime_id: 9, status: 'published' }
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }

test('dedicated owner confirmation uses record URL and signed proof, never journal editing', async () => {
  const calls = []
  const client = createPublicationClient({ get: async (...args) => { calls.push(args); return { data: list() } },
    post: async (...args) => { calls.push(args); return { data: ack } } })
  await client.pending(1)
  await client.publish(item)
  assert.equal(calls[0][0], 'records/showcase-publication/')
  assert.equal(calls[1][0], 'records/7/publish-showcase/')
  assert.deepEqual(calls[1][1], { confirm_publication: true, ticket: item.ticket })
})

test('list validation rejects duplicates, wrong owner capability, absent proof, and malformed IDs', () => {
  assert.equal(validatePublicationList(list()).count, 1)
  for (const data of [{ ...list(), can_publish: false }, list([item, item]), list([{ ...item, ticket: null }]),
    list([{ ...item, record_id: '7' }]), { ...list(), contract_version: 'unknown' }]) assert.throws(() => validatePublicationList(data))
})

test('reviewed adult publication uses the same explicit confirmation while unavailable items cannot submit', async () => {
  const adult = { ...item, content_rating: 'adult', code: 'showcase_confirmation_required' }
  const blocked = { ...item, record_id: 8, content_rating: 'unavailable', code: 'showcase_review_required',
    requires_confirmation: false, ticket: null, detail: '需要有效作品核验' }
  assert.equal(validatePublicationList(list([adult, blocked])).count, 2)
  assert.throws(() => validatePublicationList(list([{ ...adult, content_rating: 'anything' }])))
  let writes = 0
  const controller = createPublicationController({ pending: async () => list([adult, blocked]),
    publish: async () => { writes++; return ack } })
  await controller.read(); await controller.publish(blocked)
  assert.equal(writes, 0)
  await controller.publish(adult); assert.equal(writes, 1)
})

test('publication content failure explains the exact blocker without suggesting an administrative bypass', async () => {
  const controller = createPublicationController({ pending: async () => list(), publish: async () => {
    throw { response: { status: 403, data: { code: 'showcase_review_required' } } }
  } })
  await controller.read(); await controller.publish(item)
  assert.match(controller.getSnapshot().error, /有效的一般成人内容审核/)
  assert.doesNotMatch(controller.getSnapshot().error, /联系管理员/)
})

test('one write while clicking quickly; successful acknowledgement refreshes rather than creating records', async () => {
  const request = deferred(); let writes = 0, reads = 0
  const controller = createPublicationController({ pending: async () => ++reads === 1 ? list() : list([]),
    publish: () => { writes++; return request.promise } })
  await controller.read()
  const first = controller.publish(item)
  await controller.publish(item)
  await Promise.resolve()
  assert.equal(writes, 1)
  request.resolve(ack); await first
  assert.equal(controller.getSnapshot().count, 0)
  assert.equal(controller.getSnapshot().saved, true)
})

test('saved-but-refresh-failed stays saved; retry performs GET only', async () => {
  let reads = 0, writes = 0
  const controller = createPublicationController({ pending: async () => {
    reads++; if (reads === 2) throw Error('offline'); return reads === 1 ? list() : list([])
  }, publish: async () => { writes++; return ack } })
  await controller.read(); await controller.publish(item)
  assert.match(controller.getSnapshot().error, /公开已完成，列表更新失败/)
  await controller.read()
  assert.equal(writes, 1)
  assert.equal(controller.getSnapshot().error, '')
})

test('failed/409 confirmation keeps work; prevents repeat write until reread and explicit confirmation', async () => {
  let writes = 0
  const controller = createPublicationController({ pending: async () => list(), publish: async () => {
    writes++; throw { response: { status: 409 } }
  } })
  await controller.read(); await controller.publish(item); await controller.publish(item)
  assert.equal(writes, 1)
  assert.equal(controller.getSnapshot().count, 1)
  assert.match(controller.getSnapshot().error, /重新读取/)
})

test('older list responses and disposed write acknowledgements cannot pollute a new session', async () => {
  const old = deferred(), newer = deferred(); let reads = 0
  const controller = createPublicationController({ pending: () => ++reads === 1 ? old.promise : newer.promise })
  const a = controller.read(), b = controller.read()
  newer.resolve(list([])); await b
  old.resolve(list()); await a
  assert.equal(controller.getSnapshot().count, 0)
  let current = true
  const save = deferred()
  const second = createPublicationController({ pending: async () => list(), publish: () => save.promise }, () => current)
  await second.read(); const writing = second.publish(item)
  await Promise.resolve(); current = false; second.dispose(); save.resolve(ack); await writing
  assert.equal(second.getSnapshot().saved, false)
})

test('normal users never get a management panel or repeated capability lookups', async () => {
  let calls = 0
  const controller = createPublicationController({ pending: async () => { calls++; return { ...list([]), can_publish: false } } })
  await controller.read(); await controller.read()
  assert.equal(calls, 1)
  assert.equal(controller.getSnapshot().canPublish, false)
})

test('both adding entry pages expose pending management; no nested modal or default approval', () => {
  const ui = readFileSync(new URL('../src/components/ShowcasePublicationPanel.jsx', import.meta.url), 'utf8')
  const dashboard = readFileSync(new URL('../src/pages/Dashboard.jsx', import.meta.url), 'utf8')
  const rec = readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8')
  assert.match(ui, /type="checkbox"/)
  assert.match(ui, /setSelected\(\{\}\)/)
  assert.match(ui, /评分、长评、观看状态及标签/)
  assert.match(ui, /<details/)
  assert.doesNotMatch(ui, /showModal|localStorage\.setItem|dangerouslySetInnerHTML/)
  assert.match(dashboard, /ShowcasePublicationPanel refreshKey=\{recordsRefreshKey\}/)
  assert.match(rec, /showcase_publication\?\.status === 'review_required'/)
})
