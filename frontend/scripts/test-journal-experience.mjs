// Real browser and isolated synthetic Django data. Run after seed-watch-review-c2-e2e.py.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, mkdir } from 'node:fs/promises'
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const f = JSON.parse(await readFile('../.tools/watch-review-c2-fixture.json', 'utf8'))
assert.match(f.username, /^plans-b2-/)
const base = 'http://127.0.0.1:5191', output = '../.tools/journal-experience-qa'
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' })
await context.addInitScript(({ token, user_id }) => {
  localStorage.setItem('anime_journal_access_token', token)
  localStorage.setItem(`anime-dashboard:journal-display-mode:v1:${user_id}`, 'preview')
  window.__revealRuns = []; window.__revealErrors = []
  const animate = Element.prototype.animate
  Element.prototype.animate = function(...args) {
    if (this.matches('.animate-item')) window.__revealRuns.push(this)
    return animate.apply(this, args)
  }
  function frame() {
    document.querySelectorAll('.animate-item').forEach(el => {
      if (!el.getClientRects().length) return
      const r = el.getBoundingClientRect(), state = el.dataset.revealState
      if (r.bottom > 0 && r.top < innerHeight && (!state || state === 'pending' && Number(getComputedStyle(el).opacity) > .001)) {
        window.__revealErrors.push({ state, text: el.textContent.slice(0, 25) })
      }
    })
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}, f)
const page = await context.newPage(); page.setDefaultTimeout(20000)
const errors = []; page.on('pageerror', e => errors.push(e.message))
const nav = name => page.getByRole('navigation', { name: '私人手账工作区' }).getByRole('button', { name, exact: true })
const api = async path => {
  const r = await context.request.get(base + '/api/' + path, { headers: { Authorization: `Bearer ${f.token}` } })
  assert.equal(r.status(), 200, await r.text()); return r.json()
}
const close = async () => { await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click(); await page.getByRole('dialog').waitFor({ state: 'detached' }) }
const edit = async title => page.locator('tbody tr').filter({ hasText: title }).getByRole('button', { name: '修改', exact: true }).click()
try {
  await page.goto(base + '/dashboard'); await nav('手账').waitFor()
  assert.match(await page.locator('.journal-identity-navigation').innerText(), /个人手账\s*\/ 追番记录/)
  for (const view of ['#btnViewGrid', '#btnViewList']) {
    await page.locator(view).click(); await page.waitForTimeout(600)
    for (let y = 0; y < 6500; y += 450) { await page.evaluate(y => scrollTo(0, y), y); await page.waitForTimeout(45) }
    await page.waitForTimeout(650); await page.evaluate(() => scrollTo(0, 0)); await page.waitForTimeout(300)
  }
  const reveal = await page.evaluate(() => {
    const runs = new Map(); __revealRuns.forEach(el => runs.set(el, (runs.get(el) || 0) + 1))
    return { count: runs.size, repeated: [...runs.values()].filter(n => n > 1).length, errors: __revealErrors }
  })
  assert.ok(reveal.count > 0); assert.equal(reveal.repeated, 0); assert.deepEqual(reveal.errors, [])
  await nav('回顾').click()
  const review = page.getByRole('region', { name: '私人观看回顾工作区' })
  assert.equal(await review.evaluate(el => getComputedStyle(el).animationName), 'plan-reveal')
  await review.getByLabel('回顾月份', { exact: true }).fill(f.month)
  await review.getByRole('button', { name: '查看回顾', exact: true }).click()
  await review.locator('.review-summary[aria-busy=false]').waitFor()
  assert.equal(await review.locator('.review-header').evaluate(el => {
    const a = el.querySelector('.review-sticker').getBoundingClientRect(), b = el.querySelector('h2').getBoundingClientRect()
    return a.right < b.left && Math.abs(a.top - b.top) < 25
  }), true)
  await page.screenshot({ path: output + '/review-desktop.png' })
  await review.getByRole('button', { name: '导出回顾长图', exact: true }).click()
  const exporter = page.getByRole('dialog', { name: '导出观看回顾' })
  await exporter.getByRole('button', { name: '读取并预览完整内容', exact: true }).click()
  await exporter.getByRole('button', { name: '下载回顾长图', exact: true }).waitFor()
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 }); await page.waitForTimeout(150)
    await exporter.locator('.review-export-preview').scrollIntoViewIfNeeded()
    assert.equal(await exporter.locator('.review-export-preview').evaluate(el => el.scrollWidth <= el.clientWidth + 1 && el.querySelector('.review-export-sheet').getBoundingClientRect().width <= el.clientWidth + 1), true)
    assert.equal(await exporter.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
    assert.equal(await exporter.getByRole('button', { name: '关闭面板', exact: true }).evaluate(el => el.getBoundingClientRect().top >= 0), true)
    await page.screenshot({ path: `${output}/export-fit-${width}.png` })
  }
  await page.setViewportSize({ width: 390, height: 900 })
  await exporter.getByRole('button', { name: '原尺寸查看', exact: true }).click()
  assert.equal(await exporter.locator('.review-export-preview').evaluate(el => el.scrollWidth > el.clientWidth), true)
  await exporter.getByRole('button', { name: '关闭面板', exact: true }).click()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await nav('手账').click(); await page.locator('#btnViewList').click()
  await page.locator('tbody tr').filter({ hasText: 'B2 准备重看' }).getByRole('button', { name: /详情|查看/ }).first().click()
  const history = page.getByRole('region', { name: '私人观看历程', exact: true })
  await history.getByRole('button').first().click()
  await history.getByRole('button', { name: '前往编辑模式管理', exact: true }).click()
  await page.getByRole('dialog', { name: '观看历程管理', exact: true }).waitFor()
  await page.getByRole('button', { name: '更正第 1 轮', exact: true }).click()
  const form = page.locator('.watch-form')
  await form.getByLabel('本轮已看集数', { exact: true }).fill('8')
  await form.getByLabel('本轮总集数（未知填 0）', { exact: true }).fill('12')
  assert.match(await form.locator('.watch-episode-reference').innerText(), /12/)
  assert.match(await form.innerText(), /集数将同步到作品当前进度/)
  await form.getByRole('button', { name: '保存这条记录', exact: true }).click(); await form.waitFor({ state: 'detached' })
  const corrected = await api(`records/${f.records[2]}/`)
  assert.equal(corrected.watch_status, 'completed'); assert.equal(corrected.watched_eps, 8); assert.equal(corrected.total_eps, 12)
  await page.getByRole('button', { name: '返回作品编辑', exact: true }).click()
  assert.equal(await page.locator('input[name=watchedEps]').inputValue(), '8')
  assert.equal(await page.locator('input[name=totalEps]').inputValue(), '12')
  await page.screenshot({ path: output + '/completed-editor.png' }); await close()
  assert.match(await page.locator('.journal-identity-navigation').innerText(), /个人手账\s*\/ 追番管理/)
  const add = page.locator('.add-anime-placeholder-list'); await add.scrollIntoViewIfNeeded()
  await page.waitForFunction(() => document.querySelector('.add-anime-placeholder-row')?.dataset.revealState === 'done')
  assert.equal(await add.evaluate(el => getComputedStyle(el.closest('.animate-item')).opacity), '1'); assert.ok(await add.innerText())
  await edit('B2 恢复搁置')
  await page.locator('input[name=watchedEps]').fill('6'); await page.locator('input[name=totalEps]').fill('12')
  const saved = page.waitForResponse(r => r.url().includes(`/api/records/${f.records[1]}/details/`) && r.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存全部修改', exact: false }).click(); assert.equal((await saved).status(), 200)
  await page.waitForTimeout(150); await close()
  const held = await api(`records/${f.records[1]}/`); assert.equal(held.watch_status, 'on_hold'); assert.equal(held.watched_eps, 6)
  await edit('B2 进行中'); await page.getByRole('button', { name: '观看历程', exact: true }).click()
  await page.getByRole('button', { name: '记录进度', exact: true }).click()
  await form.getByLabel('本轮已看集数', { exact: true }).fill('5')
  assert.match(await form.locator('.watch-episode-reference').innerText(), /12/)
  await form.getByLabel('随记内容', { exact: true }).fill('未保存的进度草稿')
  await page.setViewportSize({ width: 390, height: 900 }); await page.screenshot({ path: output + '/progress-mobile.png' })
  assert.equal(await form.getByLabel('本轮已看集数', { exact: true }).inputValue(), '5')
  await page.goto(base + '/tests/fixtures/watchPlansB3Recommendation.html')
  await page.getByRole('button', { name: '安排B3 推荐安排合成作品观看计划', exact: true }).click()
  const arrange = page.getByRole('dialog', { name: '加入观看计划', exact: true }); await arrange.waitFor()
  assert.equal(await arrange.evaluate(el => getComputedStyle(el).borderTopWidth), '3px')
  assert.equal(await arrange.locator('.rec-dialog-header').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(78, 205, 196)')
  assert.equal(await arrange.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true)
  await page.screenshot({ path: output + '/recommendation-arrange-mobile.png' })
  await arrange.getByRole('button', { name: '取消', exact: true }).click(); await arrange.waitFor({ state: 'detached' })
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ result: 'passed', reveal, checks: ['labels', 'single prepaint reveal', 'review entrance and layout', 'fit/original preview', 'direct edit placeholder', 'current completed round sync', 'on-hold editing', 'episode reference and draft', 'recommendation dialog theme'] }))
} catch (e) { await page.screenshot({ path: output + '/failure.png' }); console.log((await page.locator('body').innerText()).slice(-1600)); throw e }
finally { await context.close(); await browser.close() }
