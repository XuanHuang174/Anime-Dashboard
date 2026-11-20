import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const origin = process.env.JOURNAL_C_TEST_URL || 'http://127.0.0.1:5189'
assert.match(origin, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const output = new URL('../../.tools/recommendation-batch-c/', import.meta.url)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const report = { checks: [], errors: [] }, check = (name, value) => { report.checks.push(name); assert.ok(value, name) }
let page
try {
  const context = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return url.origin === origin || ['cdn.tailwindcss.com', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com'].includes(url.hostname) ? route.continue() : route.abort()
  })
  page = await context.newPage(); page.setDefaultTimeout(12000)
  page.on('pageerror', error => report.errors.push(error.message))
  for (const width of [1440, 780, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(origin + '/tests/fixtures/journalFiltersBHarness.html?mode=quick')
    await page.locator('.quick-filter-basics').waitFor()
    check(`quick-filter-column-alignment-${width}`, await page.evaluate(() => {
      const boxes = [...document.querySelectorAll('.quick-filter-basics > label, section[aria-label="包含条件"], section[aria-label="排除条件"]')].map(el => el.getBoundingClientRect())
      return Math.abs(boxes[0].right - boxes[2].right) < 1 && Math.abs(boxes[1].right - boxes[3].right) < 1
    }))
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(origin + '/tests/fixtures/explorationHarness.html?batchC=1')
  await page.getByRole('link', { name: '开始验收个性化推荐', exact: true }).click()
  await page.locator('.rec-card').first().waitFor()
  await page.locator('.rec-browse-help > summary').click()
  // Reproduce late shared chunk loading: visual hierarchy must remain stable.
  await page.addStyleTag({ url: origin + '/src/styles/recommendations-layout.css' })
  check('cream-explanation-with-black-border-after-late-layout-css', await page.locator('.rec-browse-help-body').evaluate(el => {
    const style = getComputedStyle(el); return style.backgroundColor === 'rgb(255, 245, 238)' && style.borderTopWidth === '2px'
  }))
  check('preferences-remain-next-to-filter', await page.locator('.rec-browse-actions .rec-preferences').count() === 1)
  await page.screenshot({ path: fileURLToPath(new URL('navigation-desktop.png', output)) })
  await page.locator('.rec-card .rec-cover-button').first().click()
  await page.locator('.rec-detail-footer').waitFor()
  check('personal-evidence-default-collapsed', await page.locator('dialog .rec-evidence').getAttribute('open') === null)
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  await page.getByRole('link', { name: '单番相似', exact: true }).click()
  await page.locator('#rec-seed-query').fill('1'); await page.getByRole('button', { name: '搜索作品', exact: true }).click()
  await page.locator('.rec-seed-options button').first().click()
  await page.locator('.rec-card').first().waitFor()
  const counts = () => page.evaluate(() => window.batchCHarness.calls.filter(name => name.startsWith('browse:')).length)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 640 : 900 })
    const card = page.locator('.rec-card').first()
    await card.locator('.rec-cover-button').click()
    await page.locator('.rec-detail-footer').waitFor()
    check(`evidence-default-collapsed-${width}`, await page.locator('dialog .rec-evidence').getAttribute('open') === null)
    const footer = page.locator('.rec-detail-footer'), before = await footer.boundingBox()
    await page.locator('.rec-dialog-main > .rec-dialog-body').evaluate(el => { el.scrollTop = el.scrollHeight })
    const after = await footer.boundingBox()
    check(`footer-fixed-while-body-scrolls-${width}`, Math.abs(before.y - after.y) < 1)
    check(`resources-visible-and-clickable-${width}`, await footer.getByRole('button', { name: /寻找资源/ }).evaluate(el => {
      const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
    }))
    await footer.getByRole('button', { name: /寻找资源/ }).click()
    await page.keyboard.press('Escape')
    check(`resource-escape-keeps-detail-${width}`, await page.locator('dialog[open]').count() === 1)
    await page.keyboard.press('Escape')
    await card.locator('.rec-title-button').click(); await page.locator('.rec-detail-footer').waitFor(); await page.keyboard.press('Escape')
    // A metadata/background click opens details, native disclosures do not.
    await card.locator('.rec-meta').click(); await page.locator('.rec-detail-footer').waitFor(); await page.keyboard.press('Escape')
    await card.locator('.rec-evidence summary').click()
    check(`evidence-click-does-not-open-detail-${width}`, await page.locator('dialog[open]').count() === 0)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('.rec-filter-button').click()
  await page.getByRole('checkbox', { name: 'TV', exact: true }).click()
  await page.getByRole('checkbox', { name: 'OVA', exact: true }).click()
  await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2020')
  await page.getByRole('button', { name: '应用筛选', exact: true }).click()
  await page.locator('.rec-card').first().waitFor()
  await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.rec-card').length === 24)
  const beforeReturn = await counts()
  await page.evaluate(() => window.scrollTo(0, 1600)); await page.waitForTimeout(100)
  const savedScroll = await page.evaluate(() => window.scrollY)
  // Browser history transitions keep the host mounted, as real mode navigation does.
  await page.evaluate(() => { history.pushState(null, '', '/recommendations/tags?include=日常'); dispatchEvent(new PopStateEvent('popstate')) })
  await page.locator('#tag-results .rec-card').first().waitFor()
  await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.rec-card').length === 24)
  await page.evaluate(() => window.scrollTo(0, 800)); await page.waitForTimeout(100)
  await page.goBack()
  await page.waitForFunction(() => document.querySelectorAll('#similar-results .rec-card').length === 24)
  await page.waitForTimeout(300)
  check('return-keeps-pages-and-scroll', Math.abs((await page.evaluate(() => window.scrollY)) - savedScroll) < 4)
  check('return-does-not-restart-similar-browse', await counts() === beforeReturn + 2)
  check('return-keeps-applied-type-and-year-filters', await page.getByRole('button', { name: '移除条件：OVA / TV', exact: true }).count() === 1
    && await page.getByRole('button', { name: '移除条件：2020—不限', exact: true }).count() === 1)
  await page.goForward()
  await page.waitForFunction(() => document.querySelectorAll('#tag-results .rec-card').length === 24)
  check('tag-query-return-keeps-pages', await counts() === beforeReturn + 2)
  await page.goBack()
  await page.waitForFunction(() => document.querySelectorAll('#similar-results .rec-card').length === 24)
  // Visiting another reference must not replace the previous reference's pages.
  await page.evaluate(() => { history.pushState(null, '', '/recommendations/similar?seed=2'); dispatchEvent(new PopStateEvent('popstate')) })
  await page.waitForFunction(() => document.querySelectorAll('#similar-results .rec-card').length === 12)
  await page.goBack()
  await page.waitForFunction(() => document.querySelectorAll('#similar-results .rec-card').length === 24)
  check('older-reference-keeps-its-own-pages', true)
  await page.evaluate(() => window.batchCHarness.expire())
  await page.getByRole('link', { name: '个性化推荐', exact: true }).click()
  await page.getByRole('link', { name: '单番相似', exact: true }).click()
  await page.locator('#rec-context-update').waitFor()
  check('invalidated-snapshot-retains-position-but-removes-evidence', await page.locator('.rec-card .rec-evidence-body .rec-evidence-label').count() === 0)
  await page.goBack()
  await page.locator('.rec-card').first().waitFor()
  await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.rec-card').length === 24)
  await page.getByRole('link', { name: '单番相似', exact: true }).click()
  await page.goBack()
  await page.waitForFunction(() => document.querySelectorAll('.rec-card').length === 24)
  check('personal-return-keeps-pages', true)
  await page.goto(origin + '/tests/fixtures/creditsHarness.html')
  await page.getByRole('link', { name: /人物与制作/ }).click()
  await page.getByRole('textbox', { name: '声优姓名或人物链接' }).fill('白羽')
  await page.getByRole('button', { name: /查找/ }).click()
  await page.getByRole('button', { name: /查看关联作品/ }).first().click()
  await page.locator('.credits-work .rec-cover-button').first().click()
  await page.locator('.rec-detail-footer').waitFor()
  check('credits-cover-opens-detail-with-resource-footer', await page.locator('.rec-detail-footer').getByRole('button', { name: /寻找资源/ }).count() === 1)
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  await page.locator('.credits-work .rec-title-button').first().press('Enter')
  await page.locator('.rec-detail-footer').waitFor()
  check('credits-title-keyboard-opens-detail', true)
  check('no-runtime-errors', report.errors.length === 0)
} catch (error) {
  report.failure = error.message
  if (page) await page.screenshot({ path: fileURLToPath(new URL('failure.png', output)) })
  throw error
} finally {
  await writeFile(new URL('report.json', output), JSON.stringify(report, null, 2))
  await browser.close(); console.log(JSON.stringify(report))
}
