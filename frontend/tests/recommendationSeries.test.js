import test from 'node:test'
import assert from 'node:assert/strict'
import { validateSeries, validateSeriesDetail, seriesStatus, seriesBackground } from '../src/utils/recommendationSeries.js'
import { createSeriesWorkspace } from '../src/utils/seriesWorkspace.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { seriesPage, seriesDetail } from './fixtures/seriesContract.js'
import { adultScope } from './fixtures/explorationContract.js'
import { lockDialogBody } from '../src/utils/dialogBodyLock.js'

const gate = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const conflict = () => ({ response: { status: 409, data: { code: 'series_snapshot_changed' } } })
const result = id => ({ contract_version: 'recommendations.want.v1', bangumi_id: id, anime_id: 19, record_id: 24, created: true, outcome: 'created', watch_status: 'plan', content_rating: 'non_adult' })
const client = (overrides = {}) => ({ series: async () => seriesPage(), seriesDetail: async (_, id) => seriesDetail(seriesPage(), id), seriesWant: async (_, id) => result(id), ...overrides })
const workspace = api => createSeriesWorkspace({ client: api || client(), source: 1, scope: 'safe' })

test('series validates direction, sections, release axes, own states and missing-data semantics', () => {
  const page = seriesPage()
  assert.equal(validateSeries(page, 1), page)
  assert.equal(validateSeriesDetail(seriesDetail(page), page, 2, 'safe').bangumiId, 2)
  assert.equal(seriesStatus(page.results[0]), '无已确认记录')
  for (const patch of [{ depth: 2 }, { ordering: 'watch_order' }, { coverage: 'independent' }, { context: '' }, { series_revision: -1 },
    { content_scope_context: undefined }, { selected_seed: { ...page.selected_seed, bangumi_id: 99 } }]) assert.throws(() => validateSeries({ ...page, ...patch }, 1))
  for (const patch of [{ section: 'related' }, { relation_label: '必看续作' }, { kind: '__proto__', relation_label: undefined, section: undefined }, { bangumi_id: 1 }, { release_precision: 'day' },
    { record_state: 'no_confirmed_record', watch_status: 'completed' }, { can_add_from_series: false }]) {
    const data = structuredClone(page); Object.assign(data.results[0], patch); assert.throws(() => validateSeries(data, 1))
  }
  assert.throws(() => validateSeries({ ...page, results: [page.results[0], page.results[0]] }, 1))
  assert.throws(() => validateSeriesDetail(seriesDetail(page, 4), { ...page, results: [] }, 4, 'safe'))
  assert.equal(validateSeries(seriesPage({ rows: false, coverage: 'empty_observed' }), 1).results.length, 0)
})

test('series details enforce scope and blank adult covers independently of media', () => {
  const page = { ...seriesPage(), content_scope_context: adultScope }, detail = seriesDetail(page)
  detail.item.content_rating = 'adult'; detail.item.cover_url = 'https://unsafe.example/cover.jpg'
  assert.throws(() => validateSeriesDetail(detail, page, 2, 'safe'))
  assert.equal(validateSeriesDetail(detail, page, 2, 'include_adult').coverUrl, '')
  detail.content_scope_context = { ...adultScope, scope_valid_until: '2000-01-01' }
  assert.throws(() => validateSeriesDetail(detail, page, 2, 'include_adult'))
})

test('late detail, closed selection and account change cannot refill current state', async () => {
  const a = gate(), b = gate(), controller = workspace(client({ seriesDetail: (_, id) => id === 2 ? a.promise : b.promise }))
  await controller.load()
  const first = controller.select(2), second = controller.select(3)
  b.resolve(seriesDetail(seriesPage(), 3)); await second
  a.resolve(seriesDetail(seriesPage(), 2)); await first
  assert.equal(controller.getSnapshot().detail.target, 3)
  const c = gate(), api = client({ series: () => c.promise })
  let current = true
  const other = createSeriesWorkspace({ client: api, source: 1, scope: 'safe', isCurrent: () => current })
  const pending = other.load(); current = false; c.resolve(seriesPage()); await pending
  assert.equal(other.getSnapshot().data, null)
  controller.dispose(); other.dispose()
})

