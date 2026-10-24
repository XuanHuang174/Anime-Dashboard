import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { emptyTagDraft } from '../src/utils/explorationTagQuery.js'
import { readFileSync } from 'node:fs'

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Chips } = await server.ssrLoadModule('/src/components/recommendations/ExplorationTagChips.jsx')
const { default: Editor } = await server.ssrLoadModule('/src/components/recommendations/ExplorationTagEditor.jsx')
const { RecommendationRouteLoading: Loading } = await server.ssrLoadModule('/src/components/recommendations/RecommendationLoading.jsx')
const descriptor = JSON.parse(readFileSync(new URL('./fixtures/recommendationExplorationContract.json', import.meta.url)))
const render = (component, props, path = '/recommendations/tags') => renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [path] }, React.createElement(component, props)))

test('only exact standard labels receive exploration actions; arbitrary private labels stay text', () => {
  const html = render(Chips, { tags: ['日常', '私人评分9分', '轩皇力推'], standardTags: descriptor.available_tags })
  assert.match(html, /探索这个题材：日常/)
  assert.doesNotMatch(html, /探索这个题材：私人评分|探索这个题材：轩皇/)
  assert.match(html, /<span>私人评分9分<\/span>/)
  assert.equal((html.match(/<button/g) || []).length, 1)
})

test('editor communicates draft, full catalog counts, hard exclusion limits and explicit execution', () => {
  const html = render(Editor, { draft: { ...emptyTagDraft(), include: ['音乐'], exclude: ['战斗'] }, onChange() {}, onApply() {}, status: 'ready',
    options: { tags: descriptor.available_tags.map(tag => ({ tag, count: 0 })), count_description: '范围内目录数量，不扣除隐藏记录。', exclusion_description: '按已有标注筛选。' } })
  for (const copy of ['草稿', '开始探索', '同时满足全部', '满足任一', 'Bangumi 评分优先', '排除题材', '包含题材', '重置草稿', '普通浏览范围', '0 部']) assert.ok(html.includes(copy), copy)
  assert.doesNotMatch(html, /保存偏好|喜欢概率|我的口味/)
  assert.match(render(Loading, {}, '/recommendations/tags?include=音乐'), /探索条件/)
  assert.doesNotMatch(render(Loading, {}, '/recommendations/tags'), /我的口味|本次参照/)
})
