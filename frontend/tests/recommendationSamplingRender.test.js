import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { createPreferenceEditor } from '../src/utils/recommendationPreferences.js'
import { preferences, previewData, profileData, itemData } from './fixtures/profileSampling.js'

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Panel } = await server.ssrLoadModule('/src/components/recommendations/RecommendationPreferencesPanel.jsx')
const { default: Evidence } = await server.ssrLoadModule('/src/components/recommendations/RecommendationEvidence.jsx')
const { RecommendationColdStartGuide: Guide, RecommendationTasteNote: Note } =
  await server.ssrLoadModule('/src/components/recommendations/RecommendationProfileGuide.jsx')
const state = (prefs = preferences()) => ({ status: 'editing', preferences: prefs, draftTags: [...prefs.tags], draftMinScore: prefs.profile_min_score,
  preview: { status: 'idle' } })
const render = (Component, props) => renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Component, props)))
const panel = (editor, props = {}) => render(Panel, { editor, profile: profileData(editor.preferences), ...props })

test('P2 workbench renders nine accessible choices inside existing three columns and one save footer', () => {
  const html = panel(state())
  assert.equal((html.match(/<option /g) || []).length, 9)
  assert.match(html, /for="rec-profile-min-score"/)
  assert.match(html, /id="rec-profile-min-score"[^>]*aria-describedby="rec-sampling-help"/)
  assert.match(html, /恢复默认 8.5/)
  assert.match(html, /不是 Bangumi 评分筛选/)
  assert.match(html, /不保证推荐更准确/)
  assert.match(html, /2. 我的作品画像/)
  assert.equal((html.match(/class="rec-preference-scroll"/g) || []).length, 3)
  assert.equal((html.match(/>保存喜好</g) || []).length, 1)
  assert.doesNotMatch(html, /高分作品画像|部高分作品/)
})

test('P2 unsaved preview and effective profile counts stay visibly separate without promoting readiness', () => {
  const editor = { ...state(), draftMinScore: '6.0', preview: { status: 'ready', data: previewData('6.0', preferences(), 4) } }
  const html = panel(editor, { profile: profileData(preferences(), 1) })
  assert.match(html, /未保存预览 · 个人评分 ≥ 6.0 分/)
  assert.match(html, /可分析样本 4 部 · 达到 3 部画像条件/)
  assert.match(html, /已生效画像：<\/strong>可分析样本 1 部/)
  assert.match(html, /不替换下方已生效画像/)
  assert.match(html, /根据你选择的题材推荐/)
})

test('P2 loading and preview failure do not invent zero and keep local recovery', () => {
  for (const status of ['debouncing', 'loading', 'error']) {
    const html = panel({ ...state(), draftMinScore: '7.0', preview: { status, error: status === 'error' ? '预览网络失败' : '' } })
    assert.doesNotMatch(html, /可分析样本 0/)
    assert.match(html, status === 'error' ? /重试预览/ : /正在预览可分析样本/)
    if (status === 'error') assert.match(html, /role="alert">预览网络失败/)
    assert.match(html, /已生效画像/)
  }
})

test('P2 disabled gate and older server remain honest about stored versus effective settings', () => {
  const disabled = panel(state(preferences('7.0', 1, ['日常'], false)))
  assert.match(disabled, /已保存 ≥ 7.0 分 · 当前生效 ≥ 8.5 分/)
  assert.match(disabled, /select[^>]*disabled=""/)
  assert.match(disabled, /画像门槛调整暂未开放/)
  const legacy = { contract_version: 'recommendations.3a.v1', tags: ['日常'], version: 1, available_tags: ['日常'] }
  const html = render(Panel, { editor: { status: 'editing', preferences: legacy, draftTags: ['日常'] } })
  assert.match(html, /当前服务尚未提供门槛设置/)
  assert.doesNotMatch(html, /<select|可分析样本 0/)
})

test('P2 native handlers change only local drafts and reset works without a bound receiver', () => {
  let writes = 0
  const editor = createPreferenceEditor({ savePreferences() { writes++ } }, () => {}, { setTimeout: () => 1, clearTimeout() {} })
  editor.open(preferences())
  const props = { editor: editor.getSnapshot(), onMinScore: editor.setMinScore, onResetMinScore: editor.resetMinScore }
  const tree = Panel(props)
  const find = (node, predicate) => {
    if (!node || typeof node !== 'object') return null
    if (predicate(node)) return node
    return React.Children.toArray(node.props?.children).map(child => find(child, predicate)).find(Boolean)
  }
  find(tree, node => node.type === 'select').props.onChange({ target: { value: '7.0' } })
  assert.equal(editor.getSnapshot().draftMinScore, '7.0')
  props.editor = editor.getSnapshot()
  find(Panel(props), node => node.type === 'button' && node.props.onClick === editor.resetMinScore).props.onClick()
  assert.equal(editor.getSnapshot().draftMinScore, '8.5')
  editor.close(); assert.equal(writes, 0)
})

test('P2 scalar conflicts expose explicit choices and current drafts at the existing footer', () => {
  const html = panel({ ...state(), status: 'conflict_review', scoreConflict: true, draftMinScore: '7.0',
    remotePreferences: preferences('9.0', 2) })
  assert.match(html, /最新门槛 ≥ 9.0 分；本页草稿 ≥ 7.0 分/)
  assert.match(html, /保留本页门槛并合并题材/)
  assert.match(html, /采用最新门槛并合并题材/)
  assert.match(html, /采用最新设置，放弃旧草稿/)
})

test('P2 low-threshold evidence and cold guides describe actual scores without high-score claims', () => {
  for (const score of ['6.0', '7.0', '8.0', '8.5']) {
    const html = render(Evidence, { item: itemData(score), expanded: true })
    assert.match(html, new RegExp(`你对《来源作品》的评分：<strong>${score} 分`))
    assert.ok(html.includes(`个人评分 ≥ ${score} 分`))
    assert.doesNotMatch(html, /来自高分作品|匹配率：|喜欢概率：/)
    const prefs = preferences(score), profile = profileData(prefs, 1)
    const guide = render(Guide, { profile, status: 'cold', preferencesReady: true })
    const note = render(Note, { profile, preferences: prefs, status: 'ready' })
    assert.ok(guide.includes(`个人评分 ≥ ${score} 分`))
    assert.ok(note.includes(`个人评分 ≥ ${score} 分`))
    assert.doesNotMatch(guide + note, /高分样本|高分记录|完整的高分/)
  }
})

test('P2 sampling CSS is namespaced, touchable and does not add another scrolling shell', () => {
  const css = readFileSync(new URL('../src/styles/recommendations-preferences.css', import.meta.url), 'utf8')
  assert.match(css, /\.memphis-rec-container \.rec-sampling-inputs select \{[^}]*min-height: 44px/)
  assert.match(css, /rec-sampling-inputs select:focus-visible/)
  assert.match(css, /@media \(max-width: 1099px\)/)
  assert.doesNotMatch(css, /rec-sampling[^}]*overflow-y: auto|position: fixed|!important/)
})
