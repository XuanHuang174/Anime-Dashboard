// Real local annual API, real PNG/ZIP downloads, synthetic private data only.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/watch-review-d3-fixture.json', 'utf8')), base = 'http://127.0.0.1:5191', out = '../.tools/watch-review-d3-qa'
assert.match(f.username, /^plans-b2-/); await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, reducedMotion: 'reduce' })
await context.addInitScript(({ token, user_id }) => { localStorage.setItem('anime_journal_access_token', token); localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'preview') }, f)
await context.route('https://media.xh-anime.com/**', route => route.abort())
const page = await context.newPage(); page.setDefaultTimeout(30000)
const errors = [], downloads = [], writes = [], reads = [], coverReads = []
page.on('pageerror', e => errors.push(e.message)); page.on('download', d => downloads.push(d))
page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()); if (r.url().includes('/api/watch-review/')) reads.push(new URL(r.url())); if (r.url().includes('/api/utils/image-proxy/')) coverReads.push(r.url()) })
await page.route('**/api/utils/image-proxy/**', route => route.fulfill({ status: 503, json: {} }))
const review = () => page.getByRole('region', { name: '私人观看回顾工作区' }), dialog = () => page.getByRole('dialog', { name: '导出观看回顾' })
const stage = () => page.locator('.review-export-stage'), btn = name => dialog().getByRole('button', { name, exact: true })
const ready = async () => { await review().locator('.review-summary[aria-busy=false]').waitFor(); await review().locator('.review-error').waitFor({ state: 'detached' }) }
const open = async () => { await review().getByRole('button', { name: '导出回顾长图', exact: true }).click(); await dialog().waitFor() }
const prepare = async () => { await btn('读取并预览完整内容').click(); await btn('下载回顾长图').waitFor(); assert.equal(await btn('下载回顾长图').isEnabled(), true) }
const close = async () => { await btn('关闭面板').click(); await dialog().waitFor({ state: 'detached' }) }
const applyYear = async year => { await review().getByLabel('回顾年份', { exact: true }).fill(year); await review().getByRole('button', { name: '查看回顾', exact: true }).click(); await ready() }
const api = async (method, path, data) => { const r = await context.request[method](base + '/api/' + path, { headers: { Authorization: `Bearer ${f.token}` }, ...(data ? { data } : {}) }); assert.equal(r.status(), 200, await r.text()); return r.json() }
try {
  await page.goto(base + '/dashboard'); await page.getByRole('navigation', { name: '私人手账工作区' }).getByRole('button', { name: '回顾', exact: true }).click()
  await review().getByRole('button', { name: '按年度', exact: true }).click(); await applyYear(f.year)
  await review().getByRole('group', { name: '回顾内容' }).getByRole('button', { name: '本年月份待补充', exact: true }).click(); await ready()
  await review().getByLabel('回顾年份', { exact: true }).fill('2025') // unapplied draft must not change export
  const offset = reads.length; await open()
  for (const label of ['统计摘要', '十二个月分布／月份待补充说明', '完整作品清单']) assert.equal(await dialog().getByLabel(label, { exact: true }).isChecked(), true)
  for (const label of ['完成轮次', '随记与补录／轮次备注']) assert.equal(await dialog().getByLabel(label, { exact: true }).isChecked(), false)
  await dialog().getByLabel('包含海报（最多24张，失败或超限显示占位）', { exact: true }).check(); await prepare()
  assert.equal(coverReads.length, 24)
  await dialog().getByText(/张海报使用占位/).waitFor()
  assert.equal(await stage().getByText('D3 年度分页 100', { exact: true }).count(), 1)
  assert.equal(await stage().locator('.review-export-month-table tbody tr').count(), 12)
  assert.deepEqual(await stage().locator('.review-export-month > strong').allTextContents(), f.annual_expected.annual.months.map(row => String(row.summary.completed_rounds)))
  const text = await stage().innerText()
  assert.match(text, /2026年观看回顾/); assert.match(text, /属于本年，月份待补充/)
  assert.equal(text.includes('D3 私人轮次备注'), false); assert.equal(text.includes('D3 年度私人随记'), false)
  assert.equal(text.includes(f.annual_expected.snapshot), false)
  const exportReads = reads.slice(offset)
  assert.ok(exportReads.some(url => url.searchParams.get('section') === 'works' && url.searchParams.get('page') === '2'))
  assert.ok(exportReads.every(url => url.searchParams.get('year') === f.year && !url.searchParams.has('start') && url.searchParams.has('snapshot')))
  const pages = await stage().locator('.review-export-sheet').count()
  assert.ok(pages > 1)
  assert.equal(await stage().locator('.review-export-sheet').evaluateAll(nodes => nodes.every(n => n.scrollHeight <= 5000)), true)
  const zipTask = page.waitForEvent('download', { timeout: 120000 }); await btn('下载回顾长图').click(); const zip = await zipTask
  assert.equal(zip.suggestedFilename(), '2026年观看回顾.zip'); await zip.saveAs(out + '/annual-full.zip')
  assert.equal(await stage().locator('.review-export-sheet').count(), pages)
  await writeFile(out + '/download-metadata.json', JSON.stringify({ pages, works: f.annual_expected.summary.related_works }))
  await close()
  // A complete annual overview remains one PNG; mobile preview never clips it horizontally.
  await open(); await dialog().getByLabel('完整作品清单', { exact: true }).uncheck(); await prepare()
  assert.equal(await stage().locator('.review-export-sheet').count(), 1)
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 }); await dialog().locator('.review-export-preview').scrollIntoViewIfNeeded()
    assert.equal(await dialog().locator('.review-export-preview').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    assert.equal(await dialog().evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    await page.screenshot({ path: `${out}/annual-preview-${width}.png` })
  }
  const pngTask = page.waitForEvent('download'); await btn('下载回顾长图').click(); const png = await pngTask
  assert.equal(png.suggestedFilename(), '2026年观看回顾_01.png'); await png.saveAs(out + '/annual-overview.png')
  await dialog().getByLabel('十二个月分布／月份待补充说明', { exact: true }).uncheck(); await prepare()
  assert.equal(await stage().locator('.review-export-month-table').count(), 0)
  await dialog().getByLabel('完成轮次', { exact: true }).check(); await dialog().getByLabel('随记与补录／轮次备注', { exact: true }).check(); await prepare()
  const privateText = await stage().innerText()
  assert.ok(privateText.includes('D3 私人轮次备注')); assert.ok(privateText.includes('D3 年度私人随记'))
  assert.match(privateText, /2026年（仅年份）/)
  // A successful proxy response can still contain corrupt image bytes.
  await page.unroute('**/api/utils/image-proxy/**')
  await page.route('**/api/utils/image-proxy/**', route => route.fulfill({ status: 200, json: { data_url: 'data:image/png;base64,bm90LWFuLWltYWdl' } }))
  await dialog().getByLabel('包含海报（最多24张，失败或超限显示占位）', { exact: true }).check(); await prepare()
  await dialog().getByText(/张海报使用占位/).waitFor()
  assert.equal(await stage().locator('img').count(), 0)
  assert.ok(await stage().getByText('海报未载入', { exact: true }).count() > 0)
  // Same annual totals, different month attribution: real server correction blocks the old report.
  const history = await api('get', `records/${f.year_only_record}/watch-history/`)
  await api('post', `records/${f.year_only_record}/watch-history/`, { action: 'update-round', version: history.version, operation_id: crypto.randomUUID(), round_number: 1, completed_on: '2026-04' })
  const count = downloads.length; await btn('下载回顾长图').click(); await dialog().getByRole('alert').waitFor(); assert.equal(downloads.length, count)
  await close(); await review().getByRole('button', { name: '刷新回顾', exact: true }).click(); await ready(); await open()
  // Cancelling while reading must not resurrect a preview or initiate a download.
  let held = false
  await page.route('**/api/watch-review/entries/**', route => { if (!held) { held = true; return new Promise(resolve => setTimeout(() => route.continue().catch(() => {}).then(resolve), 300)) }; return route.continue() })
  await btn('读取并预览完整内容').click(); await btn('取消并关闭').click(); await dialog().waitFor({ state: 'detached' }); await page.waitForTimeout(500)
  assert.equal(downloads.length, count); assert.equal(await stage().count(), 0); await page.unroute('**/api/watch-review/entries/**')
  await open(); await prepare(); await btn('下载回顾长图').click(); await btn('取消并关闭').click(); await dialog().waitFor({ state: 'detached' }); await page.waitForTimeout(800)
  assert.equal(downloads.length, count)
  // Leap year and a completely empty year still produce complete 12-month reports.
  await page.setViewportSize({ width: 1440, height: 1000 })
  for (const year of ['2024', '9999']) {
    await applyYear(year); await open(); await dialog().getByLabel('完整作品清单', { exact: true }).uncheck(); await prepare()
    assert.equal(await stage().locator('.review-export-month-table tbody tr').count(), 12)
    assert.match(await stage().innerText(), new RegExp(`${year}-01-01 至 ${year}-12-31`))
    if (year === '2024') assert.equal((await stage().locator('.review-export-month > strong').allTextContents())[1], '1')
    else assert.ok((await stage().locator('.review-export-month > strong').allTextContents()).every(n => n === '0'))
    await close()
  }
  assert.deepEqual(errors, []); assert.deepEqual(writes, [])
  console.log(JSON.stringify({ result: 'passed', pages, checks: ['applied year and original proof', 'actual second API page', '12 months and year-only explanation', '24-cover bound/fallback including corrupt bytes', 'full ZIP and single overview PNG', 'preview pagination equals output', '320/390/1440 fit', 'private text opt-in', 'real month correction blocks stale export', 'cancel read and render', 'leap/empty years', 'no page writes/errors'], downloads: downloads.length }))
} catch (error) { console.log((await page.locator('body').innerText()).slice(-2500)); await page.screenshot({ path: out + '/failure.png' }); throw error }
finally { await context.close(); await browser.close() }
