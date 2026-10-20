import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationBrowseControls: Controls, RecommendationBatchActions: Batch } = await server.ssrLoadModule('/src/components/recommendations/RecommendationBrowseControls.jsx')
const { RecommendationTasteNote: Taste } = await server.ssrLoadModule('/src/components/recommendations/RecommendationProfileGuide.jsx')
const { RecommendationSkeleton: Skeleton } = await server.ssrLoadModule('/src/components/recommendations/RecommendationStates.jsx')
const { default: Card } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCard.jsx')
const render = (component, props = {}) => renderToStaticMarkup(React.createElement(component, props))
const filters = { type: 'TV', minScore: '7', yearFrom: '2015', yearTo: '2026', hideRecorded: true }

test('R1 controls keep actual filter chips but fold explanation into a closed native disclosure', () => {
  const html = render(Controls, { category: 'all', style: 'balanced', filters, algorithmVersion: 'content.v1' })
  assert.match(html, /为你发现/); assert.match(html, /电波契合/); assert.match(html, /换一种口味/)
  assert.match(html, /aria-label="移除条件：2015—2026"/)
  assert.match(html, /aria-label="移除条件：Bangumi 评分 ≥ 7.0"/)
  const help = html.slice(html.indexOf('<details'))
  assert.match(help, /^<details class="rec-browse-help"><summary>浏览说明/)
  assert.match(help, /不代表喜欢概率/); assert.match(help, /不会覆盖已有评分/)
  assert.match(help, /推荐 V1/)
  assert.doesNotMatch(html.slice(0, html.indexOf('<details')), /不代表喜欢概率|保持当前 V1 排序/)
  const legacy = render(Controls, { category: 'all', style: 'balanced', filters, legacy: true })
  assert.doesNotMatch(legacy, /aria-label="推荐风格"/)
})

test('R1 taste note separates real counts and keeps old identity review accessible without a page-wide banner', () => {
  const html = render(Taste, { status: 'ready', preferences: { tags: ['百合'] }, profile: {
    usable_sample_count: 63, cold_start: false, tag_summary: ['日常', '喜剧', '百合'].map(tag => ({ tag })),
    diagnostics: { pending_identity_count: 14, high_score_record_count: 77 } } })
  assert.match(html, /<dt>可分析样本<\/dt><dd>63 部/)
  assert.match(html, /<dt>自动识别题材<\/dt><dd>3 个/)
  assert.match(html, /<dt>手动偏好<\/dt><dd>1 个/)
  assert.match(html, /核对旧作品 →/)
  assert.match(html, /<details class="rec-profile-notes"><summary>画像说明/)
  assert.match(html, /手账已有 77 条符合取样门槛/)
  assert.doesNotMatch(html, /rec-identity-notice/)
})

test('R1 loading/error taste note hides stale metrics and never invents zero values', () => {
  for (const status of ['loading', 'error']) {
    const html = render(Taste, { status, profile: { usable_sample_count: 99 }, confirmedPreferences: { tags: ['音乐'] } })
    assert.doesNotMatch(html, /rec-taste-metrics|99 部|0 部|0 个/)
    assert.match(html, /手动偏好已保存 1 个/)
    assert.match(html, status === 'error' ? /重试读取/ : /正在读取你的真实喜好/)
  }
})

test('R1 featured skeleton does not render a second fake grid; batch footer retains exact recovery semantics', () => {
  assert.doesNotMatch(render(Skeleton, { featuredOnly: true }), /class="rec-grid"/)
  assert.match(render(Skeleton), /class="rec-grid"/)
  const state = { items: Array(12), data: { batch: 1, batch_count: 24, next_page_cursor: 'p', next_batch_cursor: 'b' } }
  const html = render(Batch, { state })
  assert.ok(html.indexOf('第 1 批') < html.indexOf('加载本批更多'))
  assert.match(html, /换一批，看看其他作品 →/)
  assert.match(render(Batch, { state: { ...state, batchError: '合成错误' } }), /重试原目标批次 →/)
  assert.match(render(Batch, { state: { ...state, snapshotExpired: true } }), /浏览依据已更新，请重新加载推荐。/)
})

test('R1 hero is honestly labelled for the current batch and does not add empty synopsis spacing', () => {
  const html = render(Card, { hero: true, item: { title: '合成作品', bangumiId: 101, score: 7.5, tags: ['音乐'] } })
  assert.match(html, /本批首推/)
  assert.doesNotMatch(html, /今日精选|本期主推|rec-hero-synopsis/)
  assert.match(html, /Bangumi 评分/); assert.match(html, /查看详情/); assert.match(html, /加入想看/)
})
