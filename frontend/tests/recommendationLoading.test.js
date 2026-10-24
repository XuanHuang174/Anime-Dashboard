import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFile } from 'node:fs/promises'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationRouteLoading: RouteLoading, RecommendationHeroSkeleton: Hero,
  RecommendationTasteSkeleton: Taste, RecommendationGridSkeleton: Grid, RECOMMENDATION_SLOW_LOAD_MS } =
  await server.ssrLoadModule('/src/components/recommendations/RecommendationLoading.jsx')
const render = (component, props = {}) => renderToStaticMarkup(React.createElement(MemoryRouter, {}, React.createElement(component, props)))
const { default: ModeNavigation } = await server.ssrLoadModule('/src/components/recommendations/RecommendationModeNavigation.jsx')

test('route fallback carries real navigation and truthful code-loading copy, without preference numbers or fake progress', () => {
  const html = render(RouteLoading)
  assert.match(html, /rec-loading-route/); assert.match(html, /返回我的手账/)
  assert.match(html, /正在翻开番剧探索手账/); assert.match(html, /role="status"/)
  assert.match(html, /rec-load-book/); assert.match(html, /rec-load-taste-lines/)
  assert.doesNotMatch(html, /正在读取你的口味|百分比|progressbar|0 部|0 个|这次加载较慢/)
})

test('route render/import failures offer page reload while retaining return navigation and removing the waiting state', () => {
  const html = render(RouteLoading, { failed: true })
  assert.match(html, /role="alert"/); assert.match(html, /重新加载页面/); assert.match(html, /返回我的手账/)
  assert.doesNotMatch(html, /rec-load-book|正在翻开|rec-load-taste-lines/)
})

test('data skeleton preserves hero poster, title, metadata, reason, tags and two noninteractive action slots', () => {
  const html = render(Hero)
  assert.match(html, /正在读取你的口味与推荐/)
  for (const part of ['poster', 'title', 'meta', 'reason', 'tags', 'actions']) assert.match(html, new RegExp(`rec-load-${part}`))
  assert.doesNotMatch(html, /<button|<img|已完成|正在分析|匹配率|progressbar/)
  assert.match(html, /aria-hidden="true"/)
})

test('single-work skeleton never claims it is loading a personal profile', () => {
  assert.match(render(Hero, { phase: 'explore' }), /正在读取参照与相似作品/)
  assert.doesNotMatch(render(Hero, { phase: 'explore' }), /你的口味|个人评分/)
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/recommendations/similar'] }, React.createElement(RouteLoading)))
  assert.match(html, /本次参照/); assert.doesNotMatch(html, /我的口味/)
})

test('mode navigation exposes approved labels, current marker, seed URL and unavailable tags', () => {
  const caps = { navigation: [{ id: 'personal', available: true }, { id: 'similar', available: true }, { id: 'tags', available: false }] }
  const html = render(ModeNavigation, { current: 'personal', capabilities: caps, similarSeed: 123 })
  assert.match(html, /个性化推荐 · 当前/); assert.match(html, /aria-current="page"/)
  assert.match(html, /href="\/recommendations\/similar\?seed=123"/)
  assert.match(html, /按标签找，尚未开放/)
  assert.doesNotMatch(render(ModeNavigation, { current: 'personal' }), /href="\/recommendations\/similar/)
})

test('taste and small grid placeholders contain no fabricated user data or recommendations', () => {
  assert.doesNotMatch(render(Taste), /0 部|0 个|rec-taste-metrics|sample_count/)
  const html = render(Grid)
  assert.equal((html.match(/class="rec-load-grid-card"/g) || []).length, 4)
  assert.match(html, /aria-hidden="true"/); assert.doesNotMatch(html, /<button|<img|查看详情|加入想看/)
})

test('shared loading styles are eager, scoped and reduced-motion aware; slow state is not a request timeout', async () => {
  const source = await readFile(new URL('../src/components/recommendations/RecommendationLoading.jsx', import.meta.url), 'utf8')
  const css = await readFile(new URL('../src/styles/recommendations-loading.css', import.meta.url), 'utf8')
  assert.match(source, /import '\.\.\/\.\.\/styles\/recommendations-loading.css'/)
  assert.doesNotMatch(source, /from 'gsap'|fetch\(|axios|recommendationApi/)
  assert.match(source, /clearTimeout\(timer\)/)
  assert.match(css, /prefers-reduced-motion: reduce/); assert.match(css, /animation: none/)
  assert.doesNotMatch(css, /@import|url\(|position: fixed|backdrop-filter/)
  assert.equal(RECOMMENDATION_SLOW_LOAD_MS, 8000)
})
