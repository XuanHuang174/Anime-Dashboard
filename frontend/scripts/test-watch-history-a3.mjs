// Real local Django + real Dashboard. Seed with scripts/seed-watch-history-e2e.py.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.JOURNAL_TEST_URL || 'http://127.0.0.1:5191'
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/)
const fixture = JSON.parse(await readFile('../.tools/watch-history-a3-fixture.json', 'utf8'))
assert.match(fixture.username, /^watch-a3-/)
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 950 }, reducedMotion: 'reduce' })
await context.addInitScript(({ token, user_id }) => {
  localStorage.setItem('anime_journal_access_token', token)
  localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'edit')
}, fixture)
const page = await context.newPage(); page.setDefaultTimeout(15000)
const errors = [], writes = []
page.on('pageerror', error => errors.push(error.message))
page.on('request', req => { if (req.method() === 'POST' && req.url().includes('/watch-history/')) writes.push(JSON.parse(req.postData())) })
const api = async (path, body) => {
  const response = await context.request[body ? 'post' : 'get'](`${base}/api/${path}`, { headers: { Authorization: `Bearer ${fixture.token}` }, ...(body ? { data: body } : {}) })
  assert.equal(response.status(), 200, await response.text())
  return response.json()
}
let recordId = fixture.record_id
const history = () => api(`records/${recordId}/watch-history/`)
const command = async (action, extra = {}) => api(`records/${recordId}/watch-history/`, { action, version: (await history()).version, operation_id: crypto.randomUUID(), ...extra })
const panel = () => page.getByRole('region', { name: '私人观看历程', exact: true })
const button = name => panel().getByRole('button', { name, exact: true })
const field = name => panel().getByLabel(name, { exact: true })
const close = () => page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click()
const save = async () => { await button('保存这条记录').click(); await panel().locator('.watch-form').waitFor({ state: 'detached' }); await button('重新读取历程').waitFor({ state: 'visible' }); await page.waitForFunction(() => !document.querySelector('.watch-workshop .watch-status')?.textContent.includes('读取历程…')) }
const hit = async locator => { await locator.scrollIntoViewIfNeeded(); assert.equal(await locator.evaluate(el => { const r = el.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2, target = document.elementFromPoint(x, y); return x >= 0 && x < innerWidth && y >= 0 && y < innerHeight && (target === el || el.contains(target)) }), true) }
const upload = async payload => page.locator('input[type="file"][accept=".json,application/json"]').setInputFiles({ name: 'synthetic-watch-a3.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(payload)) })
const importDialog = () => page.getByRole('dialog', { name: '确认 JSON 恢复影响' })
await mkdir('../.tools/watch-history-a3-qa', { recursive: true })
try {
  await page.goto(`${base}/dashboard`)
  await page.getByRole('button', { name: '修改', exact: true }).first().click()
  // Open the fixture's main work explicitly; list date ordering may vary.
  if (await page.locator('input[name="title"]').inputValue() !== 'A3 合成观看历程') {
    await close(); await page.getByRole('button', { name: '修改', exact: true }).last().click()
  }
  assert.equal(await page.locator('input[name="title"]').inputValue(), 'A3 合成观看历程')
  const entry = page.getByRole('button', { name: '观看历程', exact: true })
  const del = page.getByRole('button', { name: /删除私人记录/ })
  const a = await del.boundingBox(), b = await entry.boundingBox()
  assert.equal(b.x > a.x && b.y === a.y, true)
  assert.equal(await entry.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)')
  await page.screenshot({ path: '../.tools/watch-history-a3-qa/editor-desktop.png' })
  for (const [width, height] of [[320, 568], [844, 390]]) {
    await page.setViewportSize({ width, height }); await hit(entry); await hit(page.getByRole('button', { name: /保存全部修改/ }))
    assert.equal(await page.getByRole('dialog').evaluate(el => el.scrollWidth <= el.clientWidth), true)
  }
  await page.setViewportSize({ width: 1440, height: 950 })
  const title = page.locator('input[name="title"]'); await title.fill('未保存'); assert.equal(await entry.isDisabled(), true); await title.fill('A3 合成观看历程')
  await page.getByRole('button', { name: '个人评价', exact: true }).click(); await entry.click()
  await page.getByRole('dialog', { name: '观看历程管理' }).waitFor()
  await button('记录进度').waitFor()
  assert.equal(await panel().locator('.watch-toggle').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)')
  await button('写随记').click(); await field('随记内容').fill('草稿留在管理区')
  await page.getByRole('button', { name: '返回作品编辑', exact: true }).click(); await button('继续编辑').click()
  assert.equal(await field('随记内容').inputValue(), '草稿留在管理区')
  await close(); await button('继续编辑').click()
  await save()
  await page.getByRole('button', { name: '返回作品编辑', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: '个人评价', exact: true }).getAttribute('aria-pressed'), 'true')
  await entry.click(); await button('补录往事').click(); await field('随记内容').fill('大致月份往事')
  await button('保存这条记录').click(); await panel().getByRole('alert').waitFor()
  await field('回忆发生日期精度').selectOption('month'); await field('回忆发生日期').fill('2010-02'); await save()
  await button('记录进度').click(); await field('本轮已看集数').fill('5'); await save()
  await button('标记完成').click(); await button('保存这条记录').click(); await panel().getByText('请选择是否将进度补齐至总集数。', { exact: true }).waitFor()
  await field('完成进度处理').selectOption('fill'); await save()
  assert.equal((await history()).rounds[0].watched_eps, 12)
  await button('开始重看').click(); await field('重看开始日期精度').selectOption('year'); await field('重看开始日期').fill('2026')
  const before = writes.length; await button('确认开始重看').evaluate(el => { el.click(); el.click() }); await panel().locator('.watch-form').waitFor({ state: 'detached' })
  assert.equal(writes.length, before + 1)
  await button('撤销最近一次操作').click(); await button('确认撤销最近操作').click(); await panel().locator('.watch-form').waitFor({ state: 'detached' })
  await panel().locator('.watch-timeline').getByText('开始重看 · 已撤销', { exact: true }).waitFor()
  await button('更正第 1 轮').click(); await field('轮次评分').fill('8.5'); await save()
  assert.equal((await api(`records/${recordId}/`)).personal_score, '9.1')
  for (let i = 0; i < 8; i++) await command('note', { note: `真实分页随记 ${i}` })
  await button('重新读取历程').click(); await button('下一页历程').first().click()
  await page.waitForFunction(() => document.querySelector('.watch-pagination')?.textContent.includes('2 /'))
  assert.equal(await panel().locator('.watch-timeline > li').count(), 5)
  await field('时间线类型').selectOption('notes'); await field('每页历程数量').selectOption('10')
  await page.waitForFunction(() => document.querySelectorAll('.watch-timeline > li').length === 10)
  assert.equal(await panel().locator('.watch-timeline > li').count(), 10)
  for (const [width, height] of [[320, 568], [390, 740], [844, 390], [1440, 950]]) {
    await page.setViewportSize({ width, height }); await hit(field('时间线类型')); await hit(field('每页历程数量'))
    assert.equal(await panel().evaluate(el => el.scrollWidth <= el.clientWidth), true)
    await page.screenshot({ path: `../.tools/watch-history-a3-qa/timeline-${width}.png` })
  }
  await field('时间线类型').selectOption('all')
  await panel().locator('.watch-maintenance > summary').click(); await button('清空本作品观看历程').click()
  assert.equal(await button('清空观看历程').isDisabled(), true)
  await panel().getByRole('checkbox').check()
  const oldBackup = await api('export/'), beforeClear = await history()
  // Simulate a committed clear whose acknowledgement was lost, using the real backend.
  let lost = false
  await page.route('**/watch-history/', async route => {
    if (!lost && route.request().method() === 'POST' && route.request().postDataJSON().action === 'clear') {
      lost = true; const response = await route.fetch(); assert.equal(response.status(), 200)
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'synthetic lost acknowledgement' }) })
    }
    return route.continue()
  })
  await button('清空观看历程').click(); await button('重试原操作并核对结果').waitFor()
  assert.equal((await history()).total_count, 0)
  await button('重新读取历程').click(); await button('重试原操作并核对结果').click(); await panel().locator('.watch-form').waitFor({ state: 'detached' })
  assert.deepEqual(writes.at(-1), writes.at(-2))
  const afterClear = await history(); assert.equal(afterClear.version, beforeClear.version + 1); assert.equal(afterClear.rewatch_count, 0)
  const savedWork = await api(`records/${recordId}/`); assert.equal(savedWork.personal_score, '9.1'); assert.equal(savedWork.review, '保留综合评分与长评')
  await close(); await page.getByRole('button', { name: /预览模式/ }).click()
  await page.getByRole('button', { name: '详情', exact: true }).last().click(); await panel().locator('.watch-toggle').click()
  assert.equal(await panel().evaluate(el => el.parentElement.classList.contains('memphis-modal-piece')), true)
  await button('前往编辑模式管理').waitFor(); assert.equal(await button('写随记').count(), 0); assert.equal(await button('清空本作品观看历程').count(), 0)
  await button('前往编辑模式管理').click(); await page.getByRole('dialog', { name: '观看历程管理' }).waitFor(); await button('写随记').click(); await field('随记内容').fill('新的历程'); await save(); await close()
  // Restore the full old backup after removing the other work from the file.
  const restoreFile = structuredClone(oldBackup); restoreFile.records = restoreFile.records.filter(row => row.anime.chinese_title === 'A3 合成观看历程')
  await upload(restoreFile); await importDialog().getByText(/文件未包含的 1 部作品/).waitFor()
  for (const [width, height] of [[320, 568], [1440, 950]]) {
    await page.setViewportSize({ width, height }); await hit(importDialog().getByRole('checkbox')); await hit(importDialog().getByRole('button', { name: '取消', exact: true }))
    assert.equal(await importDialog().evaluate(el => el.scrollWidth <= el.clientWidth), true)
    await page.screenshot({ path: `../.tools/watch-history-a3-qa/restore-${width}.png` })
  }
  assert.equal(await importDialog().getByRole('button', { name: /确认覆盖/ }).isDisabled(), true)
  await command('note', { note: '预览后并发变化' })
  await importDialog().getByRole('checkbox').check(); await importDialog().getByRole('button', { name: /确认覆盖/ }).click()
  await importDialog().getByRole('alert').getByText(/手账或文件已变化/).waitFor()
  await importDialog().getByRole('button', { name: '重新预览恢复影响' }).click(); await importDialog().getByRole('checkbox').check()
  await importDialog().getByRole('button', { name: /确认覆盖/ }).click(); await importDialog().waitFor({ state: 'detached' })
  const list = await api('records/?page_size=all'); assert.equal(list.length, 1); recordId = list[0].id
  assert.equal((await history()).version, beforeClear.version)
  assert.equal((await history()).events.some(e => e.note === '草稿留在管理区'), true)
  let importAttempts = 0
  await page.route('**/api/import/?confirm=true', async route => {
    importAttempts++; const response = await route.fetch(); assert.equal(response.status(), 200)
    return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
  })
  await upload(restoreFile); await importDialog().getByRole('checkbox').check()
  await importDialog().getByRole('button', { name: /确认覆盖/ }).click()
  await importDialog().getByRole('alert').getByText(/恢复结果尚未确认/).waitFor()
  assert.equal(await importDialog().getByRole('button', { name: /确认覆盖/ }).isDisabled(), true)
  assert.equal(importAttempts, 1)
  await importDialog().getByRole('button', { name: '取消', exact: true }).click()
  const confirmed = await api('records/?page_size=all'); assert.equal(confirmed.length, 1)
  const hReads = []; page.on('request', req => { if (req.url().includes('/watch-history/')) hReads.push(req.url()) })
  await page.goto(`${base}/shared/${fixture.username}`); await page.getByRole('button', { name: '详情', exact: true }).click()
  assert.equal(await panel().count(), 0); assert.equal(hReads.length, 0)
  await page.getByRole('button', { name: '关闭详情', exact: true }).click()
  await page.emulateMedia({ reducedMotion: 'no-preference' }); await page.goto(`${base}/dashboard`)
  await page.getByRole('button', { name: '修改', exact: true }).click()
  await page.waitForFunction(() => { const modal = document.querySelector('.edit-record-modal'); return modal && !modal.style.transform && getComputedStyle(modal).opacity === '1' })
  await page.getByRole('button', { name: '观看历程', exact: true }).click(); await button('开始重看').waitFor()
  await page.screenshot({ path: '../.tools/watch-history-a3-qa/management-motion.png' })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', backend: 'real isolated Django SQLite', checks: ['left white entry', 'draft/tab preservation', 'read-only preview and explicit edit switch', 'partial dates', 'completion choice', 'rewatch double gate', 'combined undo', 'independent score', 'server filters and paging', 'mobile/landscape', 'clear receipt retry and monotonic barrier', 'restore impact and stale token', 'intentional old history restore', 'public privacy'], writes: writes.length }))
} catch (error) {
  await page.screenshot({ path: '../.tools/watch-history-a3-qa/failure.png' }).catch(() => {})
  console.error(JSON.stringify({ pageErrors: errors, writes: writes.length })); throw error
} finally { await browser.close() }
