// P3: production React page/native dialog, synthetic HTTP only. No live writes.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { browsePage } from '../tests/fixtures/browseContract.js'
import { preferences, profileData, previewData, versions, itemData } from '../tests/fixtures/profileSampling.js'

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright')
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react()],
  server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' })
await server.listen()
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage(), errors = [], external = [], writes = [], previewReads = []
page.setDefaultTimeout(15000)
page.on('pageerror', error => errors.push(error.message))
let score = '8.5', version = 1, tags = ['日常'], enabled = true, serial = 0, listFail = false, saveFail = false, holdSave = false, releaseSave
const prefs = () => preferences(score, version, tags, enabled)
const sampleCount = value => Number(value) <= 6 ? 4 : Number(value) <= 7 ? 3 : Number(value) <= 8.5 ? 2 : Number(value) <= 9 ? 1 : 0
const profile = () => profileData(prefs(), sampleCount(prefs().effective_profile_min_score))
const finish = async (route, data, status = 200) => { try { await route.fulfill({ status, json: data }) } catch (error) { if (!route.request().failure()) throw error } }
await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url())
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (!url.pathname.startsWith('/__9bqa__/')) return route.continue()
  const suffix = url.pathname.slice('/__9bqa__/recommendations/'.length)
  if (request.method() !== 'GET') {
    assert.equal(request.method(), 'PATCH'); assert.equal(suffix, 'preferences/')
    const body = request.postDataJSON(); writes.push(body)
    if (holdSave) await new Promise(resolve => { releaseSave = resolve })
    if (saveFail) return finish(route, {}, 503)
    if (body.expected_version !== version) return finish(route, {}, 409)
    score = body.profile_min_score ?? score; tags = body.tags; version++
    return finish(route, prefs())
  }
  if (suffix === 'capabilities/') return finish(route, { contract_version: 'recommendations.capabilities.v1',
    algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) })
  if (suffix === 'profile/') return finish(route, profile())
  if (suffix === 'preferences/') return finish(route, prefs())
  if (suffix === 'profile/preview/') {
    const value = url.searchParams.get('profile_min_score'); previewReads.push(value)
    return finish(route, previewData(value, prefs(), sampleCount(value)))
  }
  if (suffix === 'feedback/hidden/') return finish(route, { contract_version: 'recommendations.feedback.v1',
    feedback_version: 0, count: 0, results: [], page: 1, page_size: 12, next_page: null })
  assert.equal(suffix, '')
  if (listFail) return finish(route, {}, 503)
  const params = Object.fromEntries(url.searchParams), current = profile()
  const evidenceContext = { ...versions(prefs().effective_profile_min_score, version), feedback_version: 0 }
  const basis = current.cold_start_state.recommendation_basis
  const data = browsePage({ ids: basis === 'none' ? [] : [101, 102, 103],
    snapshot: (++serial).toString(16).padStart(32, '0'), context: evidenceContext,
    category: params.category || 'all', style: params.style || 'balanced',
    filters: { type: params.type || 'all', minScore: params.min_score || '0', hideRecorded: params.hide_recorded === 'true' },
    row: id => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: `作品${id}`, title_jp: '', score: 7.5,
      tags: ['日常', '音乐'], season: '2026-01', status_type: 'TV', watch_status: null, cover_url: '',
      evidence_token: 'b'.repeat(64), evidence_context: evidenceContext,
      evidence: { ...itemData('7.0', prefs().effective_profile_min_score).evidence, versions: evidenceContext,
        ...(basis !== 'personalized' ? { kind: 'explicit_preferences', source: null, shared_tags: ['日常'] } : {}) },
      reason: basis === 'personalized' ? '你对来源作品评分 7.0 分，共有音乐题材。' : '来自你选择的日常题材。' }) })
  data.cold_start_state = current.cold_start_state
  data.mode = { personalized: 'personalized', explicit_preferences: 'preference_based', none: 'cold_start' }[basis]
  return finish(route, data)
})
const output = path.resolve('../.tools/acceptance-sampling-p3')
await mkdir(output, { recursive: true })
const report = { scope: 'p3-sampling-isolated-browser', production_writes: false, checks: [] }
const check = name => { report.checks.push({ name, passed: true }); console.log('PASS: ' + name) }
const dialog = () => page.locator('.rec-dialog--preferences[open]')
const select = () => page.getByLabel('画像取样门槛：个人评分 ≥', { exact: true })
const load = async () => { await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`); await page.locator('[data-rec-motion-id="101"]').waitFor() }
const open = async () => {
  await page.getByRole('button', { name: '☷ 偏好设置', exact: true }).click(); await dialog().waitFor()
  await page.waitForFunction(() => getComputedStyle(document.querySelector('dialog')).transform === 'none')
}
const close = async () => { await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor() }
const saved = async () => { await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor() }
try {
  await load(); await open()
  assert.equal(await select().locator('option').count(), 9)
  const sections = await page.locator('.rec-preference-section').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().y))
  assert.ok(sections.every(y => Math.abs(y - sections[0]) < 1))
  await select().selectOption('7.0')
  await page.getByText('可分析样本 3 部 · 达到 3 部画像条件。', { exact: true }).waitFor()
  assert.equal(writes.length, 0); assert.equal(score, '8.5')
  assert.match(await dialog().innerText(), /已生效画像：可分析样本 2 部/)
  await page.screenshot({ path: path.join(output, 'desktop.png') })
  await close(); await open(); assert.equal(await select().inputValue(), '8.5'); await close()
  check('desktop nine-choice draft preview stays separate from actual profile; Escape cancels without writes')

  await open(); await select().selectOption('7.0')
  await page.getByRole('checkbox', { name: '音乐', exact: true }).check()
  saveFail = true
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByRole('button', { name: '重试保存喜好', exact: true }).waitFor()
  assert.equal(await select().inputValue(), '7.0')
  assert.equal(await page.getByRole('checkbox', { name: '音乐', exact: true }).isChecked(), true)
  saveFail = false; holdSave = true
  const before = writes.length
  await page.getByRole('button', { name: '重试保存喜好', exact: true }).evaluate(node => { node.click(); node.click(); node.click() })
  await page.getByRole('button', { name: '正在保存…', exact: true }).waitFor()
  await page.waitForFunction(() => window.browseQA.calls.filter(row => row.method === 'PATCH').length >= 2)
  assert.equal(writes.length, before + 1)
  assert.deepEqual(writes.at(-1), { tags: ['日常', '音乐'], expected_version: 1, profile_min_score: '7.0' })
  await page.keyboard.press('Escape'); assert.equal(await dialog().count(), 1)
  holdSave = false; releaseSave(); await saved()
  assert.equal(score, '7.0')
  assert.match(await page.locator('.rec-taste-note').innerText(), /个人评分 ≥ 7.0 分/)
  await page.locator('.rec-evidence summary').first().click()
  assert.match(await page.locator('.rec-evidence-body').first().innerText(), /你对《来源作品》的评分：7.0 分/)
  assert.match(await page.locator('.rec-evidence-body').first().innerText(), /个人评分 ≥ 7.0 分/)
  check('failure preserves drafts; rapid retry coalesces; atomic acknowledgement refreshes note and real low-score evidence')

  await open(); await select().selectOption('9.0'); listFail = true
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByText(/偏好已保存，推荐更新失败。已保存的设置不会丢失/).waitFor()
  const confirmedWrites = writes.length
  listFail = false; await page.getByRole('button', { name: '重试更新推荐', exact: true }).click()
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(writes.length, confirmedWrites)
  assert.match(await page.locator('.rec-taste-note').innerText(), /个人评分 ≥ 9.0 分/)
  assert.equal(await page.locator('.rec-evidence-policy').count(), 0)
  check('save success plus refresh failure recovers with GET only and removes obsolete low-score evidence')

  await open(); await select().selectOption('6.0')
  score = '10.0'; version++ // Isolated second tab, never production.
  await page.getByRole('button', { name: '保存喜好', exact: true }).click()
  await page.getByRole('button', { name: '读取最新设置，保留草稿', exact: true }).click()
  const conflictWrites = writes.length
  await page.getByRole('button', { name: '保留本页门槛并合并题材', exact: true }).click()
  assert.equal(await select().inputValue(), '6.0'); assert.equal(score, '10.0'); assert.equal(writes.length, conflictWrites)
  await page.getByRole('button', { name: '保存喜好', exact: true }).click(); await saved()
  assert.equal(score, '6.0'); assert.deepEqual(tags, ['日常', '音乐'])
  check('second-tab threshold conflict requires explicit scalar choice and another save, never silent overwrite')

  enabled = false; await load(); await open()
  assert.equal(await select().isDisabled(), true)
  assert.match(await dialog().innerText(), /已保存 ≥ 6.0 分 · 当前生效 ≥ 8.5 分/)
  await close(); enabled = true; await load(); await open()
  assert.equal(await select().inputValue(), '6.0'); assert.equal(await select().isEnabled(), true)
  await close()
  check('feature-off rollback uses 8.5 honestly without deleting stored threshold; re-enable restores saved 6.0')

  const noWrites = writes.length
  for (const size of [{ width: 390, height: 844 }, { width: 320, height: 640 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(size); await open()
    await select().scrollIntoViewIfNeeded(); await select().focus()
    assert.equal(await select().evaluate(node => node === document.activeElement), true)
    const layout = await dialog().evaluate(node => ({ overflow: node.scrollWidth > node.clientWidth + 1,
      right: node.getBoundingClientRect().right, bottom: node.getBoundingClientRect().bottom }))
    assert.equal(layout.overflow, false); assert.ok(layout.right <= size.width && layout.bottom <= size.height, JSON.stringify({ size, layout }))
    assert.equal(await page.getByRole('button', { name: '保存喜好', exact: true }).isVisible(), true)
    await page.screenshot({ path: path.join(output, `viewport-${size.width}-${size.height}.png`) })
    await close()
    assert.equal(await page.getByRole('button', { name: '☷ 偏好设置', exact: true }).evaluate(node => node === document.activeElement), true)
  }
  assert.equal(writes.length, noWrites); assert.deepEqual(errors, []); assert.deepEqual(external, [])
  check('narrow and short-landscape layouts preserve controls, footer, keyboard focus and Escape return without external requests')
  report.result = 'passed'; report.synthetic_writes = writes.length; report.synthetic_previews = previewReads.length
} catch (error) { report.result = 'failed'; report.failure = error.stack; throw error }
finally {
  releaseSave?.()
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
