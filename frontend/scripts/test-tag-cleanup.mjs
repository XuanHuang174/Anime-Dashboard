// Local synthetic API only. No credentials, production services or database.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5189'
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 950 } })
const page = await context.newPage()
page.setDefaultTimeout(10000)
const errors = [], writes = []
let job = null, rows = [], selected = [], failNextStep = false, serial = 0
const snapshot = () => ({ ...job, counts: Object.fromEntries(['proposed', 'applied', 'undone', 'undo_conflict'].map(status => [status, rows.filter(row => row.status === status).length])), needs_review: 26, protected_records: 26 })
const initialize = () => {
  job = { id: `00000000-0000-0000-0000-${String(++serial).padStart(12, '0')}`, status: 'scanning', scope: 'filtered', filters: {}, total: 26, scan_cursor: 0, apply_cursor: 0, undo_cursor: 0, selected_count: 0, created_at: new Date().toISOString(), expires_at: new Date(Date.now()+86400000).toISOString(), expired: false }
  rows = Array.from({ length: 26 }, (_, i) => ({ record_id: i+1, title: `合成作品 ${i+1}`, before: ['2020', '治愈系', '用途不明'], after: ['治愈', '用途不明'], changes: [{ kind: 'normalize', from: '治愈系', to: '治愈' }, { kind: 'remove', from: '2020', reason: '年份信息' }], protected: ['个人标记'], uncertain: ['用途不明'], source_note: '本机合成标准资料', status: 'proposed', message: '' }))
}
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token', 'synthetic-tag-cleanup'))
await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url())
  if (!url.pathname.startsWith('/api/')) return url.origin === base ? route.continue() : route.abort()
  const path = url.pathname.slice(5), method = request.method(), body = request.postDataJSON()
  const respond = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
  if (method !== 'GET') writes.push({ path, body })
  if (path === 'export/') return respond({ records: [] })
  if (!path.startsWith('tag-cleanup/')) throw new Error(`Unexpected API request: ${method} ${path}`)
  if (path === 'tag-cleanup/') {
    if (method === 'GET') return respond(job ? [snapshot()] : [])
    assert.equal(body.scope, 'filtered'); assert.equal(body.filters.search, '合成')
    initialize(); return respond(snapshot(), 201)
  }
  if (path.endsWith('/step/')) {
    if (failNextStep) { failNextStep = false; return respond({ detail: '合成批次中断，请重新读取任务。' }, 500) }
    const phase = body.phase
    if (phase === 'scan') { job.scan_cursor = 26; job.status = 'ready' }
    if (phase === 'apply') {
      const batch = selected.slice(job.apply_cursor, job.apply_cursor + 10)
      rows.filter(row => batch.includes(row.record_id)).forEach(row => { row.status = 'applied' })
      job.apply_cursor += batch.length; if (job.apply_cursor === selected.length) job.status = 'applied'
    }
    if (phase === 'undo') {
      rows.filter(row => row.status === 'applied').forEach(row => { row.status = row.record_id === 2 ? 'undo_conflict' : 'undone'; if (row.record_id === 2) row.message = '应用后标签已被编辑，已保留新修改。' })
      job.undo_cursor = selected.length; job.status = 'undone'
    }
    return respond(snapshot())
  }
  if (path.endsWith('/apply/')) {
    assert.deepEqual(Object.keys(body), ['excluded_ids'])
    selected = rows.filter(row => !body.excluded_ids.includes(row.record_id)).map(row => row.record_id)
    job.selected_count = selected.length; job.status = 'applying'; return respond(snapshot())
  }
  if (path.endsWith('/undo/')) { job.status = 'undoing'; return respond(snapshot()) }
  if (path.endsWith('/changes/')) return respond({ job: snapshot(), items: rows })
  const offset = (Number(url.searchParams.get('page') || 1)-1)*20
  return respond({ ...snapshot(), item_count: rows.length, items: rows.slice(offset, offset+20) })
})
page.on('pageerror', error => errors.push(error.message))
const button = name => page.getByRole('button', { name, exact: true })
const open = async (mobile = false) => {
  if (mobile) await page.getByRole('button', { name: '☰ 菜单' }).click()
  await page.getByRole('button', { name: '📦 数据管理' }).click()
  await page.getByRole('button', { name: /🧹 整理作品标签/ }).click()
  await page.getByRole('dialog').waitFor()
}
const hit = async locator => {
  await locator.scrollIntoViewIfNeeded()
  assert.ok(await locator.evaluate(el => {
    const r = el.getBoundingClientRect(), x = r.left+r.width/2, y = r.top+r.height/2
    const hit = document.elementFromPoint(x, y)
    return x >= 0 && y >= 0 && x < innerWidth && y < innerHeight && (hit === el || el.contains(hit))
  }), 'Control must be visible and clickable, without footer overlap')
}
try {
  await page.goto(`${base}/tests/fixtures/tagCleanupHarness.html`)
  await open()
  assert.equal(writes.length, 0, 'Opening must not start writes')
  const backup = page.waitForEvent('download'); await button('下载 JSON 备份').click(); await backup
  await button('扫描并生成预览').click(); await button('应用选中的 26 部').waitFor()
  await page.getByRole('checkbox', { name: '整理 合成作品 1', exact: true }).uncheck()
  await button('下一页').click(); await page.getByText('合成作品 26', { exact: true }).waitFor()
  await button('上一页').click(); assert.equal(await page.getByRole('checkbox', { name: '整理 合成作品 1', exact: true }).isChecked(), false)
  const list = page.waitForEvent('download'); await button('下载变更清单').click(); await list
  // Leave the server task partially applied, reload, and resume from its stored cursor.
  await button('应用选中的 25 部').click()
  await page.waitForFunction(() => document.querySelector('.tag-cleanup-progress')?.textContent.includes('已应用 10 部'))
  await button('完成当前批次后暂停').click(); await button('继续应用').waitFor()
  assert.equal(job.apply_cursor, 10)
  await page.reload(); await open()
  assert.equal(job.apply_cursor, 10)
  await button('继续应用').click(); await button('撤销本次整理').waitFor()
  assert.equal(job.apply_cursor, 25); assert.equal(rows[0].status, 'proposed')
  const undoBefore = writes.length
  await button('撤销本次整理').click(); assert.equal(writes.length, undoBefore, 'Undo requires explicit second confirmation')
  await button('确认撤销本次整理').click(); await button('关闭').waitFor()
  await page.getByText('应用后标签已被编辑，已保留新修改。', { exact: true }).waitFor()
  assert.equal(rows.filter(row => row.status === 'undone').length, 24)
  assert.equal(rows[1].status, 'undo_conflict')
  // Failed batch does not trigger automatic write replay.
  failNextStep = true
  await button('扫描并生成预览').click()
  await page.getByRole('alert').filter({ hasText: '合成批次中断' }).waitFor()
  const failedWrites = writes.length
  await page.waitForTimeout(900); assert.equal(writes.length, failedWrites)
  await button('重新读取任务').click(); await button('继续扫描').click(); await button('应用选中的 26 部').waitFor()
  await mkdir('../.tools/tag-cleanup-qa', { recursive: true })
  await page.screenshot({ path: '../.tools/tag-cleanup-qa/desktop.png' })
  for (const [width, height] of [[390, 640], [320, 480]]) {
    await page.setViewportSize({ width, height })
    await hit(button('应用选中的 26 部')); await hit(button('关闭'))
    assert.equal(await page.locator('dialog').evaluate(el => el.scrollWidth <= innerWidth), true)
    await hit(page.getByRole('checkbox', { name: '整理 合成作品 1', exact: true }))
    await page.screenshot({ path: `../.tools/tag-cleanup-qa/mobile-${width}.png` })
  }
  // Switching subject closes private preview; no further writes are sent.
  await page.evaluate(() => { localStorage.setItem('anime_journal_access_token', 'synthetic-other'); window.dispatchEvent(new Event('storage')) })
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  await page.setViewportSize({ width: 1440, height: 950 })
  await page.getByRole('button', { name: '👁 预览模式', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: '📦 数据管理' }).count(), 0)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', checks: ['edit-only entry', 'backup and change downloads', 'scan without record writes', 'exclusions across pages', 'partial apply reload/resume', 'confirmed undo and conflict', 'failed write no replay', 'desktop/mobile hit targets', 'account switch isolation'], apiWrites: writes.length }))
} finally { await browser.close() }
