// Full production components, synthetic HTTP backend, real native dialog/focus.
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
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } })
const page = await context.newPage(), errors = [], external = [], calls = []
const artifacts = path.resolve('../.tools/acceptance-8b-browser')
await mkdir(artifacts, { recursive: true })
let version = 0, failWrite = false, holdWrite = false, held = null, failHidden = false
const hidden = new Map()
const date = '2026-10-16T12:00:00Z'
const epoch = () => ({ algorithm_version: 'content.v1', profile_version: 'a'.repeat(64), preference_version: 0, feedback_version: version })
const token = () => version.toString(16).padStart(64, '0')
const row = id => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: `作品${id}`, title_jp: '', score: 7,
  tags: ['音乐'], season: '2026-04', status_type: 'TV', synopsis: '合成详情，非线上数据。', watch_status: null,
  evidence: null, reason: '', evidence_token: token(), evidence_context: epoch() })
context.on('page', p => p.on('pageerror', error => errors.push(error.message)))
page.on('pageerror', error => errors.push(error.message))
await context.route('**/*', async route => {
  const url = new URL(route.request().url()), method = route.request().method()
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (url.pathname === '/api/utils/anime-search/') return route.fulfill({ json: { status: 'success', results: [] } })
  if (!url.pathname.startsWith('/__8bqa__/')) return route.continue()
  calls.push({ path: url.pathname, method })
  const type = url.pathname.split('/').at(-1), body = method === 'POST' ? route.request().postDataJSON() : null
  if (type === 'list') {
    const ids = Array.from({ length: 30 }, (_, n) => 101 + n).filter(id => !hidden.has(id))
    const p = Number(url.searchParams.get('page') || 1)
    return route.fulfill({ json: { contract_version: 'recommendations.3b.v1', algorithm_version: 'content.v1', mode: 'preference_based',
      count: ids.length, page: p, page_size: 12, next_page: p * 12 < ids.length ? p + 1 : null,
      state_token: token(), evidence_context: epoch(), results: ids.slice((p - 1) * 12, p * 12).map(row) } })
  }
  if (type === 'detail') return route.fulfill({ json: row(Number(url.searchParams.get('id'))) })
  if (type === 'hidden') {
    if (failHidden) return route.fulfill({ status: 503, json: {} })
    const p = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('page_size') || 12)
    const rows = [...hidden].map(([id, revision]) => ({ bangumi_id: id, is_active: true, feedback_version: revision,
      title_zh: `作品${id}`, title_jp: '', season: '2026-04', status_type: 'TV', hidden_at: date, updated_at: date }))
    return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1', feedback_version: version,
      count: rows.length, page: p, page_size: size, next_page: p * size < rows.length ? p + 1 : null,
      results: rows.slice((p - 1) * size, p * size) } })
  }
  if (type === 'hide' || type === 'undo') {
    if (holdWrite) { await new Promise(resolve => { held = resolve }); holdWrite = false }
    if (failWrite) return route.fulfill({ status: 429, json: {} })
    if (body.expected_feedback_version !== version) return route.fulfill({ status: 409, json: { code: 'feedback_version_conflict' } })
    version++
    if (type === 'hide') hidden.set(body.bangumi_id, version); else hidden.delete(body.bangumi_id)
    return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1', action: type,
      operation_id: body.operation_id, feedback_version: version, operation_feedback_version: version, changed: true, replayed: false,
      feedback: { bangumi_id: body.bangumi_id, is_active: type === 'hide', feedback_version: version, hidden_at: date, updated_at: date } } })
  }
  throw new Error(`Unexpected synthetic endpoint: ${type}`)
})
const report = { scope: '8b-isolated-ui', live_writes: false, checks: [] }
const check = name => { report.checks.push({ name, passed: true }); console.log(`PASS: ${name}`) }
const load = async (p, id = 101) => { await p.goto(`${origin}/tests/fixtures/recommendationFeedbackHarness.html`); await p.getByRole('button', { name: `作品${id}的更多操作`, exact: true }).waitFor() }
const menu = (p, id, scope = p) => scope.getByRole('button', { name: `作品${id}的更多操作`, exact: true })
const hide = async (p, id, scope = p) => { await menu(p, id, scope).click(); await scope.getByRole('menuitem', { name: '不感兴趣', exact: true }).click() }
const waitFor = async fn => { for (let i = 0; i < 80; i++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 50)) } throw new Error('Condition timed out') }
try {
  await load(page)
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).click()
  const dialog = page.locator('dialog[open]')
  await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
  await page.getByText('未找到相关资源', { exact: false }).first().waitFor()
  failWrite = true; holdWrite = true
  await hide(page, 101, dialog)
  await waitFor(() => held !== null)
  assert.ok(await dialog.getByRole('button', { name: '作品101：+ 加入想看', exact: true }).isDisabled())
  held(); held = null
  await dialog.getByText('操作较频繁，请稍后重试。', { exact: true }).waitFor()
  assert.equal(await page.locator('#recResourceDrawer').count(), 1)
  check('failed feedback preserves detail/resource; same-work want disabled during pending write')
  failWrite = false
  // Confirmed feedback, but first-page refresh fails: do not report save failure.
  await page.evaluate(() => { feedbackQA.failList = true })
  await hide(page, 101, dialog)
  await page.getByText('已在推荐中隐藏《作品101》。', { exact: true }).waitFor()
  await page.getByText('反馈已保存，推荐更新失败。', { exact: true }).waitFor()
  assert.equal(await page.locator('dialog[open]').count(), 0)
  assert.equal(await page.locator('#recResourceDrawer').count(), 0)
  await page.evaluate(() => { feedbackQA.failList = false })
  await page.getByRole('button', { name: '重新加载推荐', exact: true }).click()
  await menu(page, 102).waitFor()
  check('confirmed hide invalidates whole detail/resources and surfaces refresh-only failure with retry')
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await page.getByText('已撤销隐藏，作品已恢复参与推荐的资格。', { exact: true }).waitFor()
  await menu(page, 101).waitFor()
  check('undo restores eligibility, reloads first page and never promises original position')
  await page.getByRole('button', { name: '查看已隐藏作品', exact: true }).click()
  assert.equal(await page.locator('dialog[open]').count(), 1)
  await page.getByText('还没有已隐藏作品。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  for (let id = 115; id < 129; id++) hidden.set(id, ++version)
  await page.getByRole('button', { name: '已隐藏作品', exact: true }).click()
  await page.getByText('第 1 页 · 14 部', { exact: true }).waitFor()
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await page.getByText('第 2 页 · 14 部', { exact: true }).waitFor()
  await page.locator('.rec-hidden-works li').first().getByRole('button', { name: '撤销隐藏', exact: true }).click()
  await page.getByText('第 1 页 · 13 部', { exact: true }).waitFor()
  assert.equal(await page.locator('dialog[open]').count(), 1)
  check('hidden drawer empty/pagination/undo reset, one native dialog only')
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  failHidden = true
  await page.getByRole('button', { name: '已隐藏作品', exact: true }).click()
  await page.getByRole('button', { name: '重新加载第一页', exact: true }).click()
  failHidden = false
  await page.getByRole('button', { name: '重新加载第一页', exact: true }).click()
  await page.getByText('第 1 页 · 13 部', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  const other = await context.newPage(); await load(other)
  await other.getByRole('button', { name: '查看作品101详情', exact: true }).click()
  await other.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
  await hide(page, 101)
  await page.getByText('已在推荐中隐藏《作品101》。', { exact: true }).waitFor()
  await waitFor(async () => await other.locator('dialog[open]').count() === 0)
  await menu(other, 102).waitFor()
  assert.equal(await menu(other, 101).count(), 0)
  check('cross-tab nonce rechecks server version, closes stale detail/resources and refreshes first page')
  await other.close()
  // Native top layer, touch targets and Escape/menu focus on narrow screens.
  await page.setViewportSize({ width: 390, height: 844 })
  await menu(page, 102).click(); await page.keyboard.press('Escape')
  assert.ok(await menu(page, 102).evaluate(node => node === document.activeElement))
  await page.getByRole('button', { name: '查看作品102详情', exact: true }).click()
  await menu(page, 102, page.locator('dialog[open]')).click(); await page.keyboard.press('Escape')
  assert.equal(await page.locator('dialog[open]').count(), 1)
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  await page.getByRole('button', { name: '已隐藏作品', exact: true }).click()
  const rect = await page.locator('dialog[open]').boundingBox()
  assert.ok(rect.width <= 390 && rect.x >= 0 && rect.height <= 844)
  await page.screenshot({ path: path.join(artifacts, 'hidden-mobile.png') })
  check('mobile drawer stays in viewport; Escape closes menu before dialog and restores trigger focus')
  await page.keyboard.press('Escape')
  await page.setViewportSize({ width: 1360, height: 900 })
  await page.screenshot({ path: path.join(artifacts, 'page-desktop.png'), fullPage: true })
  const timed = await context.newPage()
  await timed.clock.install()
  await load(timed, 102)
  await hide(timed, 102)
  const toast = timed.getByRole('complementary', { name: '推荐反馈提示', exact: true })
  await toast.hover()
  await timed.clock.fastForward(14000)
  assert.ok(await toast.isVisible())
  await timed.mouse.move(0, 0)
  await timed.clock.fastForward(14000)
  assert.equal(await toast.count(), 0)
  await hide(timed, 103)
  await timed.getByRole('button', { name: '撤销', exact: true }).focus()
  await timed.clock.fastForward(14000)
  assert.ok(await toast.isVisible())
  await timed.getByRole('button', { name: '已隐藏作品', exact: true }).focus()
  await timed.clock.fastForward(14000)
  assert.equal(await toast.count(), 0)
  await hide(timed, 104)
  await timed.getByText('已在推荐中隐藏《作品104》。', { exact: true }).waitFor()
  await hide(timed, 105)
  await timed.getByText('已在推荐中隐藏《作品105》。', { exact: true }).waitFor()
  assert.equal(await toast.count(), 1)
  assert.equal(await timed.getByText('已在推荐中隐藏《作品104》。', { exact: true }).count(), 0)
  check('12-second single toast pauses for hover/focus, resumes after exit and replaces previous feedback')
  await timed.getByRole('button', { name: '查看已隐藏作品', exact: true }).click()
  await page.evaluate(() => localStorage.setItem('anime_journal_access_token', 'different-synthetic-account'))
  await waitFor(async () => await timed.locator('dialog[open]').count() === 0)
  assert.equal(await timed.getByRole('complementary', { name: '推荐反馈提示', exact: true }).count(), 0)
  check('account change in another tab discards old dialogs, toast and private feedback controller state')
  await timed.close()
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  assert.ok(calls.every(call => call.path.startsWith('/__8bqa__/')))
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.failure = error.message; throw error }
finally { await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); await server.close() }