test('series revision loss clears rows, detail, pending writes and rejects stale responses', async () => {
  const late = gate(), api = client({ seriesWant: () => late.promise }), controller = workspace(api)
  await controller.load(); await controller.select(2)
  const write = controller.want(2)
  api.series = async () => { throw conflict() }
  await controller.load(true)
  late.resolve(result(2)); await write
  assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(controller.getSnapshot().data, null)
  assert.equal(controller.getSnapshot().detail, null)
  assert.deepEqual(controller.getSnapshot().operations, {})
})

test('want deduplicates concurrent requests and records only validated acknowledgements', async () => {
  const ack = gate(); let calls = 0, confirmed = 0
  const controller = createSeriesWorkspace({ source: 1, scope: 'safe', client: client({ seriesWant: () => { calls++; return ack.promise } }), onConfirmed: () => { confirmed++; throw Error('UI failed') } })
  await controller.load(); await controller.select(2)
  const first = controller.want(2); await controller.want(2)
  assert.equal(calls, 1); ack.resolve(result(2)); await first
  assert.equal(confirmed, 1)
  assert.equal(controller.getSnapshot().data.results[0].watch_status, 'plan')
  assert.equal(controller.getSnapshot().detail.item.watchStatus, 'plan')
  assert.equal(controller.getSnapshot().operations[2].status, 'ready')
})

test('unconfirmed writes remain retryable, identity review blocks guessing, StrictMode disposal is reversible', async () => {
  let calls = 0
  const api = client({ seriesWant: async () => { calls++; if (calls === 1) throw Error('timeout'); return result(2) } }), controller = workspace(api)
  controller.dispose(); controller.activate(); await controller.load()
  await controller.want(2)
  assert.equal(controller.getSnapshot().data.results[0].watch_status, null)
  assert.equal(controller.getSnapshot().operations[2].error.code, 'retry')
  await controller.want(2)
  assert.equal(controller.getSnapshot().operations[2].status, 'ready')
  const blocked = workspace(client({ series: async () => { const page = seriesPage(); Object.assign(page.results[0], { record_state: 'needs_review', can_add_from_series: false }); return page },
    seriesWant: () => { throw Error('must never write') } }))
  await blocked.load(); await blocked.want(2)
  assert.deepEqual(blocked.getSnapshot().operations, {})
})

test('client preserves adult intent for series and distinguishes series revisions from scope revocation', async () => {
  const calls = []; let lost = 0
  const transport = { get: async (path, config) => { calls.push([path, config]); return { data: { ...seriesPage(), content_scope_context: adultScope } } } }
  const api = createRecommendationClient(transport, 'include_adult', () => lost++)
  await api.series(1, 'context')
  assert.equal(calls[0][1].params.content_scope, 'include_adult')
  transport.get = async () => { throw conflict() }
  await assert.rejects(api.series(1, 'context')); assert.equal(lost, 0)
  transport.get = async () => { throw { response: { status: 409, data: { code: 'content_scope_changed' } } } }
  await assert.rejects(api.series(1, 'context')); assert.equal(lost, 1)
  assert.throws(() => api.series(-1)); assert.throws(() => api.seriesDetail(1, 2, ''))
})

test('return context accepts only existing private exploration routes', () => {
  for (const path of ['/dashboard', '/recommendations', '/recommendations/', '/recommendations/similar', '/recommendations/tags']) {
    const location = { pathname: path, search: '?seed=42', state: { journalRecord: 9 }, key: 'original' }
    assert.equal(seriesBackground(location), location)
  }
  for (const pathname of ['https://evil.test', '/shared/XHcode', '/admin-dashboard', '/recommendations/series/2']) assert.equal(seriesBackground({ pathname }), null)
})

test('nested dialogs restore body scrolling in either unmount order, including journal preview', () => {
  for (const order of [[0, 1, 2], [2, 0, 1], [1, 2, 0]]) {
    const document = { body: { style: { overflow: 'auto' } } }
    const release = [lockDialogBody(document), lockDialogBody(document), lockDialogBody(document)]
    release[order[0]](); release[order[0]]()
    assert.equal(document.body.style.overflow, 'hidden')
    release[order[1]](); assert.equal(document.body.style.overflow, 'hidden')
    release[order[2]](); assert.equal(document.body.style.overflow, 'auto')
  }
})
