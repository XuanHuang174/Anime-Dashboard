// Full production components, synthetic HTTP backend, real native dialog/focus.
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
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } })
const page = await context.newPage(), errors = [], external = [], calls = []
const phase8c = process.env.FEEDBACK_ACCEPTANCE_PHASE === '8c'
const artifacts = path.resolve(phase8c ? '../.tools/acceptance-8c-browser' : '../.tools/acceptance-8b-browser')
await mkdir(artifacts, { recursive: true })
let version = 0, failWrite = false, holdWrite = false, held = null, failHidden = false
let dropAfterCommit = false, dropBeforeCommit = false, holdCategory = null, heldList = null, holdHiddenPage = null, heldHidden = null
const hidden = new Map(), workRevisions = new Map(), receipts = new Map(), writes = []
const finish = async (route, options) => { try { await route.fulfill(options) } catch (error) { if (!route.request().failure()) throw error } }
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
  if (url.pathname === '/api/utils/anime-search/') {
    if (url.searchParams.get('keyword') === 'offline') return route.abort('internetdisconnected')
    return route.fulfill({ json: { status: 'success', results: [] } })
  }
  if (!url.pathname.startsWith('/__8bqa__/')) return route.continue()
  calls.push({ path: url.pathname, method })
  const type = url.pathname.split('/').at(-1), body = method === 'POST' ? route.request().postDataJSON() : null
  if (type === 'list') {
    const category = url.searchParams.get('category') || 'all'
    const start = category === 'familiar' ? 201 : category === 'explore' ? 301 : 101
    const ids = Array.from({ length: 30 }, (_, n) => start + n).filter(id => !hidden.has(id))
    const p = Number(url.searchParams.get('page') || 1)
    const snapshot = { ...browsePage({ ids, row, category, offset: (p - 1) * 12, context: epoch(),
      snapshot: version.toString(16).padStart(32, '0'), makeCursor: offset => `feedback-${category}-${offset}` }), mode: 'preference_based' }
    if (holdCategory === category) await new Promise(resolve => { heldList = resolve })
    return finish(route, { json: snapshot })
  }
  if (type === 'detail') return route.fulfill({ json: row(Number(url.searchParams.get('id'))) })
  if (type === 'hidden') {
    if (failHidden) return route.fulfill({ status: 503, json: {} })
    const p = Number(url.searchParams.get('page') || 1), size = Number(url.searchParams.get('page_size') || 12)
    const rows = [...hidden].map(([id, revision]) => ({ bangumi_id: id, is_active: true, feedback_version: revision,
      title_zh: `作品${id}`, title_jp: '', season: '2026-04', status_type: 'TV', hidden_at: date, updated_at: date }))
    const snapshot = { contract_version: 'recommendations.feedback.v1', feedback_version: version,
      count: rows.length, page: p, page_size: size, next_page: p * size < rows.length ? p + 1 : null,
      results: rows.slice((p - 1) * size, p * size) }
    if (holdHiddenPage === p) await new Promise(resolve => { heldHidden = resolve })
    return finish(route, { json: snapshot })
  }
  if (type === 'hide' || type === 'undo') {
    writes.push({ type, body })
    if (holdWrite) { await new Promise(resolve => { held = resolve }); holdWrite = false }
    if (failWrite) return route.fulfill({ status: 429, json: {} })
    if (dropBeforeCommit) return route.abort('internetdisconnected')
    const old = receipts.get(body.operation_id)
    if (old) return finish(route, { json: { ...old, changed: false, replayed: true, feedback_version: version,
      feedback: { bangumi_id: body.bangumi_id, is_active: hidden.has(body.bangumi_id),
        feedback_version: workRevisions.get(body.bangumi_id), hidden_at: date, updated_at: date } } })
    if (body.expected_feedback_version !== version) return route.fulfill({ status: 409, json: { code: 'feedback_version_conflict' } })
    if (type === 'undo' && body.target_feedback_version !== workRevisions.get(body.bangumi_id)) return route.fulfill({ status: 409, json: { code: 'feedback_target_conflict' } })
    version++
    workRevisions.set(body.bangumi_id, version)
    if (type === 'hide') hidden.set(body.bangumi_id, version); else hidden.delete(body.bangumi_id)
    const ack = { contract_version: 'recommendations.feedback.v1', action: type,
      operation_id: body.operation_id, feedback_version: version, operation_feedback_version: version, changed: true, replayed: false,
      feedback: { bangumi_id: body.bangumi_id, is_active: type === 'hide', feedback_version: version, hidden_at: date, updated_at: date } }
    receipts.set(body.operation_id, ack)
    if (dropAfterCommit) { dropAfterCommit = false; return route.abort('internetdisconnected') }
    return finish(route, { json: ack })
  }
  throw new Error(`Unexpected synthetic endpoint: ${type}`)
})
const report = { scope: phase8c ? '8c-isolated-ui-network-regression' : '8b-isolated-ui', live_writes: false, checks: [] }
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
  const savedWriteCount = writes.length
  await page.getByRole('button', { name: '重新加载推荐', exact: true }).click()
  await menu(page, 102).waitFor()
  assert.equal(writes.length, savedWriteCount, 'A refresh-only retry must not POST feedback again')
  check('confirmed hide invalidates whole detail/resources and surfaces refresh-only failure with retry')
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await page.getByText('已撤销隐藏，作品已恢复参与推荐的资格。', { exact: true }).waitFor()
  await menu(page, 101).waitFor()
  check('undo restores eligibility, reloads first page and never promises original position')
  await page.getByRole('button', { name: '查看已隐藏作品', exact: true }).click()
  assert.equal(await page.locator('dialog[open]').count(), 1)
  await page.getByText('还没有已隐藏作品。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '关闭面板', exact: true }).click()
  for (let id = 115; id < 129; id++) { hidden.set(id, ++version); workRevisions.set(id, version) }
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
  if (phase8c) {
    await load(page, 106)
    holdWrite = true
    const initialWrites = writes.length
    await hide(page, 106)
    await waitFor(() => held !== null)
    await page.getByRole('button', { name: '查看作品106详情', exact: true }).click()
    const active = page.locator('dialog[open]')
    assert.ok(await menu(page, 106, active).isDisabled())
    await menu(page, 106, active).evaluate(node => { for (let i = 0; i < 30; i++) node.click() })
    await active.getByRole('button', { name: '作品106：+ 加入想看', exact: true }).evaluate(node => { for (let i = 0; i < 30; i++) node.click() })
    assert.equal(writes.length, initialWrites + 1)
    held(); held = null
    await page.getByText('已在推荐中隐藏《作品106》。', { exact: true }).waitFor()
    assert.equal(await page.locator('dialog[open]').count(), 0)
    check('8C rapid hero/detail duplicate clicks produce one POST and mutual write disabling')

    const beforeLostAck = version
    dropAfterCommit = true
    await hide(page, 107)
    await page.getByRole('button', { name: '重试确认原操作', exact: true }).waitFor()
    assert.equal(version, beforeLostAck + 1)
    assert.equal(await menu(page, 107).count(), 1) // never optimistically remove an unconfirmed card
    await page.getByRole('button', { name: '重试确认原操作', exact: true }).click()
    await page.getByText('已在推荐中隐藏《作品107》。', { exact: true }).waitFor()
    const retries = writes.filter(w => w.body.bangumi_id === 107)
    assert.equal(retries.length, 2); assert.deepEqual(retries[0].body, retries[1].body)
    assert.equal(version, beforeLostAck + 1)
    check('8C server committed but response lost: exact UUID/body replay, one state transition, no false early success')

    await page.getByRole('button', { name: '查看作品108详情', exact: true }).click()
    await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
    dropBeforeCommit = true
    await hide(page, 108, page.locator('dialog[open]'))
    await page.locator('dialog[open]').getByText('反馈结果尚未确认，请重试原操作；不会重复写入。', { exact: true }).waitFor()
    assert.equal(await page.locator('#recResourceDrawer').count(), 1)
    const input = page.locator('#recResourceDrawer-keyword')
    await input.fill('offline'); await input.press('Enter')
    await page.getByText('资源搜索暂时不可用，请稍后再试或使用网页搜索。', { exact: true }).waitFor()
    await input.fill('恢复关键词'); await input.press('Enter')
    await page.getByText('未找到相关资源，请尝试缩短或更换搜索关键词', { exact: true }).waitFor()
    dropBeforeCommit = false
    // Close the detail voluntarily; the original request remains retryable on the page.
    await page.getByRole('button', { name: '关闭面板', exact: true }).click()
    await page.getByRole('button', { name: '重试确认原操作', exact: true }).click()
    await page.getByText('已在推荐中隐藏《作品108》。', { exact: true }).waitFor()
    check('8C independent offline feedback/resource recovery preserves detail context and replays only original feedback')

    await page.getByRole('button', { name: '查看作品109详情', exact: true }).click()
    version++ // another synthetic tab's decision, deliberately no notification
    const beforeConflict = writes.length
    await hide(page, 109, page.locator('dialog[open]'))
    await page.locator('dialog[open]').getByRole('button', { name: '重新读取反馈状态', exact: true }).waitFor()
    assert.equal(writes.length, beforeConflict + 1)
    assert.equal(await page.locator('dialog[open]').count(), 1)
    await page.locator('dialog[open]').getByRole('button', { name: '重新读取反馈状态', exact: true }).click()
    await waitFor(async () => await page.locator('dialog[open]').count() === 0)
    await menu(page, 109).waitFor()
    await hide(page, 109)
    await page.getByText('已在推荐中隐藏《作品109》。', { exact: true }).waitFor()
    const decisions = writes.filter(w => w.body.bangumi_id === 109)
    assert.notEqual(decisions[0].body.operation_id, decisions[1].body.operation_id)
    check('8C stale 409 never auto-writes or closes detail; explicit reload precedes a new decision')

    holdCategory = 'familiar'
    await page.getByRole('button', { name: '熟悉的心动', exact: true }).click()
    await waitFor(() => heldList !== null)
    await page.getByRole('button', { name: '换一种口味', exact: true }).click()
    await menu(page, 301).waitFor()
    holdCategory = null; heldList(); heldList = null
    assert.equal(await menu(page, 201).count(), 0)
    await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
    await menu(page, 313).waitFor()
    check('8C slow old-category list cannot replace a newer category or its next page')

    holdWrite = true
    await hide(page, 301); await waitFor(() => held !== null)
    await page.evaluate(() => feedbackQA.navigate('/dashboard'))
    await page.getByRole('heading', { name: '8C 隔离手账返回页', exact: true }).waitFor()
    held(); held = null
    assert.equal(await page.locator('dialog[open], .rec-feedback-toast').count(), 0)
    await page.evaluate(() => feedbackQA.navigate('/recommendations'))
    await menu(page, 110).waitFor()
    check('8C navigating away cancels owner session; late feedback cannot reopen old toast or dialog')

    await page.setViewportSize({ width: 390, height: 844 })
    const touch = await menu(page, 110).boundingBox()
    assert.ok(touch.width >= 44 && touch.height >= 44)
    await page.getByRole('button', { name: '已隐藏作品', exact: true }).click()
    await page.getByRole('button', { name: '下一页', exact: true }).waitFor()
    const scrolling = await page.locator('dialog[open] > .rec-dialog-body').evaluate(node => {
      node.scrollTop = node.scrollHeight; return { inner: node.scrollHeight > node.clientHeight, offset: node.scrollTop }
    })
    assert.ok(scrolling.inner && scrolling.offset > 0)
    const buttons = page.locator('dialog[open] button:enabled')
    await buttons.last().focus(); await page.keyboard.press('Tab')
    assert.ok(await buttons.first().evaluate(node => node === document.activeElement))
    await page.keyboard.press('Shift+Tab')
    assert.ok(await buttons.last().evaluate(node => node === document.activeElement))
    holdHiddenPage = 2
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await waitFor(() => heldHidden !== null)
    await page.getByRole('button', { name: '关闭面板', exact: true }).click()
    await page.getByRole('button', { name: '已隐藏作品', exact: true }).click()
    await page.getByText(/^第 1 页 ·/).waitFor()
    holdHiddenPage = null; heldHidden(); heldHidden = null
    assert.equal(await page.getByText(/^第 2 页 ·/).count(), 0)
    check('8C narrow-screen touch/inner scrolling/native focus trap and late hidden-page isolation')
  }
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  assert.ok(calls.every(call => call.path.startsWith('/__8bqa__/')))
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.failure = error.message; throw error }
finally { await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); await server.close() }
