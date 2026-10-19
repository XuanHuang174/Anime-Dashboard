// Actual page, editor, transport and native dialog; all writes are local mocks.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { browsePage } from '../tests/fixtures/browseContract.js'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react()],
  server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage(), errors = [], external = [], writes = [], reads = []
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
const topics = ['日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑', '推理', '战斗',
  '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情', '美食', '魔法', '旅行', '偶像', '游戏', '青春', '励志', '剧情']
let tags = ['日常'], version = 0, serial = 0, saveMode = 'normal', listFail = false, empty = false, releaseSave, holdProfile = false
const profileResolvers = []
const preferences = () => ({ contract_version: 'recommendations.3a.v1', version, tags, available_tags: topics })
const profile = () => ({ contract_version: 'recommendations.3a.v1', eligible_sample_count: 63, usable_sample_count: 63,
  cold_start: false, sample_policy: { minimum_score: '8.5' }, tag_summary: topics.map(tag => ({ tag, sample_count: 6 })),
  ...(!empty ? { extraction: { trusted_profile: { usable_work_count: 63,
    tag_summary: topics.map(tag => ({ tag, sample_count: 6 })) },
  common_topics: { requires_confirmation: true, used_for_recommendation: false,
    tag_summary: ['音乐', '旅行', '美食', '青春', '游戏', '成长', '亲情', '励志'].map(tag => ({ tag, sample_count: 6 })) } } } : {}) })
