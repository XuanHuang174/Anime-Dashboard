// Real local Django + React; separate layout fixture uses production batch controls.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/watch-review-d2-fixture.json', 'utf8')), base = 'http://127.0.0.1:5191', out = '../.tools/watch-review-d2-qa'
assert.match(f.username, /^plans-b2-/); await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const motion = process.env.REVIEW_TEST_MOTION || 'reduce'
assert.ok(['reduce', 'no-preference'].includes(motion))
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: motion })
await context.addInitScript(({ token, user_id }) => { localStorage.setItem('anime_journal_access_token', token); localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'edit') }, f)
const page = await context.newPage(); page.setDefaultTimeout(20000)
const errors = [], writes = [], reads = []
page.on('pageerror', e => errors.push(e.message))
page.on('request', r => { if (r.url().includes('/api/watch-review/')) reads.push(r.url()); if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()) })
const nav = name => page.getByRole('navigation', { name: '私人手账工作区' }).getByRole('button', { name, exact: true })
const review = () => page.getByRole('region', { name: '私人观看回顾工作区', exact: true })
const ready = async () => { await review().locator('.review-summary[aria-busy=false]').waitFor(); await review().locator('.review-error').waitFor({ state: 'detached' }) }
const btn = name => review().getByRole('button', { name, exact: true })
const section = name => review().getByRole('group', { name: '回顾内容' }).getByRole('button', { name, exact: true })
const api = async (method, path, body) => { const r = await context.request[method](`${base}/api/${path}`, { headers: { Authorization: `Bearer ${f.token}` }, ...(body ? { data: body } : {}) }); assert.equal(r.status(), 200, await r.text()); return r.json() }
const applyYear = async year => { await btn('按年度').click(); await review().getByLabel('回顾年份', { exact: true }).fill(year); await btn('查看回顾').click(); await ready() }
const currentYear = () => new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai', year: 'numeric' }).format(new Date())
try {
  await page.goto(base + '/dashboard'); await nav('回顾').click(); await ready()
  await btn('自选时间段').click(); await review().getByLabel('开始日期', { exact: true }).fill('2026-02-01'); await review().getByLabel('结束日期', { exact: true }).fill('2026-03-31')
  await btn('按年度').click(); assert.equal(await review().getByLabel('回顾年份', { exact: true }).inputValue(), currentYear())
  await review().getByLabel('回顾年份', { exact: true }).fill(f.year); await btn('查看回顾').click(); await ready()
  await review().getByRole('heading', { name: '2026年观看回顾', exact: true }).waitFor()
  assert.equal(await review().locator('.review-month-chart button').count(), 12)
  assert.match(await review().locator('.review-stat--completed strong').innerText(), new RegExp(`^${f.annual_expected.summary.completed_works}部作品$`))
  assert.equal(await btn('导出回顾长图').count(), 0)
  assert.equal(await review().locator('.review-month-unknown').getByText(/已计入年度总览/).count(), 1)
  const monthCounts = await review().locator('.review-month-value').allTextContents()
  assert.deepEqual(monthCounts, f.annual_expected.annual.months.map(row => `${row.summary.completed_rounds}轮`))
  const n = reads.length; await review().getByRole('group', { name: '月份分布指标' }).getByRole('button', { name: '重看完成轮次', exact: true }).click()
  assert.equal(reads.length, n)
  assert.deepEqual(await review().locator('.review-month-value').allTextContents(), f.annual_expected.annual.months.map(row => `${row.summary.rewatch_completed_rounds}轮`))
  await btn('查看本年月份待补充').click(); await ready()
  assert.equal(await review().locator('.review-entry').count(), f.annual_expected.annual.month_unknown.facts)
  await review().getByText('已计入本年统计，月份尚未确定；不会自动分配到某个月。', { exact: true }).first().waitFor()
  await review().getByRole('button', { name: '补充／更正日期', exact: true }).first().click()
  await page.getByRole('dialog', { name: '观看历程管理', exact: true }).waitFor()
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click(); await page.getByRole('dialog').waitFor({ state: 'detached' }); await ready()
  await section('全部作品').click(); await ready()
  await review().getByRole('navigation', { name: '底部回顾分页' }).getByRole('button', { name: '下一页回顾', exact: true }).click(); await ready()
  // Keep an unapplied annual draft as well as current page/metric/scroll on drill-down.
  await review().getByLabel('回顾年份', { exact: true }).fill('2025')
  const month = review().locator('[data-review-month="2026-10"]'); await month.scrollIntoViewIfNeeded()
  const scrollY = await page.evaluate(() => window.scrollY)
  await month.click(); await ready(); await review().getByRole('heading', { name: '2026年10月观看回顾', exact: true }).waitFor()
  assert.equal(await review().locator('.review-month-chart').count(), 0)
  assert.equal(await btn('导出回顾长图').count(), 1)
  await btn('返回 2026 年度回顾').click(); await ready()
  assert.equal(await review().getByLabel('回顾年份', { exact: true }).inputValue(), '2025')
  assert.match(await review().getByRole('navigation', { name: '底部回顾分页' }).innerText(), /2 \/ /)
  assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - scrollY) < 5)
  assert.equal(await review().getByRole('group', { name: '月份分布指标' }).getByRole('button', { name: '重看完成轮次', exact: true }).getAttribute('aria-pressed'), 'true')
  await review().locator('.review-month-details > summary').click()
  const detailMonth = review().locator('[data-review-month-detail="2026-09"]'); await detailMonth.scrollIntoViewIfNeeded()
  const detailScroll = await page.evaluate(() => window.scrollY)
  await detailMonth.click(); await ready(); await btn('返回 2026 年度回顾').click(); await ready()
  assert.equal(await review().locator('.review-month-details').getAttribute('open'), '')
  assert.ok(Math.abs(await page.evaluate(() => window.scrollY) - detailScroll) < 5)
  assert.equal(await detailMonth.evaluate(el => el === document.activeElement), true)
  await review().locator('.review-month-details > summary').click()
  // Returning after a real source change must re-read a coherent first page.
  await month.click(); await ready()
  const h = await api('get', `records/${f.year_only_record}/watch-history/`)
  await api('post', `records/${f.year_only_record}/watch-history/`, { action: 'update-round', version: h.version, operation_id: crypto.randomUUID(), round_number: 1, completed_on: '2026-04' })
  await btn('返回 2026 年度回顾').click(); await ready()
  await review().getByText('资料已更新，已重新读取本区第一页。', { exact: true }).waitFor()
  assert.match(await review().getByRole('navigation', { name: '底部回顾分页' }).innerText(), /1 \/ /)
  const live = await api('get', 'watch-review/?year=2026')
  assert.deepEqual(await review().locator('.review-month-value').allTextContents(), live.annual.months.map(row => `${row.summary.rewatch_completed_rounds}轮`))
  // Responsive chart, expanded monthly detail, and real hit areas.
  for (const [width, height] of [[320, 568], [390, 740], [844, 390], [1440, 1000]]) {
    await page.setViewportSize({ width, height }); await review().locator('.review-annual-heading').scrollIntoViewIfNeeded()
    assert.equal(await review().evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    const column = review().locator('.review-month-column').first(); await column.scrollIntoViewIfNeeded()
    assert.equal(await column.evaluate(el => { const r = el.getBoundingClientRect(); return r.height >= 44 && r.width >= 35 }), true)
    await page.screenshot({ path: `${out}/annual-${width}.png` })
    await review().locator('.review-month-details > summary').click()
    assert.equal(await review().evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    await review().locator('.review-month-details > summary').click()
  }
  await btn('按月份').click(); await review().getByLabel('回顾月份', { exact: true }).fill('2024-02'); await btn('查看回顾').click(); await ready()
  await review().getByRole('heading', { name: '2024年2月观看回顾', exact: true }).waitFor()
  await btn('自选时间段').click()
  assert.equal(await review().getByLabel('开始日期', { exact: true }).inputValue(), '2026-02-01')
  assert.equal(await review().getByLabel('结束日期', { exact: true }).inputValue(), '2026-03-31')
  await applyYear('2024'); await btn('上一年').click(); await ready(); await review().getByRole('heading', { name: '2023年观看回顾', exact: true }).waitFor()
  await btn('下一年').click(); await ready(); await review().getByRole('heading', { name: '2024年观看回顾', exact: true }).waitFor()
  await btn('回到今年').click(); await ready()
  await review().getByRole('heading', { name: `${currentYear()}年观看回顾`, exact: true }).waitFor(); await review().getByText('年度进行中', { exact: true }).waitFor()
  await applyYear('1000'); assert.equal(await btn('上一年').isDisabled(), true)
  await applyYear('9999'); assert.equal(await btn('下一年').isDisabled(), true)
  await review().getByText('这段时间暂无可归属的记录', { exact: true }).waitFor()
  await applyYear(f.year); await nav('手账').click(); await nav('回顾').click(); await ready()
  assert.equal(await review().getByLabel('回顾年份', { exact: true }).inputValue(), f.year)
  await page.getByRole('button', { name: /预览模式/ }).first().click(); await ready()
  await btn('查看本年月份待补充').click(); await ready()
  await review().getByRole('button', { name: '补充／更正日期', exact: true }).first().click()
  await page.getByRole('dialog', { name: '观看历程管理', exact: true }).waitFor()
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click(); await page.getByRole('dialog').waitFor({ state: 'detached' }); await ready()
  // An incomplete annual reply is an error, never a zeroed or partially rendered chart.
  await page.route('**/api/watch-review/entries/**', async route => { const response = await route.fetch(); const data = await response.json(); if (data.annual) data.annual.months.pop(); await route.fulfill({ response, json: data }) })
  await btn('刷新回顾').click(); await btn('重试回顾').waitFor(); assert.equal(await review().locator('.review-month-chart').count(), 0)
  await page.unroute('**/api/watch-review/entries/**'); await btn('重试回顾').click(); await ready()
  assert.deepEqual(writes, [])
  // Static production batch controls: every state has real internal spacing.
  await page.goto(base + '/tests/fixtures/recommendationBatchSpacing.html')
  for (const width of [320, 390, 844, 1440]) {
    await page.setViewportSize({ width, height: 850 })
    for (const state of ['ready', 'loading', 'error', 'exhausted', 'expired']) {
      await page.getByLabel('验收状态').selectOption(state)
      const box = page.locator('.rec-batch-actions')
      const layout = await box.evaluate(el => {
        const r = el.getBoundingClientRect(), style = getComputedStyle(el)
        return { padding: parseFloat(style.paddingLeft), fits: el.scrollWidth <= el.clientWidth + 1,
          inside: [...el.querySelectorAll('button,.rec-batch-progress')].every(n => { const b = n.getBoundingClientRect(); return b.left >= r.left + 10 && b.right <= r.right - 9 }) }
      })
      assert.ok(layout.padding >= (width > 767 ? 24 : 16)); assert.equal(layout.fits, true); assert.equal(layout.inside, true)
      if (state === 'ready') await page.screenshot({ path: `${out}/batch-${width}.png` })
    }
  }
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', motion, checks: ['annual totals and 12 months', 'year precision and explicit correction', 'metric changes without requests', 'guarded round trip with page/scroll/draft', 'month detail expansion and focus restored', 'real correction invalidates return', '320/390/844/1440 layouts', 'month/custom compatibility', 'year bounds/current/empty', 'preview correction', 'malformed response rejects chart', 'batch spacing across five states'], writes: writes.length }))
} catch (error) { console.log((await page.locator('body').innerText()).slice(-2500)); await page.screenshot({ path: out + '/failure.png' }); throw error }
finally { await context.close(); await browser.close() }
