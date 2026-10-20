import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

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
  assert.match(html, /我的明确偏好/); assert.match(html, /我的作品画像/); assert.match(html, /手账常见题材/)
  assert.match(html, /参考符合取样条件的作品，并结合你的明确偏好/)
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

test('workbench keeps three named keyboard scroll regions before a single save footer', () => {
  const html = render()
  assert.match(html, /class="rec-preference-columns"/)
  for (const name of ['我的明确偏好内容', '我的作品画像内容', '手账常见题材内容']) {
    assert.ok(html.includes(`role="region" aria-label="${name}" tabindex="0"`))
  }
  assert.equal((html.match(/class="rec-preference-scroll"/g) || []).length, 3)
  assert.equal((html.match(/>保存喜好</g) || []).length, 1)
  const footer = html.slice(html.indexOf('<footer'))
  assert.match(footer, /草稿已选 <strong>1<\/strong> \/ 20/)
  assert.match(footer, /取消，不保存/)
  assert.ok(html.indexOf('rec-observed-title') < html.indexOf('<footer'))
})

test('all recovery states stay outside column scrolling and retain drafts', () => {
  for (const status of ['error', 'conflict', 'uncertain', 'conflict_review', 'saved_refresh_failed']) {
    const html = render({ editor: { ...editor, status, error: '隔离测试错误',
      remotePreferences: { tags: ['科幻'] } } })
    const footer = html.slice(html.indexOf('<footer'))
    assert.match(footer, /隔离测试错误/); assert.match(footer, /role="alert"/)
    assert.match(footer, /草稿已选 <strong>1/)
    assert.equal((html.match(/checked=""/g) || []).length, 1)
  }
})

test('workbench max-tag limit disables only new choices, observed actions and saving lock as before', () => {
  const available = Array.from({ length: 23 }, (_, i) => `题材${i}`)
  const html = render({ editor: { ...editor, draftTags: available.slice(0, 20),
    preferences: { tags: [], version: 1, available_tags: available } } })
  assert.equal((html.match(/checked=""/g) || []).length, 20)
  assert.equal((html.match(/type="checkbox" disabled=""/g) || []).length, 3)
  const saving = render({ editor: { ...editor, status: 'saving' } })
  assert.match(saving, /fieldset class="rec-taste-options" disabled=""/)
  assert.match(saving, /disabled="">正在保存…/)
})

test('workbench layout is scoped to preferences; desktop columns and touch single-scroll are separate', () => {
  const css = readFileSync(new URL('../src/styles/recommendations-preferences.css', import.meta.url), 'utf8')
  assert.match(css, /grid-template-columns: minmax\(0, 1\.2fr\) repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(css, /@media \(max-width: 1099px\)/)
  assert.match(css, /rec-preference-scroll \{ overflow: visible/)
  assert.match(css, /rec-preference-footer \{ flex-shrink: 0/)
  assert.match(css, /safe-area-inset-bottom/)
  assert.doesNotMatch(css, /\.rec-dialog--detail|!important|position: fixed|animation:|transition:/)
  assert.match(readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8'), /import '..\/styles\/recommendations-preferences.css'/)
})
