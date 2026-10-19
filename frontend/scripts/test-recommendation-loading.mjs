// Real eager route, delayed JS module and delayed API; no production connections.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { browsePage } from '../tests/fixtures/browseContract.js'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage(), requests = [], external = [], errors = []
await page.clock.install()
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
let blockModule = true, failModule = false, blockProfile = true, failProfile = false, serial = 0
const modules = [], profiles = []
const report = { scope: 'loading-route-and-api-isolated', production_writes: false, checks: [] }
const artifacts = path.resolve('../.tools/acceptance-loading-browser')
await mkdir(artifacts, { recursive: true })
const check = name => { report.checks.push({ name, passed: true }); console.log('PASS: ' + name) }
const finish = async (route, options) => { try { await route.fulfill(options) } catch (error) { if (!route.request().failure()) throw error } }
const release = queue => { for (const done of queue.splice(0)) done() }
await context.route('**/*', async route => {
  const url = new URL(route.request().url())
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (url.pathname === '/src/pages/Recommendations.jsx') {
    if (blockModule) await new Promise(resolve => modules.push(resolve))
    if (failModule) return route.abort('failed')
    try { return await route.continue() } catch (error) { if (!route.request().failure()) throw error; return }
  }
  if (!url.pathname.startsWith('/__loadingqa__/')) return route.continue()
  requests.push({ path: url.pathname, method: route.request().method() })
  assert.equal(route.request().method(), 'GET', 'loading must never write')
  const suffix = url.pathname.slice('/__loadingqa__/recommendations/'.length)
  if (suffix === 'capabilities/') return route.fulfill({ json: { contract_version: 'recommendations.capabilities.v1',
    algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) } })
  if (suffix === 'profile/') {
    const failing = failProfile
    if (blockProfile) await new Promise(resolve => profiles.push(resolve))
    return finish(route, failing ? { status: 503, json: {} } : { json: { contract_version: 'recommendations.3a.v1',
      eligible_sample_count: 3, usable_sample_count: 3, cold_start: false,
      sample_policy: { minimum_score: '8.5' }, tag_summary: [{ tag: '音乐', sample_count: 3 }] } })
  }
  if (suffix === 'preferences/') return route.fulfill({ json: { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] } })
  if (suffix === 'feedback/hidden/') return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1', feedback_version: 0,
    count: 0, results: [], page: 1, page_size: 12, next_page: null } })
  assert.equal(suffix, '')
  const params = Object.fromEntries(url.searchParams), base = params.category === 'familiar' ? 301 : 101
  return route.fulfill({ json: browsePage({ ids: Array.from({ length: 36 }, (_, i) => base + i),
    snapshot: (++serial).toString(16).padStart(32, '0'), category: params.category || 'all', style: params.style || 'balanced',
    filters: { type: params.type || 'all', minScore: params.min_score || '0', yearFrom: params.year_from || '', yearTo: params.year_to || '', hideRecorded: params.hide_recorded === 'true' } }) })
})
const url = `${origin}/tests/fixtures/recommendationLoadingHarness.html`
try {
  await page.goto(url)
  await page.getByText('正在翻开番剧探索手账…', { exact: true }).waitFor()
  assert.equal(requests.length, 0)
  const early = await page.locator('.rec-loading-route').evaluate(node => ({ bg: getComputedStyle(node).backgroundColor,
    hero: getComputedStyle(node.querySelector('.rec-load-hero')).borderTopWidth,
    lazyCss: [...document.querySelectorAll('style[data-vite-dev-id]')].some(style => style.dataset.viteDevId.endsWith('/styles/recommendations.css')) }))
  assert.equal(early.bg, 'rgb(255, 245, 238)'); assert.equal(early.hero, '3px'); assert.equal(early.lazyCss, false)
  await page.screenshot({ path: path.join(artifacts, 'route-desktop.png'), fullPage: false })
  check('eager styled route shell appears before page JavaScript/CSS or any private API read')
  assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  await page.clock.fastForward(8100)
  await page.getByText(/这次加载较慢/).waitFor()
  assert.equal(await page.getByRole('link', { name: '← 返回我的手账', exact: true }).getAttribute('href'), '/dashboard')
  check('slow route loading adds truthful guidance with navigation, no fabricated progress')
  blockModule = false; release(modules)
  await page.getByText('正在读取你的口味与推荐…', { exact: true }).waitFor()
  assert.equal(await page.locator('.rec-loading-route').count(), 0)
  assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  assert.equal(await page.locator('.rec-taste-metrics').count(), 0)
  assert.equal(await page.locator('.rec-load-taste-lines').count(), 1)
  assert.equal(await page.locator('.rec-card').count(), 0)
  assert.equal(await page.locator('.rec-load-grid-card').count(), 4)
  await page.screenshot({ path: path.join(artifacts, 'data-desktop.png'), fullPage: false })
  check('route hands off to matching data skeleton; no fake taste zeroes or actionable placeholder cards')
  await page.clock.fastForward(8100); await page.getByText(/这次加载较慢/).waitFor()
  await page.getByRole('button', { name: '电波契合', exact: true }).click()
  await page.getByText('正在读取你的口味与推荐…', { exact: true }).waitFor()
  assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  check('changing category resets slow-load timing rather than carrying stale waiting messages')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const animations = await page.locator('.rec-load-block, .rec-load-book > i').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).animationName))
  assert.ok(animations.every(name => name === 'none'))
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.screenshot({ path: path.join(artifacts, `data-${width}.png`), fullPage: false })
  }
  check('mobile skeletons do not overflow and reduced motion stops all loading decoration')
  blockProfile = false; release(profiles)
  await page.locator('[data-rec-motion-id="301"]').waitFor()
  assert.equal(await page.locator('.rec-load-hero, .rec-load-grid-shell, .rec-load-taste-lines').count(), 0)
  await page.clock.fastForward(9000)
  assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  check('success removes skeletons and cleans timers; late cancelled reads cannot replace the selected category')
  failProfile = true; await page.reload()
  await page.getByRole('button', { name: '重新加载', exact: true }).waitFor()
  assert.equal(await page.locator('.rec-load-hero').count(), 0)
  assert.equal(await page.locator('.rec-load-route-error').count(), 0)
  failProfile = false; await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  check('API failures use the existing local retry, never page-code failure or cold-start guidance')
  failModule = true; await page.reload()
  await page.getByRole('button', { name: '重新加载页面', exact: true }).waitFor()
  assert.equal(await page.locator('.rec-load-hero').count(), 0)
  await page.clock.fastForward(9000); assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  failModule = false; await page.getByRole('button', { name: '重新加载页面', exact: true }).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  check('failed page module has an independent reload boundary and recovers on hard reload')
  blockModule = true; blockProfile = true; await page.goto(url)
  await page.getByText('正在翻开番剧探索手账…', { exact: true }).waitFor()
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.screenshot({ path: path.join(artifacts, `route-${width}.png`), fullPage: false })
  }
  await page.getByRole('link', { name: '← 返回我的手账', exact: true }).click()
  await page.getByRole('heading', { name: '隔离手账', exact: true }).waitFor()
  blockModule = false; release(modules); release(profiles)
  await page.clock.fastForward(9000)
  assert.equal(await page.getByText(/这次加载较慢/).count(), 0)
  check('mobile route loading remains navigable; leaving cancels the slow message and cannot mount a late page')
  const reads = requests.length
  await page.goto(`${url}?guest=1`)
  await page.getByRole('heading', { name: '请先登录', exact: true }).waitFor()
  assert.equal(requests.length, reads)
  assert.equal(await page.locator('.rec-loading-route').count(), 0)
  check('normal ProtectedRoute still rejects guests before page rendering or private API calls')
  assert.deepEqual(external, []); assert.deepEqual(errors, [])
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.error = error.message; throw error }
finally {
  blockModule = false; blockProfile = false; release(modules); release(profiles)
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
