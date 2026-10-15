// Actual native dialog + shared drawer in a localhost fixture. No live accounts,
// external requests, clipboard writes or Magnet launches are permitted.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(),
  plugins: [react()], server: { host: '127.0.0.1', port: 0, strictPort: false }, logLevel: 'error' })
await server.listen()
const origin = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const errors = [], external = []
await context.route('**/*', route => {
  if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort() }
  return route.continue()
})
const page = await context.newPage()
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
const artifacts = path.resolve('../.tools/resource-drawer-7b')
await mkdir(artifacts, { recursive: true })
const search = page.locator('#recResourceDrawer-keyword')
const trigger = page.getByRole('button', { name: '🧲 寻找资源', exact: true })
const searchFor = async value => { await search.fill(value); await search.press('Enter') }
const settle = () => page.waitForFunction(() => document.querySelector('dialog')?.getAnimations({ subtree: true }).length === 0)
try {
  await page.goto(`${origin}/tests/fixtures/resourceDrawerHarness.html`)
  await page.getByRole('button', { name: '打开作品A' }).click()
  await trigger.waitFor()
  assert.equal(await page.evaluate(() => resourceQA.searches.length), 0, 'Opening details makes no resource request')
  await trigger.click()
  await page.getByRole('heading', { name: '资源-作品A', exact: true }).waitFor()
  assert.equal(await page.evaluate(() => resourceQA.searches.length), 1, 'StrictMode opening searches once')
  await settle()
  const geometry = await page.evaluate(() => {
    const detail = document.querySelector('.rec-dialog-main').getBoundingClientRect()
    const resource = document.querySelector('.rec-dialog-resource').getBoundingClientRect()
    return { detailRight: detail.right, resourceLeft: resource.left, resourceRight: resource.right, width: resource.width, viewport: innerWidth,
      native: document.querySelector('dialog').matches(':modal') }
  })
  assert.ok(geometry.native && geometry.resourceLeft >= geometry.detailRight - 2 && geometry.resourceRight <= geometry.viewport)
  assert.equal(geometry.width, 400)
  assert.ok(await search.isVisible())
  await page.getByRole('button', { name: '关闭面板', exact: true }).focus()
  await page.keyboard.press('Shift+Tab')
  assert.equal(await page.locator('dialog').evaluate(node => node.contains(document.activeElement)), true, 'Shift+Tab wraps to the last dialog control')
  for (let index = 0; index < 12; index++) {
    await page.keyboard.press('Tab')
    assert.equal(await page.locator('dialog').evaluate(node => node.contains(document.activeElement)), true, 'Native dialog contains keyboard focus')
  }
  await page.screenshot({ path: path.join(artifacts, 'desktop.png') })
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => !document.querySelector('dialog').classList.contains('rec-dialog--resource-open'))
  assert.equal(await page.locator('dialog[open]').count(), 1)
  assert.equal(await trigger.evaluate(node => node === document.activeElement), true)
  await trigger.click(); await page.getByRole('heading', { name: '资源-作品A', exact: true }).waitFor()
  await searchFor('slow')
  await page.waitForFunction(() => resourceQA.searches.some(row => row.keyword === 'slow'))
  await page.evaluate(() => resourceQA.open(202))
  await page.getByTestId('detail-title').filter({ hasText: '作品B' }).waitFor()
  assert.equal(await page.evaluate(() => resourceQA.searches.find(row => row.keyword === 'slow').signal.aborted), true)
  await trigger.click(); await page.getByRole('heading', { name: '资源-作品B', exact: true }).waitFor()
  await page.evaluate(() => resourceQA.resolveSlow())
  assert.equal(await page.getByRole('heading', { name: '资源-OLD', exact: true }).count(), 0)
  await page.getByRole('button', { name: '复制链接', exact: true }).click()
  await page.waitForFunction(() => resourceQA.copies.length === 1)
  await page.getByRole('button', { name: '← 返回详情' }).click()
  await trigger.click(); await page.getByRole('heading', { name: '资源-作品B', exact: true }).waitFor()
  await page.evaluate(() => resourceQA.copies[0].reject(new Error('old denied')))
  assert.equal(await page.getByRole('textbox', { name: '手动复制 Magnet 链接' }).count(), 0)
  await searchFor('error'); await page.getByText('本地模拟资源失败', { exact: true }).waitFor()
  assert.equal(await page.locator('dialog[open]').count(), 1)
  await searchFor('empty'); await page.getByText('未找到相关资源，请尝试缩短或更换搜索关键词', { exact: true }).waitFor()
  assert.ok(await page.getByRole('link', { name: '🌐 在动漫花园网页端搜索', exact: true }).isVisible())
  await page.setViewportSize({ width: 375, height: 812 })
  await page.waitForFunction(() => document.querySelector('.rec-dialog-main').inert)
  await settle()
  assert.ok(await search.isVisible())
  assert.equal(await page.locator('dialog').count(), 1)
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press('Tab')
    assert.equal(await page.locator('.rec-dialog-main').evaluate(node => node.contains(document.activeElement)), false, 'Mobile hidden detail cannot receive keyboard focus')
  }
  const mobile = await page.locator('.rec-dialog-resource').boundingBox()
  assert.ok(mobile.x >= 0 && mobile.x + mobile.width <= 375)
  await page.screenshot({ path: path.join(artifacts, 'mobile.png') })
  for (const width of [320, 768, 1024]) {
    await page.setViewportSize({ width, height: 812 })
    await page.waitForFunction(compact => document.querySelector('.rec-dialog-main').inert === compact, width < 1024)
    await settle()
    const box = await page.locator('.rec-dialog-resource').boundingBox()
    assert.ok(box.x >= 0 && box.x + box.width <= width, `Resource panel stays within ${width}px viewport`)
  }
  await page.setViewportSize({ width: 375, height: 812 })
  await page.waitForFunction(() => document.querySelector('.rec-dialog-main').inert)
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => !document.querySelector('.rec-dialog-main').inert)
  assert.equal(await trigger.evaluate(node => node === document.activeElement), true)
  await page.keyboard.press('Escape')
  await page.locator('dialog').waitFor({ state: 'detached' })
  await page.evaluate(() => resourceQA.open(101))
  await trigger.click(); await searchFor('slow')
  await page.waitForFunction(() => resourceQA.searches.at(-1).keyword === 'slow')
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  await page.locator('dialog').waitFor({ state: 'detached' })
  assert.equal(await page.evaluate(() => resourceQA.searches.at(-1).signal.aborted), true, 'Closing detail unmounts and cancels resource request')
  await page.evaluate(() => resourceQA.resolveSlow())
  assert.equal(await page.locator('dialog').count(), 0, 'Late response cannot reopen closed detail')
  assert.deepEqual(external, []); assert.deepEqual(errors, [])
  console.log('PASS: native-dialog desktop/mobile, demand-only search, Escape/focus, request/copy isolation, local errors and fallback')
  console.log(`Artifacts: ${artifacts}`)
} finally { await browser.close(); await server.close() }
