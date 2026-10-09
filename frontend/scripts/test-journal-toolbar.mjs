// Run against local Vite only. All API calls are mocked; no live account or service is used.
// PLAYWRIGHT_MODULE may point to a separately installed/bundled Playwright module.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5177'
assert.ok(/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base), 'Test URL must be localhost')
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
const page = await context.newPage()
page.setDefaultTimeout(10000)
const errors = []
const searches = []
const mutations = []
const profile = { username: 'QA', nickname: '测试手账', public_status: 'approved', showcase_subtitle: '本地界面测试' }
const records = [
  { id: 1, watch_status: 'watching', watch_status_display: '在看', watched_eps: 6, total_eps: 12, personal_score: '9.6', review: '第一行,"赞"\n第二行',
    anime: { id: 1, chinese_title: '《孤独摇滚！ 第二季》', japanese_title: 'ぼっち', airing_period: '2026-7', studio: 'Studio', episodes: '12', base_tags: ['日常'] } },
  { id: 2, watch_status: 'completed', watch_status_display: '看过', personal_score: '8.1', review: '未筛选',
    anime: { id: 2, chinese_title: '另一部番剧', japanese_title: 'Other', airing_period: '2026-4', studio: 'Studio', episodes: '12', base_tags: ['音乐'] } },
]
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token', 'local-mock-token'))
await context.route('**/*', async (route) => {
  const url = new URL(route.request().url())
  if (url.pathname.startsWith('/api/')) {
    const path = url.pathname.replace('/api/', '')
    let data = []
    if (route.request().method() !== 'GET') mutations.push(path)
    if (path === 'user/profile/' || path === 'user/me/') data = profile
    else if (path.endsWith('/profile/')) data = profile
    else if (path.endsWith('/records/') || path === 'records/') {
      const keyword = url.searchParams.get('search') || ''
      const filtered = records.filter((record) => record.anime.chinese_title.includes(keyword))
      data = url.searchParams.get('page_size') === 'all' ? filtered : { count: filtered.length, results: filtered.slice(0, 12) }
    }
    else if (path.endsWith('filter-options/')) data = { tags: ['日常', '音乐'], years: ['2026'] }
    else if (path.endsWith('stats/')) data = {}
    else if (path === 'featured-submissions/state/') data = { submissions: [], max_featured_limit: 3 }
    else if (path === 'utils/anime-search/') {
      searches.push(url.searchParams.get('keyword'))
      if (url.searchParams.get('keyword') === '慢请求') await new Promise((resolve) => setTimeout(resolve, 650))
      data = { status: 'success', results: [{ title: `资源 ${url.searchParams.get('keyword')}`, size: '1 GB', publisher: '测试发布者',
        magnet: 'magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678' }] }
    } else if (path === 'export/') {
      return route.fulfill({ contentType: 'application/json', headers: { 'content-disposition': 'attachment; filename="test.json"' }, body: JSON.stringify(records) })
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) }).catch(() => {})
  }
  // Match the application's existing public style/font dependencies, never its live API.
  if (['cdn.tailwindcss.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com'].includes(url.hostname)) return route.continue()
  if (url.origin !== base) return route.abort()
  return route.continue()
})
page.on('pageerror', (error) => errors.push(error.message))
const button = (text) => page.getByRole('button', { name: text.replace(/[▾▴]/g, ''), exact: true })
const closeDrawer = async () => {
  await page.getByRole('button', { name: '关闭资源抽屉', exact: true }).click()
  await page.locator('.journal-resource-overlay').waitFor({ state: 'detached' })
}
try {
  await page.goto(`${base}/dashboard`)
  await page.waitForFunction(() => getComputedStyle(document.querySelector('header')).borderTopWidth === '4px')
  await button('📦 数据管理▾').waitFor()
  await page.locator('#animeTableBody tr').first().waitFor()
  await button('📦 数据管理▾').click()
  assert.equal(await page.locator('.journal-dropdown-panel:visible').evaluate((el) => getComputedStyle(el).zIndex), '9999')
  await page.getByText('本地界面测试', { exact: true }).click()
  assert.equal(await page.locator('.journal-dropdown-panel:visible').count(), 0)

  await button('🧲 寻找资源').click()
  await page.locator('#journalResourceDrawer-keyword').waitFor({ state: 'visible' })
  assert.equal(await page.locator('#journalResourceDrawer-keyword').inputValue(), '')
  assert.equal(searches.length, 0, 'Global launch must not request API')
  await page.locator('#journalResourceDrawer-keyword').fill('手动关键字')
  await page.locator('#journalResourceDrawer-keyword').press('Enter')
  await page.getByRole('heading', { name: '资源 手动关键字' }).waitFor()
  await closeDrawer()

  await button('🧲 寻找资源').click()
  await page.locator('#journalResourceDrawer-keyword').waitFor({ state: 'visible' })
  await page.locator('#journalResourceDrawer-keyword').fill('慢请求')
  await page.locator('#journalResourceDrawer-keyword').press('Enter')
  await page.getByRole('status', { name: '正在搜索资源' }).waitFor()
  await closeDrawer()
  await button('🧲 寻找资源').click()
  await page.locator('#journalResourceDrawer-keyword').waitFor({ state: 'visible' })
  await page.waitForTimeout(700)
  assert.equal(await page.locator('.journal-resource-overlay .resource-card').count(), 0, 'Aborted stale request cannot repopulate results')
  assert.equal(await page.locator('#journalResourceDrawer-keyword').inputValue(), '')
  await closeDrawer()
  // Keep the existing sequence checks below relative to these additional requests.
  const initialSearchCount = searches.length
  await page.getByRole('button', { name: `寻找${records[0].anime.chinese_title}的资源`, exact: true }).last().click()
  await page.getByRole('heading', { name: '资源 孤独摇滚' }).waitFor()
  assert.equal(searches.at(-1), '孤独摇滚')
  await closeDrawer()
  await button('🧲 寻找资源').click()
  await page.locator('#journalResourceDrawer-keyword').waitFor({ state: 'visible' })
  assert.equal(await page.locator('#journalResourceDrawer-keyword').inputValue(), '')
  assert.equal(await page.locator('.journal-resource-overlay .resource-card').count(), 0)
  assert.equal(searches.length, initialSearchCount + 1)
  await closeDrawer()

  await page.getByPlaceholder('输入番剧中文或日文名...').fill('孤独')
  await button('📦 数据管理▾').click()
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: /导出当前数据 \(CSV\)/ }).click()
  const download = await downloaded
  assert.match(download.suggestedFilename(), /^我的番剧手账_\d{8}\.csv$/)
  const csv = await readFile(await download.path(), 'utf8')
  assert.ok(csv.startsWith('\uFEFF'))
  assert.ok(csv.includes('孤独摇滚'))
  assert.ok(!csv.includes('另一部番剧'))
  assert.ok(csv.includes('"第一行,""赞""\n第二行"'))
  await button('📦 数据管理▾').click()
  const jsonDownloaded = page.waitForEvent('download')
  await button('📤 导出数据 (JSON)').click()
  assert.equal((await jsonDownloaded).suggestedFilename(), 'test.json')
  await button('📦 数据管理▾').click()
  const chooserEvent = page.waitForEvent('filechooser')
  await button('📥 导入模板 (JSON)').click()
  const chooser = await chooserEvent
  await chooser.setFiles({ name: 'local-test.json', mimeType: 'application/json', buffer: Buffer.from('[]') })
  await page.getByRole('heading', { name: '危险操作确认' }).waitFor()
  await button('取消').click()
  assert.equal(mutations.length, 0, 'Cancel must not import or modify records')
  await page.getByPlaceholder('输入番剧中文或日文名...').fill(' 孤独 ')
  await page.getByRole('combobox', { name: '选择每页显示数量' }).selectOption('12')
  await button('📦 数据管理▾').click()
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find((el) => el.textContent.includes('导出当前数据 (CSV)'))
    return button && !button.disabled
  })
  const pageCsvEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: /导出当前数据 \(CSV\)/ }).click()
  const pageCsv = await readFile(await (await pageCsvEvent).path(), 'utf8')
  assert.ok(pageCsv.includes('孤独摇滚') && !pageCsv.includes('另一部番剧'), 'Paginated export uses current page and accepts trimmed search')
  await page.getByRole('combobox', { name: '选择每页显示数量' }).selectOption('all')
  await page.getByPlaceholder('输入番剧中文或日文名...').fill('孤独')

  for (const width of [768, 1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.waitForTimeout(100)
    const boxes = await page.locator('.journal-toolbar__items > *').evaluateAll((els) => els.map((el) => {
      const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top }
    }))
    assert.ok(boxes.every((box) => box.left >= 0 && box.right <= width), `Edit toolbar fits ${width}`)
    assert.ok(Math.max(...boxes.map((b) => b.top)) - Math.min(...boxes.map((b) => b.top)) < 8, 'Edit single-row toolbar')
  }
  if (process.env.QA_ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.QA_ARTIFACT_DIR, 'journal-edit.png') })

  await button('👁 预览模式').click()
  await button('🛠 更多工具▾').waitFor()
  assert.equal(await page.locator('.dashboard-preview-actions').count(), 0, 'No empty full-width control frame')
  assert.equal(await page.locator('.journal-recommendation-banner').count(), 1, 'Private preview teaser is present')
  const editButton = button('📝 编辑模式')
  const editStyle = await editButton.evaluate((el) => {
    const style = getComputedStyle(el)
    return { border: style.borderTopWidth, height: el.getBoundingClientRect().height,
      background: style.backgroundColor, shadow: style.boxShadow }
  })
  assert.equal(editStyle.border, '2px')
  assert.equal(editStyle.height, 42)
  assert.equal(editStyle.background, 'rgb(255, 224, 102)')
  assert.ok(editStyle.shadow.includes('2px 2px'))
  await editButton.hover()
  await page.waitForTimeout(170)
  assert.ok((await editButton.evaluate((el) => getComputedStyle(el).boxShadow)).includes('3px 3px'))
  await page.mouse.down()
  await page.waitForTimeout(170)
  assert.ok((await editButton.evaluate((el) => getComputedStyle(el).boxShadow)).includes('1px 1px'))
  await page.mouse.move(0, 0)
  await page.mouse.up()
  if (process.env.QA_ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.QA_ARTIFACT_DIR, 'journal-preview-desktop.png') })
  assert.equal(await button('📦 数据管理▾').count(), 0)
  await button('🛠 更多工具▾').click()
  await button('取消分享').click()
  await page.getByRole('heading', { name: '确认取消分享？' }).waitFor()
  await page.keyboard.press('Escape')
  assert.equal(await page.locator('.journal-dropdown-panel:visible').count(), 0)
  await page.getByRole('button', { name: /详情/ }).first().click()
  await page.locator('.detail-resource-stage').waitFor({ state: 'visible' })
  await page.locator('.detail-resource-stage').getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
  await page.locator('#animeResourceDrawer .resource-card').waitFor({ state: 'visible' })
  assert.equal(await page.locator('#animeResourceDrawer-keyword').inputValue(), '孤独摇滚')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await page.locator('.detail-resource-stage').waitFor({ state: 'detached' })

  for (const width of [320, 375, 767, 768, 1024, 1100, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.waitForTimeout(100)
    const bannerBox = await page.locator('.journal-recommendation-banner').boundingBox()
    assert.ok(bannerBox.x >= 0 && bannerBox.x + bannerBox.width <= width, `Teaser fits ${width}`)
    if (width >= 1100) {
      const identityBox = await page.locator('.journal-preview-identity').boundingBox()
      const controlsBox = await page.locator('.journal-preview-controls').boundingBox()
      assert.ok(Math.abs(identityBox.y - controlsBox.y) < 1, 'Desktop columns align at the top')
      assert.ok(identityBox.x + identityBox.width <= controlsBox.x, 'Header columns do not overlap')
    }
    if (width < 768) {
      await button('☰ 菜单▾').click()
      await button('🧲 寻找资源').waitFor({ state: 'visible' })
      const panel = await page.locator('.journal-dropdown-panel:visible').boundingBox()
      assert.ok(panel.x >= 0 && panel.x + panel.width <= width, `Mobile panel fits ${width}`)
      assert.equal(await button('🧲 寻找资源').evaluate((el) => getComputedStyle(el).borderTopWidth), '3px', 'Portaled button preserves Memphis CSS variables')
      await page.keyboard.press('Escape')
    } else {
      const boxes = await page.locator('.journal-toolbar__items > *').evaluateAll((els) => els.map((el) => {
        const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top }
      }))
      assert.ok(boxes.every((box) => box.left >= 0 && box.right <= width), `Toolbar fits ${width}`)
      assert.ok(Math.max(...boxes.map((b) => b.top)) - Math.min(...boxes.map((b) => b.top)) < 8, 'Single-row toolbar')
      const heights = await page.locator('.journal-toolbar__items > button').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height))
      assert.ok(heights.every((height) => height === 42), 'Preview navigation heights match')
    }
  }
  await page.setViewportSize({ width: 375, height: 900 })
  if (process.env.QA_ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.QA_ARTIFACT_DIR, 'journal-preview-mobile.png') })
  await button('☰ 菜单▾').click()
  await button('📝 编辑模式').click()
  await button('☰ 菜单▾').click()
  await button('📦 数据管理▾').click()
  await page.getByRole('button', { name: /导出当前数据 \(CSV\)/ }).waitFor({ state: 'visible' })
  await page.keyboard.press('Escape')
  if (process.env.QA_ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.QA_ARTIFACT_DIR, 'journal-mobile.png') })
  assert.equal(errors.length, 0, errors.join('\n'))
  await page.goto(`${base}/shared/QA`)
  await page.getByText('返回共创大厅', { exact: true }).waitFor()
  assert.equal(await page.locator('.journal-toolbar').count(), 0, 'Shared journals keep existing header')
  assert.equal(await page.locator('.journal-recommendation-banner').count(), 0, 'Teaser stays private')
  assert.equal(await page.locator('.journal-row-resource-button').count(), 0, 'Shared records have no private shortcut')
  console.log('PASS: dropdown/outside/CSV/JSON/import cancellation/share confirmation/global-row state isolation/responsive 320–1440px')
} catch (error) {
  console.log('Visible buttons:', await page.getByRole('button').allTextContents())
  console.log('Browser errors:', errors)
  throw error
} finally { await browser.close() }
