// Full production page, isolated contracts and resource HTTP. No live writes.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react()],
  server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const origin = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage(), errors = [], external = [], searches = [], delayed = [], copied = []
const report = { scope: '7c-local-production-page-with-synthetic-contracts', checks: [], real_backend_writes: false,
  bt_launches: 0, real_clipboard_writes: 0, limitations: ['Synthetic transport, not a production write acceptance.'] }
const artifacts = path.resolve('../.tools/acceptance-7c-browser')
await mkdir(artifacts, { recursive: true })
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
await context.addInitScript(() => { Object.defineProperty(navigator, 'clipboard', { configurable: true,
  value: { writeText: async value => { window.syntheticCopies ||= []; window.syntheticCopies.push(value) } } }) })
const resource = keyword => ({ status: 'success', results: [{ title: `资源-${keyword}`, size: '1 GB',
  magnet: `magnet:?xt=urn:btih:${'a'.repeat(40)}`, publisher: '合成测试', published_at: '' }] })
await context.route('**/*', async route => {
  const url = new URL(route.request().url())
  if (url.pathname === '/api/utils/anime-search/') {
    const keyword = url.searchParams.get('keyword'); searches.push(keyword)
    if (keyword === 'slow') { delayed.push(route); return }
    if (keyword === 'offline') return route.abort('internetdisconnected')
    if (keyword === 'many') return route.fulfill({ json: { status: 'success', results: Array.from({ length: 20 },
      (_, index) => ({ ...resource(keyword).results[0], title: `资源-${index}` })) } })
    return route.fulfill({ json: resource(keyword) })
  }
  if (url.origin !== origin) { external.push(url.href); return route.abort() }
  return route.continue()
})
const trigger = () => page.getByRole('button', { name: '🧲 寻找资源', exact: true })
const open = id => page.getByRole('button', { name: `查看作品${id}详情`, exact: true }).click()
const close = () => page.getByRole('button', { name: '关闭面板', exact: true }).click()
const query = async text => { const input = page.locator('#recResourceDrawer-keyword'); await input.fill(text); await input.press('Enter') }
const load = async () => { await page.goto(`${origin}/tests/fixtures/recommendationLoopHarness.html`); await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor() }
const check = name => { report.checks.push({ name, passed: true }); console.log(`PASS: ${name}`) }
try {
  await load()
  // Hero click then detail click before the same confirmation: one POST.
  await page.getByRole('button', { name: '作品101：+ 加入想看', exact: true }).click()
  await open(101)
  await page.getByRole('button', { name: '作品101：正在加入…', exact: true }).last().waitFor()
  await page.getByRole('button', { name: '作品101：正在加入…', exact: true }).last().evaluate(node => { for (let i = 0; i < 20; i++) node.click() })
  assert.equal(await page.evaluate(() => loopQA.calls.filter(row => row.kind === 'want').length), 1)
  await trigger().click(); await page.getByRole('heading', { name: '资源-作品101', exact: true }).waitFor()
  await page.evaluate(() => { loopQA.config.detail = 'hold'; loopQA.confirmWant() })
  await page.getByText('加入操作已确认，旧依据已作废，正在重新读取完整详情…', { exact: true }).waitFor()
  assert.equal(await page.locator('#recResourceDrawer').count(), 0)
  assert.equal(await page.getByText(/^完整详情 ctx-0/).count(), 0)
  await page.evaluate(() => { loopQA.config.detail = 'normal'; loopQA.resolveDetails() })
  await page.getByRole('button', { name: '作品101：已在想看', exact: true }).waitFor()
  assert.equal(await page.evaluate(() => loopQA.calls.filter(row => row.kind === 'detail').at(-1).token), undefined)
  await trigger().click(); await page.getByText('放送季度：2026-07', { exact: true }).waitFor()
  await page.keyboard.press('Escape'); await close()
  assert.equal(await page.getByRole('button', { name: '查看作品101详情', exact: true }).count(), 0)
  check('confirmed want: one request, stale detail/resource unmounted, whole GET reload, refreshed season, owned list exclusion')

  await load(); await open(202)
  await page.getByRole('button', { name: '作品202：+ 加入想看', exact: true }).last().click()
  assert.equal(await page.evaluate(() => loopQA.calls.filter(row => row.kind === 'want').length), 1)
  await page.evaluate(() => { loopQA.config.detail = 'error'; loopQA.confirmWant() })
  await page.getByText(/加入操作已确认，但详情更新失败/).waitFor()
  await page.evaluate(() => { loopQA.config.detail = 'normal' })
  await page.getByRole('button', { name: '重试详情', exact: true }).click()
  await page.getByRole('button', { name: '作品202：已在想看', exact: true }).last().waitFor()
  assert.equal(await page.evaluate(() => loopQA.calls.filter(row => row.kind === 'want').length), 1)
  check('confirmed write + failed read has a GET-only detail retry, never a duplicate POST')

  await load(); await page.evaluate(() => { loopQA.config.detail = 'stale' }); await open(101)
  await page.getByRole('button', { name: '重新加载推荐', exact: true }).last().click()
  await page.locator('dialog').waitFor({ state: 'detached' })
  assert.equal(await page.evaluate(() => loopQA.calls.filter(row => row.kind === 'detail').length), 1)
  check('409 detail context requires explicit recommendation reload, never strips token automatically')

  await load(); await open(101); await trigger().click()
  await query('offline'); await page.getByText('资源搜索暂时不可用，请稍后再试或使用网页搜索。', { exact: true }).waitFor()
  await query('恢复'); await page.getByRole('heading', { name: '资源-恢复', exact: true }).waitFor()
  await page.getByRole('button', { name: '复制链接', exact: true }).click()
  await page.getByRole('button', { name: '已复制 ✓', exact: true }).waitFor()
  copied.push(...await page.evaluate(() => window.syntheticCopies || []))
  assert.equal(copied.length, 1)
  await query('many'); await page.locator('.resource-card').nth(19).waitFor()
  const searchTop = (await page.locator('#recResourceDrawer-keyword').boundingBox()).y
  const scrollTop = await page.locator('.anime-resource-drawer__results').evaluate(node => { node.scrollTop = node.scrollHeight; return node.scrollTop })
  assert.ok(scrollTop > 0)
  assert.equal((await page.locator('#recResourceDrawer-keyword').boundingBox()).y, searchTop)
  await query('slow'); await page.waitForFunction(() => document.querySelector('[aria-label="正在搜索资源"]'))
  // Programmatic rapid category change while a modal is active tests cancellation
  // even when normal pointer interaction is correctly inert.
  await page.getByRole('button', { name: '换一种口味', exact: true }).evaluate(node => node.click())
  await page.locator('dialog').waitFor({ state: 'detached' })
  await page.getByRole('button', { name: '查看作品301详情', exact: true }).waitFor()
  await delayed.shift().fulfill({ json: resource('OLD') }).catch(() => {})
  assert.equal(await page.getByRole('heading', { name: '资源-OLD', exact: true }).count(), 0)
  check('resource offline/retry/copy/independent scrolling isolated; category change closes resource/detail and rejects late network result')

  await load(); await open(101); await trigger().click(); await query('slow')
  await page.evaluate(() => loopQA.navigate('/dashboard'))
  await page.getByRole('heading', { name: '隔离手账返回页' }).waitFor()
  await delayed.shift().fulfill({ json: resource('OLD') }).catch(() => {})
  assert.equal(await page.locator('dialog').count(), 0)
  check('route unmount cannot accept late resource response or reopen a dialog')

  await load(); await page.evaluate(() => { loopQA.config.list = 'hold' })
  await page.getByRole('button', { name: '熟悉的心动', exact: true }).click()
  await page.evaluate(() => { loopQA.config.list = 'normal' })
  await page.getByRole('button', { name: '换一种口味', exact: true }).click()
  await page.getByRole('button', { name: '查看作品301详情', exact: true }).waitFor()
  await page.evaluate(() => loopQA.resolveLists())
  assert.equal(await page.getByRole('button', { name: '查看作品401详情', exact: true }).count(), 0)
  await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.getByRole('button', { name: '查看作品303详情', exact: true }).waitFor()
  check('slow list, rapid category switch and pagination never append old-category items')

  await page.evaluate(() => { loopQA.config.list = 'error' })
  await page.getByRole('button', { name: '为你发现', exact: true }).click()
  await page.getByRole('heading', { name: '推荐暂时没有准备好' }).waitFor()
  assert.equal(await page.getByText('从几种喜欢的题材开始', { exact: true }).count(), 0)
  assert.ok(await page.getByRole('link', { name: '← 返回我的手账', exact: true }).isVisible())
  await page.evaluate(() => { loopQA.config.list = 'normal' })
  await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  check('recommendation error has independent retry and journal return, not cold-start or fabricated results')
  await page.screenshot({ path: path.join(artifacts, 'restored-page.png'), fullPage: true })
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.failure = error.message; throw error }
finally {
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