const finish = async (route, options) => { try { await route.fulfill(options) } catch (error) { if (!route.request().failure()) throw error } }
await context.route('**/*', async route => {
  const url = new URL(route.request().url()), method = route.request().method()
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (!url.pathname.startsWith('/__9bqa__/')) return route.continue()
  const suffix = url.pathname.slice('/__9bqa__/recommendations/'.length)
  if (method !== 'GET') {
    assert.equal(method, 'PATCH'); assert.equal(suffix, 'preferences/')
    const body = route.request().postDataJSON(); writes.push(body)
    if (saveMode === 'hold') await new Promise(resolve => { releaseSave = resolve })
    if (saveMode === 'error') return finish(route, { status: 503, json: {} })
    if (body.expected_version !== version) return finish(route, { status: 409, json: {} })
    tags = body.tags; version++
    return finish(route, { json: preferences() })
  }
  reads.push(suffix)
  if (suffix === 'capabilities/') return route.fulfill({ json: { contract_version: 'recommendations.capabilities.v1',
    algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) } })
  if (suffix === 'profile/') {
    const data = profile()
    if (holdProfile) await new Promise(resolve => profileResolvers.push(resolve))
    return finish(route, { json: data })
  }
  if (suffix === 'preferences/') return route.fulfill({ json: preferences() })
  if (suffix === 'feedback/hidden/') return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1',
    feedback_version: 0, count: 0, results: [], page: 1, page_size: 12, next_page: null } })
  assert.equal(suffix, '')
  if (listFail) return route.fulfill({ status: 503, json: {} })
  const params = Object.fromEntries(url.searchParams)
  return route.fulfill({ json: browsePage({ snapshot: (++serial).toString(16).padStart(32, '0'),
    context: { algorithm_version: 'content.v1', profile_version: version.toString(16).padStart(64, '0'),
      preference_version: version, feedback_version: 0 }, category: params.category || 'all', style: params.style || 'balanced',
    filters: { type: params.type || 'all', minScore: params.min_score || '0', hideRecorded: params.hide_recorded === 'true' } }) })
})
const artifacts = path.resolve('../.tools/acceptance-preferences-workbench')
await mkdir(artifacts, { recursive: true })
const report = { scope: 'preferences-workbench-isolated', production_writes: false, checks: [] }
const check = name => { report.checks.push({ name, passed: true }); console.log('PASS: ' + name) }
const dialog = () => page.locator('.rec-dialog--preferences[open]')
const explicit = () => page.getByRole('region', { name: '我的明确偏好内容', exact: true })
const observed = () => page.getByRole('region', { name: '手账常见题材内容', exact: true })
const open = async () => {
  await page.getByRole('button', { name: '☷ 偏好设置', exact: true }).click()
  await dialog().waitFor()
  await page.waitForFunction(() => getComputedStyle(document.querySelector('dialog')).transform === 'none')
}
const close = async () => { await page.getByRole('button', { name: '取消，不保存', exact: true }).click(); await dialog().waitFor({ state: 'detached' }) }
const load = async () => { await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`); await page.locator('[data-rec-motion-id="101"]').waitFor() }
const selected = () => explicit().getByRole('checkbox', { checked: true }).count()
const isDisabled = locator => locator.evaluate(node => node.matches(':disabled'))
try {
  await load(); await open()
  const rects = await page.locator('.rec-preference-section').evaluateAll(nodes => nodes.map(node => {
    const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width }
  }))
  assert.ok((await dialog().boundingBox()).width > 1200)
  assert.ok(rects.every(rect => rect.width > 300 && Math.abs(rect.y - rects[0].y) < 1))
  assert.ok(rects[0].x < rects[1].x && rects[1].x < rects[2].x)
  const positions = await page.locator('.rec-preference-scroll').evaluateAll(nodes => nodes.map(node => {
    node.scrollTop = node.scrollHeight; return { top: node.scrollTop, overflow: getComputedStyle(node).overflowY }
  }))
  assert.ok(positions.every(row => row.top > 0 && row.overflow === 'auto'))
  const footerBefore = await page.locator('.rec-preference-footer').boundingBox()
  await explicit().evaluate(node => { node.scrollTop = 0 })
  assert.equal((await page.locator('.rec-preference-footer').boundingBox()).y, footerBefore.y)
  assert.equal(await dialog().evaluate(node => node.scrollHeight <= node.clientHeight + 1), true)
  await page.locator('.rec-preference-scroll').evaluateAll(nodes => nodes.forEach(node => { node.scrollTop = 0 }))
  await page.screenshot({ path: path.join(artifacts, 'desktop.png') })
  check('desktop has three generous columns; each scrolls independently without moving header or save footer')

  assert.equal(await page.getByRole('region', { name: '高分作品画像内容', exact: true }).getByRole('checkbox').count(), 0)
  await observed().getByRole('button', { name: '加入我的偏好', exact: true }).first().click()
  assert.equal(await selected(), 2); assert.equal(writes.length, 0)
  await page.getByRole('button', { name: '清空草稿中的明确偏好', exact: true }).click()
  assert.equal(await selected(), 0); assert.equal(writes.length, 0)
  await close(); await open(); assert.equal(await selected(), 1)
  assert.deepEqual(tags, ['日常']); assert.equal(writes.length, 0)
  check('observations and clearing only edit the draft; cancellation discards it and readonly profile cannot be checked')

  await page.getByRole('button', { name: '清空草稿中的明确偏好', exact: true }).click()
  for (const tag of topics.slice(0, 20)) await explicit().getByRole('checkbox', { name: tag, exact: true }).check()
  assert.equal(await selected(), 20)
  assert.equal(await isDisabled(explicit().getByRole('checkbox', { name: topics[20], exact: true })), true)
  assert.equal(await isDisabled(observed().getByRole('button', { name: '加入我的偏好', exact: true }).first()), true)
  await explicit().getByRole('checkbox', { name: topics[0], exact: true }).uncheck()
  assert.equal(await isDisabled(explicit().getByRole('checkbox', { name: topics[20], exact: true })), false)
  await close()
  check('twenty-tag limit protects new choices and observed actions while allowing removal')

  await open(); await explicit().getByRole('checkbox', { name: '音乐', exact: true }).check()
  saveMode = 'hold'; const before = writes.length
  const save = page.getByRole('button', { name: '保存喜好', exact: true })
  await save.evaluate(node => { node.click(); node.click(); node.click() })
  await page.getByRole('button', { name: '正在保存…', exact: true }).waitFor()
  await page.waitForFunction(() => window.browseQA.calls.some(row => row.method === 'PATCH'))
  assert.equal(writes.length, before + 1)
  assert.equal(await isDisabled(page.getByRole('button', { name: '关闭面板', exact: true })), true)
  await page.keyboard.press('Escape'); assert.equal(await dialog().count(), 1)
  releaseSave(); saveMode = 'normal'
  await dialog().waitFor({ state: 'detached' })
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.deepEqual(tags, ['日常', '音乐']); assert.equal(version, 1)
  assert.match(await page.locator('.rec-taste-metrics').innerText(), /手动偏好\s*2 个/)
  await open(); assert.equal(await selected(), 2); await close()
  check('rapid saves coalesce; pending modal cannot close; acknowledgement reloads profile, note and recommendation')

  await open(); await explicit().getByRole('checkbox', { name: '旅行', exact: true }).check()
  saveMode = 'error'; await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByRole('button', { name: '重试保存喜好', exact: true }).waitFor()
  assert.equal(await explicit().getByRole('checkbox', { name: '旅行', exact: true }).isChecked(), true)
  assert.equal(await page.locator('.rec-preference-footer [role="alert"]').isVisible(), true)
  saveMode = 'normal'; await page.getByRole('button', { name: '重试保存喜好', exact: true }).click()
  await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.ok(tags.includes('旅行'))
  check('failed saves retain draft and show footer-local retry without falsely acknowledging success')

  await open(); await explicit().getByRole('checkbox', { name: '科幻', exact: true }).check()
  tags = [...tags, '魔法']; version++ // Synthetic second-tab write, never production data.
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByRole('button', { name: '读取最新设置，保留草稿', exact: true }).click()
  await page.getByRole('button', { name: '合并本次修改到草稿', exact: true }).click()
  assert.equal(await explicit().getByRole('checkbox', { name: '魔法', exact: true }).isChecked(), true)
  assert.equal(await explicit().getByRole('checkbox', { name: '科幻', exact: true }).isChecked(), true)
  const conflictWrites = writes.length
  assert.equal(tags.includes('科幻'), false)
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(writes.length, conflictWrites + 1); assert.ok(tags.includes('魔法') && tags.includes('科幻'))
  check('version conflicts require reread and explicit merge; remote additions survive and merge is not autosaved')

  await open(); await explicit().getByRole('checkbox', { name: '美食', exact: true }).check()
  listFail = true; await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByText(/偏好已保存，推荐更新失败。已保存的设置不会丢失/).waitFor()
  const confirmedWrites = writes.length
  listFail = false; await page.getByRole('button', { name: '重试更新推荐', exact: true }).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(writes.length, confirmedWrites); assert.ok(tags.includes('美食'))
  check('save success plus recommendation failure stays acknowledged; recovery only reads, never repeats PATCH')

  await open(); const beforeClear = version
  await page.getByRole('button', { name: '清空草稿中的明确偏好', exact: true }).click()
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.deepEqual(tags, []); assert.equal(version, beforeClear + 1)
  await open(); assert.equal(await selected(), 0)
  assert.match(await page.getByRole('region', { name: '高分作品画像内容', exact: true }).innerText(), /可分析样本 63 部/)
  await close()
  check('confirmed clearing only changes explicit preferences; inferred profile remains intact')

  empty = true; await load(); await open()
  await page.getByText(/画像摘要暂不可用，重新读取后展示/).waitFor()
  await page.getByText('观察摘要暂不可用，请重新读取。', { exact: true }).waitFor()
  assert.equal(await observed().getByRole('button').count(), 0)
  await close(); empty = false
  check('missing summaries stay unavailable, not fabricated zeroes or unconfirmed suggestions')

  const noWrites = writes.length
  for (const size of [{ width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 320, height: 640 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(size); await open()
    const layout = await dialog().evaluate(node => {
      const columns = node.querySelector('.rec-preference-columns'), footer = node.querySelector('.rec-preference-footer')
      const r = node.getBoundingClientRect(), f = footer.getBoundingClientRect()
      return { overflow: node.scrollWidth > node.clientWidth + 1, x: r.x, right: r.right, bottom: r.bottom,
        display: getComputedStyle(columns).display, scroll: getComputedStyle(columns).overflowY, footerBottom: f.bottom,
        nested: [...node.querySelectorAll('.rec-preference-scroll')].map(n => getComputedStyle(n).overflowY) }
    })
    assert.equal(layout.display, 'block'); assert.equal(layout.scroll, 'auto')
    assert.equal(layout.overflow, false); assert.ok(layout.x >= 0 && layout.right <= size.width && layout.bottom <= size.height)
    assert.ok(layout.nested.every(value => value === 'visible'))
    const footerY = (await page.locator('.rec-preference-footer').boundingBox()).y
    await page.locator('.rec-preference-columns').evaluate(node => { node.scrollTop = node.scrollHeight })
    assert.equal((await page.locator('.rec-preference-footer').boundingBox()).y, footerY)
    assert.equal(await page.getByRole('button', { name: '保存喜好', exact: true }).isVisible(), true)
    await page.screenshot({ path: path.join(artifacts, `viewport-${size.width}-${size.height}.png`) })
    await close()
  }
  assert.equal(writes.length, noWrites)
  check('tablet, narrow phone and short landscape use one content scroller with visible safe-area save controls and no overflow')

  await page.setViewportSize({ width: 1440, height: 900 }); await open()
  const closeButton = page.getByRole('button', { name: '关闭面板', exact: true })
  await closeButton.focus(); await page.keyboard.press('Shift+Tab')
  assert.equal(await page.getByRole('button', { name: '保存喜好', exact: true }).evaluate(node => node === document.activeElement), true)
  await page.keyboard.press('Tab'); assert.equal(await closeButton.evaluate(node => node === document.activeElement), true)
  const trustedRegion = page.getByRole('region', { name: '高分作品画像内容', exact: true })
  await trustedRegion.focus(); await trustedRegion.evaluate(node => { node.scrollTop = 0 })
  await page.keyboard.press('End'); await page.waitForFunction(() => document.querySelector('[aria-label="高分作品画像内容"]').scrollTop > 0)
  await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'detached' })
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(await page.getByRole('button', { name: '☷ 偏好设置', exact: true }).evaluate(node => node === document.activeElement), true,
    await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 500)))
  assert.equal(await page.evaluate(() => document.body.style.overflow), '')
  await page.emulateMedia({ reducedMotion: 'reduce' }); await open()
  assert.equal(await dialog().evaluate(node => getComputedStyle(node).transform), 'none')
  await close()
  assert.equal(writes.length, noWrites)
  check('keyboard scroll regions, native modal focus loop, Escape return and reduced motion stay intact')

  await open(); holdProfile = true
  await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'detached' })
  await page.waitForFunction(() => document.querySelector('.rec-load-hero'))
  const back = page.getByRole('link', { name: '← 返回我的手账', exact: true })
  await back.focus()
  holdProfile = false; for (const done of profileResolvers.splice(0)) done()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(await back.evaluate(node => node === document.activeElement), true)
  await page.getByRole('button', { name: '调整明确喜好 →', exact: true }).click()
  await dialog().waitFor(); await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'detached' })
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(await page.getByRole('button', { name: '调整明确喜好 →', exact: true }).evaluate(node => node === document.activeElement), true,
    await page.evaluate(() => document.activeElement?.outerHTML.slice(0, 500)))
  assert.equal(writes.length, noWrites)
  check('delayed close reread never steals new user focus; taste-note entry restores its own focus')
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  report.result = 'passed'; report.synthetic_patches = writes.length; report.synthetic_reads = reads.length
} catch (error) { report.result = 'failed'; report.failure = error.stack; throw error }
finally {
  releaseSave?.()
  for (const done of profileResolvers.splice(0)) done()
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
