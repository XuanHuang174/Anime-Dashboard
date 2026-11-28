// F1 visual polish acceptance against isolated local API and synthetic records.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const fixture = JSON.parse(await readFile('../.tools/curation-polish-fixture.json', 'utf8'))
assert.match(fixture.username, /^curation-e1-/)
const output = '../.tools/import-f1-qa'
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' })
await context.addInitScript(({ token, user_id }) => {
  localStorage.setItem('anime_journal_access_token', token)
  localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'edit')
}, fixture)
const page = await context.newPage(), errors = []
page.setDefaultTimeout(20000)
page.on('pageerror', error => errors.push(error.message))
const nav = name => page.getByRole('navigation', { name: '私人手账工作区' }).getByRole('button', { name, exact: true })
const workspace = () => page.getByRole('region', { name: '私人片单工作区', exact: true })
const ready = async () => {
  await workspace().locator('[aria-busy=false]').waitFor({ state: 'attached' })
  await workspace().getByText('正在读取片单…', { exact: true }).waitFor({ state: 'detached' })
}
try {
  await page.goto('http://127.0.0.1:5191/dashboard')
  await page.locator('#dashboardPaginationTop .journal-batch-tools button').waitFor()
  for (const width of [320, 390, 844, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    const tools = page.locator('#dashboardPaginationTop .journal-list-tools')
    const layout = await tools.evaluate(el => {
      const sort = el.querySelector('.memphis-priority-notice'), batch = el.querySelector('.journal-batch-tools')
      return { width: el.scrollWidth, client: el.clientWidth, sortHeight: sort.getBoundingClientRect().height,
        batchBelow: batch.getBoundingClientRect().top >= el.querySelector('.journal-list-tools-row').getBoundingClientRect().bottom,
        sortIcon: sort.querySelector('i').className, collectIcon: batch.querySelector('i').className }
    })
    assert.ok(layout.width <= layout.client + 1)
    assert.ok(layout.sortHeight < 105)
    assert.equal(layout.batchBelow, true)
    assert.notEqual(layout.sortIcon, layout.collectIcon)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    await tools.screenshot({ path: `${output}/toolbar-${width}.png` })
  }
  await nav('片单').click(); await ready()
  assert.equal(await workspace().locator('h2 .fa-list-check').count(), 1)
  await workspace().getByRole('button', { name: 'E2 跨页主题', exact: true }).click(); await ready()
  await workspace().getByRole('button', { name: '＋ 收录作品', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '从私人手账收录作品', exact: true })
  await picker.getByRole('checkbox').first().waitFor()
  const status = picker.getByRole('button', { name: '观看状态', exact: true })
  for (const width of [320, 390, 844, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await status.click()
    const panel = picker.locator('.searchable-select__panel')
    assert.equal(await panel.getByRole('searchbox').count(), 0)
    assert.equal(await panel.getByRole('option').count(), 5)
    assert.ok((await panel.boundingBox()).height <= 282)
    await page.screenshot({ path: `${output}/status-${width}.png` })
    await page.keyboard.press('Escape')
    assert.equal(await panel.count(), 0); assert.equal(await picker.count(), 1)
    assert.equal(await status.evaluate(el => el === document.activeElement), true)
  }
  await status.focus(); await page.keyboard.press('ArrowDown')
  await picker.getByRole('option', { name: '全部状态', exact: true }).waitFor()
  await page.keyboard.press('End'); await page.keyboard.press('Enter')
  assert.equal(await status.innerText(), '搁置')
  await status.click(); await page.keyboard.press('Home'); await page.keyboard.press('Enter')
  assert.equal(await status.innerText(), '全部状态')
  await picker.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: /预览模式/ }).click(); await ready()
  assert.equal(await workspace().locator('h2 .fa-list-check').count(), 1)
  await nav('手账').click()
  await page.locator('#dashboardPaginationTop .journal-list-tools').waitFor()
  assert.equal(await page.locator('#dashboardPaginationTop .journal-batch-tools').count(), 0)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ ok: true, widths: [320, 390, 844, 1440], checks: ['compact toolbar', 'distinct icons', 'nonsearchable status', 'keyboard Home End Enter', 'Escape focus isolation', 'heading icon in edit and preview', 'preview write entry hidden'], pageErrors: 0 }))
} catch (error) {
  await page.screenshot({ path: `${output}/failure.png`, fullPage: true })
  throw error
} finally { await browser.close() }
