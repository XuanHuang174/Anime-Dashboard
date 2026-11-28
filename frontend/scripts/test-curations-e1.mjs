import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/curations-e1-fixture.json', 'utf8')), base = 'http://127.0.0.1:5191', out = '../.tools/curations-e1-qa'
assert.match(f.username, /^curation-e1-/); await mkdir(out, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
await context.addInitScript(({ token, user_id }) => { localStorage.setItem('anime_journal_access_token', token); localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'edit') }, f)
const page = await context.newPage(), errors = []
page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(20000)
const api = async (path, data) => {
  const response = await context.request[data ? 'post' : 'get'](base + '/api/' + path, { headers: { Authorization: `Bearer ${f.token}` }, ...(data ? { data } : {}) })
  assert.equal(response.status(), 200, await response.text()); return response.json()
}
try {
  const backup = await api('export/'), originalCount = (await api('curations/')).count
  backup.curations.lists.pop()
  backup.curations.lists.push({ id: crypto.randomUUID(), title: '文件内新增主题', description: '', pinned: false, archived: false, cover_entry_id: null, entries: [] })
  await page.goto(base + '/dashboard')
  await page.getByRole('button', { name: /数据管理/ }).click(); await page.getByRole('button', { name: '📥 导入模板 (JSON)', exact: true }).click()
  const upload = async data => page.locator('input[type=file][accept=".json,application/json"]').setInputFiles({ name: 'synthetic-e1.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) })
  await upload(backup)
  const impact = page.getByLabel('私人片单恢复影响', { exact: true })
  await impact.waitFor(); assert.match(await impact.innerText(), /新增 1 张，替换 1 张，移除 1 张/)
  assert.match(await impact.innerText(), /1 项需重新关联/)
  assert.equal((await api('curations/')).count, originalCount) // preview is read-only
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 1000 }); await impact.scrollIntoViewIfNeeded()
    assert.equal(await impact.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    await page.screenshot({ path: `${out}/restore-${width}.png` })
  }
  // Changing a reason after preview must invalidate restoration in the actual UI.
  const state = await api('curations/')
  await api('curations/', { action: 'update-entry', version: state.version, operation_id: crypto.randomUUID(), list_id: f.list_id, entry_id: f.entry_id, reason: '预览后保留的草稿理由' })
  await page.getByRole('checkbox', { name: /我已核对影响/ }).check()
  await page.getByRole('button', { name: /确认覆盖/ }).click()
  await page.getByLabel('确认 JSON 恢复影响', { exact: true }).getByRole('alert').filter({ hasText: /已变化|过期/ }).waitFor()
  assert.equal((await api(`curations/${f.list_id}/`)).results[0].reason, '预览后保留的草稿理由')
  assert.equal((await api('curations/')).count, originalCount)
  const publicPage = await context.newPage(), reads = []
  publicPage.on('request', r => { if (r.url().includes('/api/curations/')) reads.push(r.url()) })
  await publicPage.goto(base + '/shared/' + f.username); await publicPage.locator('tbody tr').first().waitFor()
  assert.equal((await publicPage.locator('body').innerText()).includes('私人入选理由'), false)
  assert.deepEqual(reads, []); await publicPage.close()
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', checks: ['real restore counts', 'placeholder explanation', 'preview read-only', '320/390/1440 layouts', 'reason edit invalidates restore', 'public privacy'], browserErrors: errors }))
} catch (error) { await page.screenshot({ path: out + '/failure.png' }); console.log((await page.locator('body').innerText()).slice(-2000)); throw error }
finally { await context.close(); await browser.close() }
