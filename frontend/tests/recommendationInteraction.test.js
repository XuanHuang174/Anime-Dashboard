import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { createRecommendationDetailController } from '../src/utils/recommendationDetail.js'
import { createWantController } from '../src/utils/recommendationWant.js'
import { mapRecommendationItem } from '../src/utils/recommendations.js'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Card } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCard.jsx')
const { default: Actions } = await server.ssrLoadModule('/src/components/recommendations/RecommendationDetailActions.jsx')
const { default: ResourceBoundary } = await server.ssrLoadModule('/src/components/recommendations/RecommendationResourceBoundary.jsx')
const raw = (id = 123, changes = {}) => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: '完整作品 第二季',
  title_jp: 'Title', season: '2026-04', score: 7.5, tags: ['音乐'], reason: '', evidence: null,
  evidence_token: 'a'.repeat(64), watch_status: null, ...changes })
const item = mapRecommendationItem(raw())
const ack = (id = 123) => ({ contract_version: 'recommendations.want.v1', bangumi_id: id, anime_id: 9, record_id: 7,
  content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props))

test('7A cards keep exactly two action buttons; resource entry exists only in detail', () => {
  for (const hero of [false, true]) {
    const html = render(Card, { item: { ...item, watchStatus: 'completed' }, hero, onDetail() {}, onWant() {} })
    assert.match(html, /查看详情/); assert.match(html, /已看过/)
    assert.doesNotMatch(html, /寻找资源/)
    assert.equal((html.match(/<button/g) || []).length, 2)
  }
  const html = render(Actions, { item: { ...item, watchStatus: 'completed' }, resourceOpen: true,
    onClose() {}, onWant() {}, onResourceOpen() {}, onResourceClose() {} })
  assert.match(html, /寻找资源/); assert.match(html, /完整作品 第二季/); assert.match(html, /2026-04/)
  assert.match(html, /搜索关键词/); assert.match(html, /<input[^>]*placeholder=/)
  assert.match(html, /不代表已匹配正确季度或版本/)
})

test('a confirmed POST invalidates the WHOLE detail before standalone reload; duplicate clicks notify once', async () => {
  const post = deferred(), refresh = deferred(), calls = []
  const detail = createRecommendationDetailController({ detail: async (id, signal, token) => {
    calls.push({ id, token }); return calls.length === 1 ? raw() : refresh.promise
  } })
  await detail.open(item); detail.openResources()
  let notifications = 0, reload
  const want = createWantController({ want: () => post.promise }, result => {
    notifications++; reload = detail.afterWant(result)
  })
  const first = want.add(item), second = want.add(item)
  assert.equal(first, second)
  post.resolve(ack()); await first
  assert.equal(notifications, 1)
  assert.equal(detail.getSnapshot().status, 'refreshing')
  assert.equal(detail.getSnapshot().item, undefined)
  assert.equal(detail.getSnapshot().resourceOpen, false)
  assert.equal(calls[0].token, item.evidenceToken)
  assert.equal(calls[1].token, undefined) // ONLY a known successful acknowledgement establishes a new context.
  refresh.resolve(raw(123, { watch_status: 'plan', evidence_token: 'b'.repeat(64) })); await reload
  assert.equal(detail.getSnapshot().item.watchStatus, 'plan')
  assert.equal(detail.getSnapshot().item.evidenceToken, 'b'.repeat(64))
})

test('ordinary 409 never strips a token or retries automatically', async () => {
  const calls = []
  const detail = createRecommendationDetailController({ detail: async (...args) => {
    calls.push(args); throw { response: { status: 409 } }
  } })
  await detail.open(item); await detail.retry()
  assert.equal(detail.getSnapshot().snapshotChanged, true)
  assert.equal(detail.getSnapshot().item, undefined)
  assert.equal(calls.length, 1); assert.equal(calls[0][2], item.evidenceToken)
})

