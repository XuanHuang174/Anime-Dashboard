import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { REC_CATEGORIES } from '../src/utils/recommendations.js'

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Banner } = await server.ssrLoadModule('/src/components/JournalRecommendationBanner.jsx')
const { default: Header } = await server.ssrLoadModule('/src/components/Header.jsx')
const { default: Drawer } = await server.ssrLoadModule('/src/components/AnimeResourceDrawer.jsx')
const { default: Stats } = await server.ssrLoadModule('/src/components/StatsSummary.jsx')
const { default: Guide } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCategoryGuide.jsx')
const render = (component, props = {}) => renderToStaticMarkup(React.createElement(MemoryRouter, {}, React.createElement(component, props)))
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('familiar category changes label and explanation, not wire identity or category order', () => {
  assert.deepEqual(REC_CATEGORIES.map(row => row.id), ['all', 'familiar', 'explore'])
  const html = render(Guide, { category: 'familiar', mode: 'personalized' })
  assert.match(html, /<strong>电波契合<\/strong>/); assert.match(html, /已有口味电波契合/)
  assert.doesNotMatch(html, /熟悉的心动/)
})

test('loaded page and lazy-route shell share the new exploration naming', () => {
  const page = read('../src/pages/Recommendations.jsx'), loading = read('../src/components/recommendations/RecommendationLoading.jsx')
  for (const code of [page, loading]) {
    assert.match(code, /我的手账/); assert.match(code, /<h1>番剧探索<\/h1>/)
    assert.doesNotMatch(code, /番剧发现|发现篇/)
  }
  assert.match(page, /我的手账 \/ 个性化推荐/)
  assert.match(page, /rec-explore-heading/)
})

test('journal banner preserves its own sparkle icon with shared animation and boxed arrow', () => {
  const banner = render(Banner), header = render(Header)
  assert.match(banner, /发现下一部宝藏作品，翻开/); assert.match(banner, /番剧探索手账/)
  assert.match(banner, /to=|href="\/recommendations"/)
  assert.match(banner, /class="cta-spark-icon">✨<\/span>/)
  assert.doesNotMatch(banner, /fa-wand-magic-sparkles/)
  assert.match(header, /cta-spark-icon fa-solid fa-wand-magic-sparkles/)
  for (const html of [banner, header]) {
    assert.match(html, /memphis-pressable/); assert.match(html, /cta-spark-icon/)
    assert.match(html, /cta-arrow-box/); assert.match(html, /fa-solid fa-arrow-right/)
  }
  assert.doesNotMatch(banner, /心头好|番剧发现|手账 →/)
})

test('recommendation continuation heading is 还有更多', () => {
  const page = read('../src/pages/Recommendations.jsx')
  assert.match(page, /<h2\b[^>]*>还有更多<\/h2>/); assert.doesNotMatch(page, /继续发现/)
})

test('resource notice uses the exact supplied copy and stays within reusable drawer', () => {
  const notice = '打开 Magnet 需自行安装 BT 客户端。本站仅为数据索引与信息交流平台，所有资源均非本站上传或存储。请用户严格遵守当地法律法规与版权条例，下载内容仅限学习交流使用，请于下载后 24 小时内删除。一切资源真实性与版权归属概与本站无关，使用者须自行承担相关法律责任。'
  assert.ok(render(Drawer, { open: false }).includes(notice))
  const css = read('../src/components/AnimeResourceDrawer.css')
  assert.match(css, /\.anime-resource-drawer \.anime-resource-drawer__search input:focus-visible \{ outline: none; border-color: #673ab7;/)
})

test('status summary uses held-inclusive local fallback or authoritative backend count, never sums both', () => {
  const db = [{ title: '合成作品', status: 'on_hold', period: '2026-4', score: 0, tags: [] }]
  assert.match(render(Stats, { db, theme: 'memphis' }), /0 部在看 \/ 1 部待定/)
  assert.match(render(Stats, { db, statsData: { watching_count: 2, pending_count: 6 } }), /2 部在看 \/ 6 部待定/)
})
