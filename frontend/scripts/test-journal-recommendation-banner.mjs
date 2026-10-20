import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage(), external = [], errors = [], checks = []
const artifacts = path.resolve('../.tools/acceptance-journal-banner')
await mkdir(artifacts, { recursive: true })
page.on('pageerror', error => errors.push(error.message))
await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : (external.push(route.request().url()), route.abort()))
const check = name => { checks.push({ name, passed: true }); console.log('PASS: ' + name) }
let result = 'failed'
try {
  await page.goto(`${origin}/tests/fixtures/journalRecommendationBannerHarness.html`)
  const banner = page.locator('.journal-recommendation-banner'), icon = banner.locator('.cta-spark-icon'), arrow = banner.locator('.cta-arrow-box')
  assert.equal(await banner.getAttribute('href'), '/recommendations')
  assert.equal(await icon.textContent(), '✨')
  assert.ok(['block', 'inline-block'].includes(await icon.evaluate(node => getComputedStyle(node).display)))
  assert.equal(await icon.evaluate(node => getComputedStyle(node).animationName), 'ctaSparkPop')
  check('banner restores its sparkle icon with shared animation and keeps recommendation navigation')
  const before = await banner.boundingBox()
  await banner.hover(); await page.waitForTimeout(180)
  const hover = await banner.boundingBox()
  assert.ok(hover.x > before.x && hover.y > before.y)
  assert.equal(await banner.evaluate(node => getComputedStyle(node).boxShadow), 'none')
  assert.equal(await arrow.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(78, 205, 196)')
  assert.notEqual(await arrow.evaluate(node => getComputedStyle(node).transform), 'none')
  check('hover matches homepage pressed feedback and moves the boxed teal arrow')
  for (const width of [500, 390, 320]) {
    await page.mouse.move(0, 0); await page.setViewportSize({ width, height: 800 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    const box = await arrow.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width)
    await page.screenshot({ path: path.join(artifacts, `banner-${width}.png`) })
  }
  check('desktop and narrow mobile keep the boxed arrow without horizontal overflow')
  await banner.focus()
  assert.equal(await banner.evaluate(node => node.matches(':focus-visible')), true)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  assert.equal(await icon.evaluate(node => getComputedStyle(node).animationName), 'none')
  assert.equal(await banner.evaluate(node => getComputedStyle(node).transitionDuration), '0s')
  assert.equal(await arrow.evaluate(node => getComputedStyle(node).transitionDuration), '0s')
  check('keyboard navigation stays visible and reduced motion stops icon and transition animation')
  assert.deepEqual(external, []); assert.deepEqual(errors, []); result = 'passed'
} finally {
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify({ result, checks, production_writes: false }, null, 2))
  await browser.close(); await server.close()
}
