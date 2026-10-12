import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'

// Compile the actual JSX with the installed toolchain, not a duplicate fixture.
// Middleware/disabled HMR means this test opens no browser/listening server.
const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationActions, default: RecommendationCard } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCard.jsx')
const item = { bangumiId: 123, title: '真实作品', originalTitle: 'Title', score: 8.7, tags: ['日常'],
  coverUrl: 'https://external.invalid/raw.jpg', watchStatus: null }
const actions = props => renderToStaticMarkup(React.createElement(RecommendationActions, {
  item, onDetail() {}, onWant() {}, onIdentity() {}, ...props,
}))

test('actual hero/grid JSX keeps missing-cover placeholder and exposes real want button', () => {
  for (const hero of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(RecommendationCard, { item, hero, onDetail() {}, onWant() {} }))
    assert.match(html, /暂无托管封面/)
    assert.doesNotMatch(html, /external\.invalid|待开放/)
    assert.match(html, /\+ 加入想看/)
  }
})

test('actual card/detail actions reflect pending and all existing statuses', () => {
  assert.match(actions({ operation: { status: 'adding' } }), /正在加入/)
  for (const [status, label] of Object.entries({ plan: '已在想看', watching: '在看中', completed: '已看过', on_hold: '已搁置' })) {
    const html = actions({ operation: { status: 'ready', watchStatus: status }, detailLabel: '关闭详情' })
    assert.match(html, new RegExp(label)); assert.match(html, /disabled=""/)
    assert.match(html, /关闭详情/)
  }
})

test('actual retry and identity confirmation actions include visible error explanation', () => {
  const retry = actions({ operation: { status: 'error', error: { code: 'retry', message: '请重试，已有长评仍完整保留' } } })
  assert.match(retry, /重试加入想看/); assert.match(retry, /role="alert"/)
  assert.match(retry, /长评仍完整保留/)
  const confirmation = actions({ operation: { error: { code: 'identity_confirmation_required', message: '请核对原名与季度' } } })
  assert.match(confirmation, /核对旧记录/); assert.match(confirmation, /原名与季度/)
})
