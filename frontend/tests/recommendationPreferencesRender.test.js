import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Panel } = await server.ssrLoadModule('/src/components/recommendations/RecommendationPreferencesPanel.jsx')
const { RecommendationRefreshNotice } = await server.ssrLoadModule('/src/components/recommendations/RecommendationProfileGuide.jsx')
const topics = ['日常', '音乐', '科幻', '治愈', '校园', '喜剧', '百合', '恋爱', '冒险']
const profile = { cold_start: false, sample_policy: { minimum_score: '8.5' }, extraction: {
  trusted_profile: { usable_work_count: 3, tag_summary: [{ tag: '音乐', sample_count: 3 }] },
  common_topics: { requires_confirmation: true, used_for_recommendation: false,
    tag_summary: topics.map((tag, i) => ({ tag, sample_count: 9 - i })) } } }
const editor = { status: 'editing', draftTags: ['日常'], preferences: { tags: ['日常'], version: 1, available_tags: topics } }
const render = (props = {}) => renderToStaticMarkup(React.createElement(Panel, { profile, editor,
  onTag() {}, onSave() {}, onClear() {}, onClose() {}, onReloadLatest() {}, onResolveConflict() {}, ...props }))

test('actual JSX renders three distinct blocks, readonly evidence and only eight observed topics', () => {
  const html = render()
  assert.match(html, /我的明确偏好/); assert.match(html, /高分作品画像/); assert.match(html, /手账常见题材/)
  assert.match(html, /参考高分作品，并结合你的明确偏好/)
  assert.match(html, /可分析样本 3 部/); assert.match(html, /个人评分 ≥ 8.5/)
  assert.match(html, /你的手账中有 9 部作品包含/)
  assert.doesNotMatch(html, /你的手账中有 1 部作品包含/)
  const trusted = html.split('id="rec-trusted-title"')[1].split('id="rec-observed-title"')[0]
  assert.doesNotMatch(trusted, /type="checkbox"/)
  assert.equal((html.match(/checked=""/g) || []).length, 1)
  assert.match(html, /点击只加入草稿，不自动保存/)
  assert.match(html, /不是硬性过滤/)
})

test('cold-start explanation and unavailable read-only data remain honest', () => {
  assert.match(render({ profile: { ...profile, cold_start: true } }), /根据你选择的题材推荐/)
  const html = render({ profile: undefined })
  assert.match(html, /画像摘要暂不可用/); assert.match(html, /观察摘要暂不可用/)
  assert.doesNotMatch(html, /可分析样本 0|加入我的偏好/)
  const noInference = render({ profile: { ...profile, extraction: { ...profile.extraction,
    common_topics: { ...profile.extraction.common_topics, used_for_recommendation: true } } } })
  assert.doesNotMatch(noInference, /你的手账中有/)
})

test('busy/conflict state blocks writes while retaining explicit recovery actions', () => {
  assert.match(render({ editor: { ...editor, status: 'saving' } }), /正在保存/)
  const conflict = render({ editor: { ...editor, status: 'conflict', error: '其他页面已更新' } })
  assert.match(conflict, /读取最新设置，保留草稿/); assert.match(conflict, /role="alert"/)
  const review = render({ editor: { ...editor, status: 'conflict_review', remotePreferences: { tags: ['科幻'] } } })
  assert.match(review, /合并本次修改到草稿/); assert.match(review, /采用最新设置，放弃旧草稿/)
})

test('saved-but-refresh-failed has a read retry, never a misleading save failure', () => {
  const html = renderToStaticMarkup(React.createElement(RecommendationRefreshNotice, {
    confirmedPreferences: { tags: ['音乐'], version: 2 }, status: 'error', onRetry() {},
  }))
  assert.match(html, /偏好已保存，推荐更新失败/); assert.match(html, /重试更新推荐/)
  assert.doesNotMatch(html, /保存失败|重试保存/)
})
