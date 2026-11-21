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
let job = null, rows = [], selected = [], failNextStep = false, serial = 0, backupReads = 0, delayRead = false, historyExtras = 0, throttleRead = false
const snapshot = () => ({ ...job, counts: Object.fromEntries(['proposed', 'unchanged', 'applied', 'undone', 'conflict', 'undo_conflict'].map(status => [status, rows.filter(row => row.status === status).length])), change_records: rows.filter(row => row.changes.length).length, needs_review: rows.filter(row => row.uncertain.length).length, protected_records: rows.filter(row => row.protected.length).length })
const initialize = () => {
  job = { id: `00000000-0000-0000-0000-${String(++serial).padStart(12, '0')}`, status: 'scanning', scope: 'filtered', filters: { search: '合成', status: 'plan' }, total: 26, scan_cursor: 0, apply_cursor: 0, undo_cursor: 0, selected_count: 0, created_at: new Date().toISOString(), expires_at: new Date(Date.now()+86400000).toISOString(), expired: false }
  rows = Array.from({ length: 26 }, (_, i) => ({ record_id: i+1, title: `合成作品 ${i+1}`, before: ['2020', '治愈系', '用途不明'], after: ['治愈', '用途不明'], changes: [{ kind: 'normalize', from: '治愈系', to: '治愈' }, { kind: 'remove', from: '2020', reason: '年份信息' }], protected: ['个人标记'], uncertain: ['用途不明'], source_note: '本机合成标准资料', status: 'proposed', message: '' }))
}
await context.addInitScript(() => localStorage.setItem('anime_journal_access_token', 'synthetic-tag-cleanup'))
await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url())
  if (!url.pathname.startsWith('/api/')) return url.origin === base || ['cdn.tailwindcss.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com'].includes(url.hostname) ? route.continue() : route.abort()
  const path = url.pathname.slice(5), method = request.method(), body = request.postDataJSON()
  const respond = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
  if (method !== 'GET') writes.push({ path, body })
  if (path === 'export/') { backupReads++; await new Promise(resolve => setTimeout(resolve, 200)); return respond({ records: [] }) }
  if (!path.startsWith('tag-cleanup/')) {
    assert.equal(method, 'GET', 'Only synthetic cleanup writes are allowed')
    if (path === 'user/profile/' || path === 'user/me/') return respond({ username: 'QA', nickname: '合成手账', public_status: 'private', is_staff: false })
    if (path === 'records/') {
      const record = { id: 1, watch_status: 'plan', custom_tags: [], watched_eps: 0, total_eps: 12, anime: { id: 1, chinese_title: '合成作品', airing_period: '2020-4', studio: '测试', episodes: '12', base_tags: ['日常'] } }
      return respond(url.searchParams.get('page_size') === 'all' ? [record] : { count: 1, results: [record] })
    }
    if (path.endsWith('filter-options/')) return respond({ tags: ['日常'], years: ['2020'] })
    if (path.endsWith('stats/')) return respond({ total_count: 1, watching_count: 0, pending_count: 1 })
    if (path === 'featured-submissions/state/') return respond({ submissions: [], max_featured_limit: 3 })
    return respond([])
  }
  if (path === 'tag-cleanup/') {
    if (method === 'GET') return respond(job ? [snapshot(), ...Array.from({ length: historyExtras }, (_, i) => ({ ...snapshot(), id: `history-${i}`, status: 'applied', scope: 'all' }))] : [])
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
  if (throttleRead) { throttleRead = false; return respond({ detail: 'Synthetic rate limit' }, 429) }
  if (delayRead) await new Promise(resolve => setTimeout(resolve, 250))
  const filter = url.searchParams.get('filter'), filtered = rows.filter(row => filter === 'changes' ? row.changes.length : filter === 'review' ? row.uncertain.length : filter === 'protected' ? row.protected.length : filter === 'conflicts' ? ['conflict', 'undo_conflict'].includes(row.status) : true)
  const offset = (Number(url.searchParams.get('page') || 1)-1)*20
  return respond({ ...snapshot(), item_count: filtered.length, items: filtered.slice(offset, offset+20) })
})
page.on('pageerror', error => errors.push(error.message))
const button = name => page.locator('.tag-cleanup-modal').getByRole('button', { name, exact: true })
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
const settled = async () => {
  await page.waitForFunction(() => document.querySelector('.tag-cleanup-previews')?.getAttribute('aria-busy') === 'false')
  await page.waitForTimeout(250) // Wait for the finite 180/200ms entrance/disclosure animations.
}
await mkdir('../.tools/tag-cleanup-qa', { recursive: true })
try {
  await page.goto(`${base}/tests/fixtures/tagCleanupHarness.html`)
  await open()
  assert.equal(writes.length, 0, 'Opening must not start writes')
  const backup = page.waitForEvent('download'); await button('下载 JSON 备份').evaluate(el => { el.click(); el.click() }); await backup
  assert.equal(backupReads, 1, 'Repeated download clicks share one pending request')
  await button('扫描并生成预览').click(); await button('应用选中的 26 部').waitFor()
  await page.getByRole('navigation', { name: '标签整理预览类别' }).getByRole('button', { name: /^有变化/ }).waitFor()
  assert.equal(await page.locator('.tag-cleanup-tabs button[aria-pressed=true]').textContent(), '有变化26')
  assert.equal(await page.locator('.tag-cleanup-tabs').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(238, 230, 221)', 'Global journal navigation must not paint the preview tabs coral')
  assert.equal(await page.locator('.tag-cleanup-tabs button').first().evaluate(el => getComputedStyle(el).borderRadius), '0px')
  assert.equal(await page.locator('.memphis-record-pagination').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 107, 107)', 'The actual journal pagination retains its appearance')
  await button('取消本页').click(); await button('应用选中的 6 部').waitFor()
  await button('下一页').click(); await page.getByText('合成作品 26', { exact: true }).waitFor()
  await button('取消本页').click(); assert.equal(await button('应用选中的 0 部').isDisabled(), true)
  await button('全选本页').click(); await button('上一页').click(); await button('全选本页').click()
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
  await button('确认撤销本次整理').click(); await button('完成并关闭').waitFor()
  await page.getByText('应用后标签已被编辑，已保留新修改。', { exact: true }).waitFor()
  assert.equal(rows.filter(row => row.status === 'undone').length, 24)
  assert.equal(rows[1].status, 'undo_conflict')
  assert.equal(await page.locator('.tag-cleanup-tabs button[aria-pressed=true]').textContent(), '冲突1')
  assert.equal(await page.locator('.tag-cleanup-card').count(), 1)
  // Failed batch does not trigger automatic write replay.
  failNextStep = true
  await button('新建扫描').click()
  await button('扫描并生成预览').click()
  await page.getByRole('alert').filter({ hasText: '合成批次中断' }).waitFor()
  const failedWrites = writes.length
  await page.waitForTimeout(900); assert.equal(writes.length, failedWrites)
  await button('重新读取任务').click(); await button('继续扫描').click(); await button('应用选中的 26 部').waitFor()
  delayRead = true
  const tabs = page.getByRole('navigation', { name: '标签整理预览类别' })
  await tabs.getByRole('button', { name: /^未知保留/ }).click()
  await page.getByText('正在读取预览…', { exact: true }).waitFor()
  assert.equal(await page.getByRole('checkbox').count(), 0, 'Old preview cannot be selected during a new read')
  await button('应用选中的 26 部').waitFor({ state: 'visible' })
  await page.waitForFunction(() => document.querySelector('.tag-cleanup-previews')?.getAttribute('aria-busy') === 'false')
  assert.equal(await tabs.getByRole('button', { name: /^未知保留/ }).getAttribute('aria-pressed'), 'true')
  delayRead = false
  await tabs.getByRole('button', { name: /^有变化/ }).click()
  await page.locator('.tag-cleanup-card').first().getByRole('button', { name: '查看完整对比与保护说明', exact: true }).click()
  await page.getByText('整理后方案', { exact: true }).first().waitFor()
  assert.equal(await page.locator('.tag-cleanup-card').first().locator('.is-remove').count(), 2)
  await page.locator('.tag-cleanup-card').first().getByRole('button', { name: '收起对比与保护说明', exact: true }).click()
  throttleRead = true
  await button('下一页').click()
  await page.getByRole('alert').filter({ hasText: '操作较频繁' }).waitFor()
  await page.waitForTimeout(700)
  assert.equal(await page.getByRole('alert').filter({ hasText: '操作较频繁' }).isVisible(), true, '429 is not automatically replayed')
  await button('重新读取任务').click(); await settled()
  historyExtras = 4
  rows[19].title = `《这是包含中日文与超长副标题的合成作品 LongTitle》${'很长的副标题'.repeat(10)}`
  for (let i = 0; i < 36; i++) { rows[19].after.push(`合成题材${i}`); rows[19].changes.push({ kind: 'add', to: `合成题材${i}`, reason: '合成标准资料' }) }
  rows.push(...Array.from({ length: 4 }, (_, i) => ({ record_id: 27+i, title: `无需修改的作品 ${i}`, before: ['日常', '音乐'], after: ['日常', '音乐'], changes: [], protected: ['日常'], uncertain: [], source_note: '合成资料', status: 'unchanged', message: '' })))
  job.total = 30; job.scan_cursor = 30
  await button('关闭').click(); await open()
  await settled()
  await page.locator('.tag-cleanup-tabs').getByRole('button', { name: /^全部/ }).click()
  await button('下一页').click(); await settled()
  assert.equal(await page.locator('.tag-cleanup-card.is-compact').count(), 4)
  await page.locator('.tag-cleanup-tabs').getByRole('button', { name: /^有变化/ }).click(); await settled()
  const longCard = page.locator('.tag-cleanup-card').last()
  await longCard.getByRole('button', { name: /另 32 项变化/ }).click()
  await longCard.getByText('整理后方案', { exact: true }).waitFor()
  assert.equal(await longCard.evaluate(el => el.scrollWidth <= el.clientWidth), true, 'Long title and many tags must wrap inside the card')
  await longCard.getByRole('button', { name: '收起对比与保护说明', exact: true }).click()
  await page.locator('.tag-cleanup-previews').evaluate(el => { el.scrollTop = 0 })
  await page.screenshot({ path: '../.tools/tag-cleanup-qa/desktop.png' })
  for (const [width, height] of [[390, 640], [320, 480], [844, 390]]) {
    await page.setViewportSize({ width, height })
    if (width < 768) {
      await button(/^最近任务/).click()
      assert.equal(await page.locator('.tag-cleanup-history-list button').count(), 5)
      await hit(page.locator('.tag-cleanup-history-list button').last())
      await button(/^最近任务/).click()
      await page.locator('.tag-cleanup-main').evaluate(el => { el.scrollTop = 0 })
      await page.screenshot({ path: `../.tools/tag-cleanup-qa/mobile-top-${width}.png` })
      await button('新建扫描').click()
      await hit(page.getByRole('combobox', { name: '标签整理范围' }))
      assert.ok((await page.locator('.tag-cleanup-task-scope p').textContent()).includes('30 部'))
      assert.ok((await page.getByRole('combobox', { name: '标签整理范围' }).textContent()).includes('26 部'), 'New scope must not overwrite historical task membership')
      await button('收起扫描设置').click()
    }
    await hit(button('应用选中的 26 部')); await hit(button('关闭'))
    assert.equal(await page.locator('dialog').evaluate(el => el.scrollWidth <= innerWidth), true)
    await hit(page.getByRole('checkbox', { name: '整理 合成作品 1', exact: true }))
    await page.screenshot({ path: `../.tools/tag-cleanup-qa/mobile-${width}.png` })
  }
  await page.emulateMedia({ reducedMotion: 'reduce' })
  assert.equal(await page.locator('.tag-cleanup-modal').evaluate(el => getComputedStyle(el).animationName), 'none')
  assert.equal(await page.locator('.tag-cleanup-primary').first().evaluate(el => getComputedStyle(el).transitionDuration), '0s')
  // Switching subject closes private preview; no further writes are sent.
  await page.evaluate(() => { localStorage.setItem('anime_journal_access_token', 'synthetic-other'); window.dispatchEvent(new Event('storage')) })
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  await page.setViewportSize({ width: 1440, height: 950 })
  await page.getByRole('button', { name: '👁 预览模式', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: '📦 数据管理' }).count(), 0)
  // Check the actual Dashboard route too, with every API call intercepted above.
  await page.goto(`${base}/dashboard`)
  if (await page.getByRole('button', { name: '📝 编辑模式', exact: true }).count()) await page.getByRole('button', { name: '📝 编辑模式', exact: true }).click()
  await open()
  await button('应用选中的 26 部').waitFor()
  assert.equal(await page.locator('.tag-cleanup-tabs').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(238, 230, 221)')
  await page.screenshot({ path: '../.tools/tag-cleanup-qa/dashboard-desktop.png' })
  await page.setViewportSize({ width: 390, height: 640 })
  await hit(button('应用选中的 26 部')); await hit(button('关闭'))
  await page.locator('.tag-cleanup-main').evaluate(el => { el.scrollTop = 0 })
  await page.screenshot({ path: '../.tools/tag-cleanup-qa/dashboard-mobile.png' })
  await page.keyboard.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'detached' })
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('journal-dropdown-trigger')), true, 'Focus returns to the journal menu')
  // A no-change scan offers no mutation and has a useful empty category state.
  rows.forEach(row => { row.before = [...row.after]; row.changes = []; row.status = 'unchanged' })
  await page.setViewportSize({ width: 1440, height: 950 }); await open(); await settled()
  assert.equal(await button('应用选中的 0 部').isDisabled(), true)
  assert.equal(await page.locator('.tag-cleanup-tabs button[aria-pressed=true]').textContent(), '全部30')
  await page.locator('.tag-cleanup-tabs').getByRole('button', { name: /^有变化/ }).click()
  await page.getByText('此分类暂无记录', { exact: true }).waitFor()
  await page.keyboard.press('Escape'); await page.getByRole('dialog').waitFor({ state: 'detached' })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', checks: ['edit-only entry', 'deduplicated backup and change downloads', 'default change/conflict category and overlapping counts', 'page selection across categories', 'partial apply reload/resume', 'confirmed undo and conflict', 'failed write and 429 no replay', 'loading blocks stale selection', 'long title and many tags', 'five histories', 'desktop/mobile/landscape hit targets', 'reduced motion', 'account switch isolation', 'actual Dashboard desktop/mobile CSS and focus return', 'zero-change and empty category'], apiWrites: writes.length }))
} catch (error) {
  await page.screenshot({ path: '../.tools/tag-cleanup-qa/failure.png' }).catch(() => {})
  throw error
} finally { await browser.close() }
