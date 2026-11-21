// Actual Dashboard/shared/universe/featured routes; every business API is synthetic.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { defaultRatingPreferences, histogramHighlight, ratingPreferencesError } from '../src/utils/ratingPreferences.js'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5191'
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 950 }, reducedMotion: 'reduce' })
const page = await context.newPage(); page.setDefaultTimeout(12000)
const clone = value => JSON.parse(JSON.stringify(value)), errors = [], patches = []
let config = defaultRatingPreferences(), revision = 0, featuredSnapshot = null, nextFailure = 0, staleNextStats = false
const scores = [null, 0, 6, 7, 8, 8.5, 9, 9.5, 9.9, 10]
const histogram = scores.map(score => ({ score, count: 1 }))
const records = scores.map((score, index) => ({ id: index+1, watch_status: 'completed', personal_score: score, watched_eps: 12, total_eps: 12, custom_tags: [], review: '合成个人评价', anime: { id: index+1, chinese_title: `合成作品 ${index+1}`, japanese_title: 'Synthetic', airing_period: '2020-4', studio: '合成', episodes: '12', base_tags: ['日常'], description: '合成剧情' } }))
const stats = (preferences = config, version = revision) => ({ rating_preferences: clone(preferences), rating_revision: version, total_count: 10, completed_count: 10, watching_count: 0, pending_count: 0, average_score: '7.60', movie_count: 0, ova_count: 0, short_count: 0, masterpiece_count: histogramHighlight(histogram, preferences) })
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token', `synthetic.${btoa(JSON.stringify({ user_id: 1, exp: 4102444800 }))}.local-signature`))
await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url()), method = request.method()
  if (!url.pathname.startsWith('/api/')) return url.origin === base || ['cdn.tailwindcss.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com'].includes(url.hostname) ? route.continue() : route.abort()
  const path = url.pathname.slice(5)
  const respond = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) }).catch(() => {})
  if (path === 'rating-preferences/') {
    if (method === 'GET') return respond({ preferences: clone(config), revision, histogram })
    assert.equal(method, 'PATCH'); const body = request.postDataJSON(); patches.push(body)
    assert.equal(ratingPreferencesError(body.preferences), '')
    await new Promise(resolve => setTimeout(resolve, 200))
    if (nextFailure) { const failure = nextFailure; nextFailure = 0; return respond({ detail: failure === 429 ? '稍后重试' : '合成保存失败，草稿保留' }, failure) }
    if (body.revision !== revision) return respond({ detail: '配置已在其他页面更新。请重新读取，本页草稿仍保留。' }, 409)
    config = clone(body.preferences); revision++
    return respond({ preferences: clone(config), revision, histogram })
  }
  assert.equal(method, 'GET', `Unexpected business write: ${method} ${path}`)
  if (path === 'user/profile/' || path === 'user/me/') return respond({ username: 'QA', nickname: '合成主理人', public_status: 'approved', rating_preferences: clone(config), rating_revision: revision })
  if (path.endsWith('/stats/')) {
    if (staleNextStats && path === 'records/stats/') { staleNextStats = false; await new Promise(resolve => setTimeout(resolve, 700)); return respond(stats(defaultRatingPreferences(), 0)) }
    return respond(stats())
  }
  if (path === 'public-journal/users/') return respond([{ username: 'QA', nickname: '合成主理人', ...stats(), rating_preferences: clone(config), top_anime_poster_urls: [] }])
  if (path.endsWith('filter-options/')) return respond({ tags: ['日常'], years: ['2020'], quick_filters: [] })
  if (path === 'records/' || path.endsWith('/records/') || path === 'showcase/') {
    const filtered = records.filter(row => !url.searchParams.get('search') || row.anime.chinese_title.includes(url.searchParams.get('search')))
    return respond(url.searchParams.get('page_size') === 'all' ? filtered : { count: filtered.length, results: filtered })
  }
  if (path === 'featured-submissions/state/') return respond({ submissions: [], max_featured_limit: 3 })
  if (path === 'featured/') return respond([{ id: 77, chinese_title: '合成精选', japanese_title: 'Synthetic featured', score: 9.9, tags: ['日常'], description: '合成剧情', author_name: '合成作者', watch_status: 'completed', airing_period: '2020-4', studio: '合成公司', episodes: '12', review: '合成投稿长评', rating_preferences: clone(featuredSnapshot || config) }])
  if (path === 'notifications/poll/') return respond({ notifications: [] })
  return respond([])
})
page.on('pageerror', error => errors.push(error.message))
const modal = () => page.locator('.rating-preferences-modal')
const button = name => modal().getByRole('button', { name, exact: true })
const open = async () => {
  if (page.viewportSize().width < 768) await page.getByRole('button', { name: '☰ 菜单' }).click()
  await page.getByRole('button', { name: /📦 数据管理|🛠 更多功能/ }).click()
  await page.getByRole('button', { name: /★ 评分样式与统计/ }).click()
  await modal().getByLabel('星级上限', { exact: true }).waitFor()
}
const hit = async locator => {
  await locator.scrollIntoViewIfNeeded()
  assert.ok(await locator.evaluate(el => {
    const r = el.getBoundingClientRect(), x = r.left+r.width/2, y = r.top+r.height/2, hit = document.elementFromPoint(x,y)
    return x >= 0 && y >= 0 && x < innerWidth && y < innerHeight && (hit === el || el.contains(hit))
  }), 'Control must remain visible and clickable')
}
await mkdir('../.tools/rating-preferences-qa', { recursive: true })
try {
  await page.goto(`${base}/dashboard`); await open()
  assert.equal(patches.length, 0)
  await button('Bangumi 整数习惯').click()
  await modal().getByLabel('星级上限', { exact: true }).selectOption('10')
  await modal().getByLabel('第 1 档星数', { exact: true }).selectOption('10')
  await modal().getByLabel('第 1 档数字配色', { exact: true }).selectOption('teal')
  await modal().getByLabel('第 1 档星级配色', { exact: true }).selectOption('teal')
  await modal().getByLabel('第 1 档效果', { exact: true }).selectOption('glow')
  await button('统计显示').click()
  await modal().getByLabel('推荐统计名称', { exact: true }).fill('我的珍藏')
  await modal().getByLabel('最低分（含）', { exact: true }).fill('8')
  await modal().getByLabel('最高分（含）', { exact: true }).fill('9')
  for (const name of ['追番数量', '剧场版', 'OVA', '泡面番']) await modal().getByRole('checkbox', { name, exact: true }).uncheck()
  assert.ok((await modal().locator('.rating-preview-count').textContent()).includes('3 部'))
  assert.equal(patches.length, 0, 'Live preview never writes')
  await page.screenshot({ path: '../.tools/rating-preferences-qa/desktop.png' })
  staleNextStats = true
  await button('保存并应用').evaluate(el => { el.click(); el.click() })
  await modal().getByRole('status').filter({ hasText: '已保存并应用' }).waitFor()
  assert.equal(patches.length, 1); assert.equal(revision, 1)
  featuredSnapshot = clone(config)
  await button('关闭').click()
  await page.getByRole('button', { name: '👁 预览模式', exact: true }).click()
  await page.locator('#statMasterpiece').waitFor(); assert.equal(await page.locator('#statMasterpiece').textContent(), '3')
  assert.equal(await page.getByRole('region', { name: '番剧统计' }).locator('article').count(), 2)
  await page.waitForTimeout(900)
  assert.equal(await page.locator('#statMasterpiece').textContent(), '3', 'Late old statistics must not replace saved configuration')
  assert.equal(await page.locator('.personal-score[aria-label^="9.9 分"]').first().locator('.personal-score-number').evaluate(el => getComputedStyle(el).color), 'rgb(15, 118, 110)')
  await page.reload(); await page.locator('#statMasterpiece').waitFor(); assert.equal(await page.locator('#statMasterpiece').textContent(), '3')
  await open()
  await modal().getByLabel('第 1 档名称', { exact: true }).fill('另一页冲突草稿')
  revision++
  await button('保存并应用').click()
  await modal().getByRole('alert').filter({ hasText: '其他页面更新' }).waitFor()
  assert.equal(await modal().getByLabel('第 1 档名称', { exact: true }).inputValue(), '另一页冲突草稿')
  await button('重新读取（替换草稿）').click()
  await page.waitForFunction(() => document.querySelector('.rating-preferences-actions > span')?.textContent === '已与账号配置同步')
  nextFailure = 500
  await modal().getByLabel('第 1 档名称', { exact: true }).fill('连接失败草稿')
  await button('保存并应用').click()
  await modal().getByRole('alert').filter({ hasText: '合成保存失败' }).waitFor()
  const attempts = patches.length; await page.waitForTimeout(800); assert.equal(patches.length, attempts)
  nextFailure = 429
  await button('保存并应用').click()
  await modal().getByRole('alert').filter({ hasText: '操作较频繁' }).waitFor()
  const throttled = patches.length; await page.waitForTimeout(700); assert.equal(patches.length, throttled)
  await button('关闭').click(); await button('继续编辑').click()
  assert.equal(await modal().getByLabel('第 1 档名称', { exact: true }).inputValue(), '连接失败草稿')
  for (const [width, height] of [[390,640],[320,480],[844,390]]) {
    await page.setViewportSize({ width, height })
    await hit(button('保存并应用')); await hit(button('关闭'))
    await hit(modal().getByLabel('第 1 档名称', { exact: true }))
    assert.equal(await modal().evaluate(el => el.scrollWidth <= el.clientWidth), true)
    await page.screenshot({ path: `../.tools/rating-preferences-qa/mobile-${width}.png` })
  }
  await button('关闭').click(); await button('放弃修改并关闭').click()
  records[8].anime.chinese_title = '合成作品 9：超长中日文副标题与十星展示的手机布局验收'
  await page.goto(`${base}/dashboard`)
  await page.locator('#btnViewGrid').click()
  await page.setViewportSize({ width: 320, height: 640 })
  await page.locator('#viewGridContainer').waitFor()
  await page.locator('.memphis-grid-score').first().waitFor()
  assert.equal(await page.locator('.memphis-grid-score').count(), 10)
  assert.ok(await page.locator('.memphis-grid-score').evaluateAll(elements => elements.every(el => {
    const badge = el.getBoundingClientRect(), poster = el.parentElement.getBoundingClientRect()
    return badge.left >= poster.left - 1 && badge.right <= poster.right + 1 && el.scrollWidth <= el.clientWidth
  })), 'Ten-star badges fit inside narrow poster cards')
  await page.screenshot({ path: '../.tools/rating-preferences-qa/ten-star-grid.png' })
  await page.setViewportSize({ width: 1440, height: 950 })
  await page.locator('#btnViewList').click()
  await page.locator('#viewListContainer').waitFor()
  await page.setViewportSize({ width: 320, height: 640 })
  assert.ok(await page.locator('.memphis-anime-list .personal-score').evaluateAll(elements => elements.every(el => el.getBoundingClientRect().width <= 59)), 'Mobile score remains inside the reserved right column')
  await page.setViewportSize({ width: 1440, height: 950 })
  await page.goto(`${base}/shared/QA`)
  await page.locator('#statMasterpiece').waitFor(); assert.equal(await page.locator('#statMasterpiece').textContent(), '3')
  assert.equal(await page.getByRole('button', { name: /★ 评分样式与统计/ }).count(), 0)
  await page.getByText('作者评分口径 · 10 星制', { exact: true }).waitFor()
  assert.equal(await page.locator('.personal-score[aria-label^="9.9 分"]').first().locator('.personal-score-number').evaluate(el => getComputedStyle(el).color), 'rgb(15, 118, 110)')
  await page.goto(`${base}/universe`)
  await page.getByText('我的珍藏 (8–9)', { exact: true }).waitFor()
  config.tiers[0].color = 'blue' // Later author setting; the featured snapshot remains teal.
  await page.goto(`${base}/featured`)
  await page.getByText('合成精选', { exact: true }).waitFor()
  const featuredScore = page.locator('.personal-score[aria-label^="9.9 分"]').first()
  assert.equal(await featuredScore.locator('.personal-score-number').evaluate(el => getComputedStyle(el).color), 'rgb(15, 118, 110)')
  assert.equal(await featuredScore.locator('.personal-score-stars .is-lit').count(), 10)
  await page.getByText('合成精选', { exact: true }).click()
  await page.locator('.memphis-modal-piece .personal-score').waitFor()
  assert.equal(await page.locator('.memphis-modal-piece .personal-score-number').evaluate(el => getComputedStyle(el).color), 'rgb(15, 118, 110)')
  await page.goto(`${base}/dashboard`); await open()
  await button('仅数字').click(); assert.equal(await modal().locator('.rating-preview-feature .personal-score-stars').count(), 0)
  await modal().getByLabel('第 1 档起点', { exact: true }).fill('9.5')
  assert.equal(await button('保存并应用').isDisabled(), true)
  await button('项目默认').click()
  await button('＋ 添加档位').click(); assert.equal(await modal().locator('.rating-tier').count(), 9)
  await modal().getByRole('button', { name: '删除第 7 档', exact: true }).click()
  assert.equal(await modal().locator('.rating-tier').count(), 8)
  assert.equal(await modal().getByRole('button', { name: '删除第 8 档', exact: true }).isDisabled(), true)
  await page.evaluate(() => { localStorage.setItem('anime_journal_access_token', 'synthetic-other-account'); window.dispatchEvent(new Event('storage')) })
  await modal().waitFor({ state: 'detached' })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', checks: ['actual edit and preview menu entry', 'draft-only live count', 'ten-star and colors', 'duplicate save gate', 'stale stats revision protection', 'reload persistence', '409 preserves draft', '500 and 429 no auto replay', 'discard confirmation', 'mobile and landscape hit targets', 'owner public stats and legend', 'universe custom label', 'featured immutable author style and detail', 'invalid boundary blocks save', 'account isolation'], patchAttempts: patches.length }))
} catch (error) { await page.screenshot({ path: '../.tools/rating-preferences-qa/failure.png' }).catch(() => {}); throw error }
finally { await browser.close() }