test('failed refresh preserves confirmed add and GET retry never performs another POST', async () => {
  let calls = 0, posts = 0, refresh
  const detail = createRecommendationDetailController({ detail: async () => {
    if (++calls === 2) throw new Error('offline')
    return raw(123, { watch_status: calls > 1 ? 'plan' : null })
  } })
  await detail.open(item)
  const want = createWantController({ want: async () => { posts++; return ack() } }, result => { refresh = detail.afterWant(result) })
  await want.add(item); await refresh
  assert.equal(want.getSnapshot()[123].status, 'ready')
  assert.equal(detail.getSnapshot().status, 'error')
  assert.match(detail.getSnapshot().message, /加入操作已确认，但详情更新失败/)
  assert.equal(detail.getSnapshot().item, undefined)
  await detail.retry(); assert.equal(posts, 1)
  assert.equal(detail.getSnapshot().item.watchStatus, 'plan')
})

test('callback failure cannot turn a successful POST into failed add, including asynchronous failure', async () => {
  for (const callback of [() => { throw new Error('sync failed') }, async () => { throw new Error('async failed') }]) {
    const want = createWantController({ want: async () => ack() }, callback)
    assert.equal((await want.add(item)).created, true)
    await Promise.resolve()
    assert.equal(want.getSnapshot()[123].status, 'ready')
    assert.match(want.getSnapshot()[123].syncWarning, /加入操作已确认/)
    assert.equal(want.getSnapshot()[123].error, undefined)
  }
})

test('failed add and identity conflicts do not mutate or disable an already loaded detail/resource session', async () => {
  for (const code of ['identity_confirmation_required', 'capacity_exceeded', 'retry']) {
    const detail = createRecommendationDetailController({ detail: async () => raw() })
    await detail.open(item); detail.openResources()
    const before = detail.getSnapshot()
    const want = createWantController({ want: async () => { throw { response: { status: 409, data: { code } } } } }, detail.afterWant)
    await want.add(item)
    assert.equal(detail.getSnapshot(), before)
    assert.equal(detail.getSnapshot().resourceOpen, true)
  }
})

test('late old detail, closed selection and different subjects never replace current selection', async () => {
  const slow = deferred(), calls = []
  const detail = createRecommendationDetailController({ detail: async (id, signal, token) => {
    calls.push({ id, token, signal }); return calls.length === 1 ? slow.promise : raw(id)
  } })
  const pending = detail.open(item)
  await detail.open(mapRecommendationItem(raw(124)))
  await detail.afterWant(ack(123)) // mutation of A refreshes selected B, not A.
  assert.equal(detail.getSnapshot().item.bangumiId, 124)
  slow.resolve(raw()); await pending
  assert.equal(detail.getSnapshot().item.bangumiId, 124)
  assert.ok(calls[0].signal.aborted)
  detail.close(); await detail.afterWant(ack())
  assert.equal(detail.getSnapshot().status, 'closed')
  assert.equal(calls.length, 3)
})

test('invalid acknowledgements or mismatched detail identities fail closed; controllers are user-local', async () => {
  const first = createRecommendationDetailController({ detail: async () => raw(124) })
  const second = createRecommendationDetailController({ detail: async () => raw() })
  await first.open(item)
  assert.equal(first.getSnapshot().status, 'error')
  assert.equal(second.getSnapshot().status, 'closed')
  assert.throws(() => first.afterWant({ ...ack(), content_rating: 'adult' }))
  second.openResources(); assert.equal(second.getSnapshot().status, 'closed')
})

test('want errors leave the detail resource entry available without changing card action layout', () => {
  const html = render(Actions, { item, resourceOpen: false, onClose() {}, onWant() {}, onIdentity() {},
    onResourceOpen() {}, onResourceClose() {}, operation: { error: { code: 'identity_confirmation_required', message: '核对身份后重试' } } })
  assert.match(html, /核对旧记录/); assert.match(html, /寻找资源/)
  assert.doesNotMatch(html, /class="rec-button rec-resource-trigger"[^>]*disabled=/)
})

test('resource render failure has a local recovery boundary instead of the global recommendation error state', () => {
  const boundary = new ResourceBoundary({ children: React.createElement('span', null, '资源内容') })
  boundary.state = ResourceBoundary.getDerivedStateFromError(new Error('malformed resource response'))
  const html = renderToStaticMarkup(boundary.render())
  assert.match(html, /资源区暂不可用/); assert.match(html, /重试资源区/)
  assert.match(html, /推荐和手账记录不受影响/)
  assert.doesNotMatch(html, /malformed resource|重新加载推荐|空手账/)
})
