import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const origin = process.env.JOURNAL_RETURN_TEST_URL || 'http://127.0.0.1:5189'
assert.match(origin, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const output = new URL('../../.tools/journal-exploration-return/', import.meta.url)
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const report = { checks: [], errors: [] }, check = (name, value) => { assert.ok(value, name); report.checks.push(name) }
let page
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 800 }, reducedMotion: 'reduce' })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return url.origin === origin || ['cdn.tailwindcss.com', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com'].includes(url.hostname) ? route.continue() : route.abort()
  })
  page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => report.errors.push(error.message))
  await page.goto(origin + '/tests/fixtures/journalReturnHarness.html?series=1')
  await page.getByRole('link', { name: '开始验收个性化推荐', exact: true }).click()
  await page.getByRole('link', { name: '← 返回我的手账', exact: true }).click()
  await page.locator('.memphis-anime-list').waitFor()
  await page.getByRole('button', { name: /预览模式/ }).click()
  await page.getByRole('combobox', { name: '选择每页显示数量' }).first().selectOption('12')
  await page.locator('input[placeholder*="输入番剧"]').fill('合成')
  for (const [id, label] of [['tagSelect', '日常'], ['statusSelect', '看过'], ['yearSelect', '2020 年'], ['sortSelect', '评分 (高 → 低)']]) {
    await page.locator('#' + id).click(); await page.getByRole('option', { name: label, exact: true }).click()
  }
  await page.getByRole('button', { name: /合成年份$/ }).click()
  await page.getByRole('combobox', { name: '选择每页显示数量' }).first().selectOption('12')
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: /下一页/ }).first().click()
  await page.waitForFunction(() => document.querySelector('#paginationJumpInput')?.value === '2')
  await page.waitForTimeout(400)
  const filters = () => page.evaluate(() => ({ search: document.querySelector('input[placeholder*="输入番剧"]').value,
    fields: ['tagSelect', 'statusSelect', 'yearSelect', 'sortSelect'].map(id => document.getElementById(id).textContent.trim()),
    page: document.querySelector('#paginationJumpInput').value, size: document.querySelector('select[aria-label="选择每页显示数量"]').value }))
  const reads = () => page.evaluate(() => window.journalReturnQA.journalCalls.filter(call => call.url === 'records/').length)
  const expected = await filters(), before = await reads()
  await page.locator('.memphis-anime-list').getByRole('button', { name: /详情$/ }).first().click()
  const title = await page.locator('#modalTitle').innerText(), scroll = await page.evaluate(() => window.scrollY)
  await page.locator('#detailModal').getByRole('button', { name: '找相似', exact: true }).click()
  await page.locator('#similar-results .rec-card').first().waitFor()
  check('journal-modal-unmounted-while-exploring', await page.locator('#detailModal').count() === 0)
  check('foreground-is-scrollable', await page.evaluate(() => getComputedStyle(document.body).overflow !== 'hidden'))
  await page.getByRole('link', { name: '按标签找', exact: true }).click()
  await page.getByRole('link', { name: '个性化推荐', exact: true }).click()
  await page.getByRole('link', { name: '← 返回我的手账', exact: true }).click()
  await page.locator('#detailModal').waitFor()
  check('return-keeps-journal-filters-page-and-size', JSON.stringify(await filters()) === JSON.stringify(expected))
  check('return-keeps-open-preview', await page.locator('#modalTitle').innerText() === title)
  check('no-default-or-unnecessary-record-read-on-return', await reads() === before)
  await page.waitForTimeout(100)
  check('return-keeps-journal-scroll', Math.abs(await page.evaluate(() => window.scrollY) - scroll) < 4)
  // Actual private preview tag entry, then browser Back.
  await page.locator('#detailModal').getByRole('button', { name: '探索这个题材：日常', exact: true }).click()
  await page.locator('#tag-results .rec-card').first().waitFor()
  await page.goBack(); await page.locator('#detailModal').waitFor()
  check('private-tag-entry-browser-back-keeps-filters', JSON.stringify(await filters()) === JSON.stringify(expected))
  check('private-tag-entry-browser-back-keeps-preview', await page.locator('#modalTitle').innerText() === title)
  // A confirmed synthetic write causes one filtered refresh when returning.
  await page.locator('#detailModal').getByRole('button', { name: '找相似', exact: true }).click()
  await page.locator('#similar-results .rec-card').first().waitFor()
  await page.locator('#similar-results .rec-card').first().getByRole('button', { name: /加入想看/ }).click()
  await page.waitForFunction(() => window.journalReturnQA.calls.some(call => call.startsWith('want:')))
  await page.waitForTimeout(250)
  await page.getByRole('link', { name: '← 返回我的手账', exact: true }).click()
  await page.waitForFunction(n => window.journalReturnQA.journalCalls.filter(call => call.url === 'records/').length > n, before)
  check('confirmed-write-refresh-keeps-conditions', JSON.stringify(await filters()) === JSON.stringify(expected))
  check('refresh-sends-original-conditions', await page.evaluate(() => {
    const p = window.journalReturnQA.journalCalls.filter(call => call.url === 'records/').at(-1).params
    return p.page === 2 && p.page_size === '12' && p.search === '合成' && p.tag === '日常' && p.status === 'completed' && p.year === '2020' && p.sort === 'score-desc' && p.quick_filter === 201
  }))
  // Use the existing synthetic exploration source to test the real series layer.
  await page.locator('#detailModal').getByRole('button', { name: '找相似', exact: true }).click()
  await page.locator('#similar-results .rec-card .rec-cover-button').first().click()
  await page.getByRole('button', { name: /查看全部关联/ }).click()
  await page.locator('.rec-dialog--series .series-row').first().waitFor()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 600 })
    const layer = page.locator('.rec-dialog--series'), top = layer.getByRole('button', { name: '返回页面顶部', exact: true, includeHidden: true })
    await layer.evaluate(el => { el.scrollTop = 500 }); await page.waitForTimeout(100)
    check(`series-back-to-top-visible-${width}`, await top.getAttribute('aria-hidden') === 'false')
    check(`series-back-to-top-hittable-${width}`, await top.evaluate(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)) }))
    await top.click(); await page.waitForTimeout(100)
    check(`series-back-to-top-scrolls-real-owner-${width}`, await layer.evaluate(el => el.scrollTop) === 0)
    await layer.getByRole('button', { name: /查看本作详情/ }).click()
    const detail = page.locator('.rec-dialog--detail').last(); await detail.locator('.rec-detail-footer').waitFor()
    check(`series-top-hidden-under-nested-detail-${width}`, await top.getAttribute('aria-hidden') === 'true')
    check(`detail-footer-right-aligned-${width}`, await detail.locator('.series-detail-actions').evaluate(el => getComputedStyle(el).justifyContent === 'flex-end'))
    await page.keyboard.press('Escape')
  }
  await page.screenshot({ path: fileURLToPath(new URL('series-return.png', output)) })
  await page.evaluate(() => { history.pushState(null, '', '/recommendations/series/1'); dispatchEvent(new PopStateEvent('popstate')) })
  await page.locator('.rec-dialog--series').waitFor({ state: 'detached' })
  await page.locator('.series-shell:not(.series-shell--overlay) .series-row').first().waitFor()
  await page.evaluate(() => window.scrollTo(0, 500)); await page.waitForTimeout(100)
  const standaloneTop = page.getByRole('main', { name: '系列与关联详情' }).getByRole('button', { name: '返回页面顶部', exact: true, includeHidden: true })
  check('standalone-series-top-visible', await standaloneTop.getAttribute('aria-hidden') === 'false')
  await standaloneTop.click(); await page.waitForTimeout(100)
  check('standalone-series-top-scrolls-window', await page.evaluate(() => window.scrollY) === 0)
  await page.evaluate(() => {
    history.pushState(null, '', '/recommendations/similar'); dispatchEvent(new PopStateEvent('popstate'))
    localStorage.setItem('anime_journal_access_token', 'synthetic-other-account')
    dispatchEvent(new Event('storage'))
  })
  await page.waitForFunction(() => document.querySelectorAll('.memphis-dashboard').length === 0)
  check('account-change-releases-retained-journal', true)
  await page.getByRole('link', { name: '← 返回我的手账', exact: true }).click()
  await page.locator('.memphis-anime-list, .memphis-anime-grid').waitFor()
  check('other-account-does-not-inherit-filters-or-preview', await page.locator('input[placeholder*="输入番剧"]').inputValue() === ''
    && await page.locator('#tagSelect').innerText() === '全部标签' && await page.locator('#detailModal').count() === 0)
  check('no-runtime-errors', report.errors.length === 0)
} catch (error) {
  report.failure = error.message
  if (page) await page.screenshot({ path: fileURLToPath(new URL('failure.png', output)) })
  throw error
} finally {
  await writeFile(new URL('report.json', output), JSON.stringify(report, null, 2))
  await browser.close(); console.log(JSON.stringify(report))
}
