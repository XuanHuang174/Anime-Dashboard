// Actual React page/dialog and HTTP transport. Synthetic local identity writes only.
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
const context = await browser.newContext({ viewport: { width: 1440, height: 950 } })
const page = await context.newPage(), requests = [], external = [], errors = [], linked = new Set(), events = new Set()
let epoch = 0, serial = 0, batchMode = 'normal', conflictId = null, searchHold = false, releaseBatch
const searchRoutes = []
const artifacts = path.resolve('../.tools/acceptance-identity-batch')
await mkdir(artifacts, { recursive: true })
const report = { scope: 'identity-batch-isolated-browser', production_writes: false, checks: [] }
const check = name => { report.checks.push({ name, passed: true }); console.log('PASS: ' + name) }
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
const row = id => ({ record_id: id, title: id === 1 ? '幸运星 OVA' : `旧作品${id}`, original_title: `Original ${id}`,
  season: id === 1 ? '2008-10' : '2026-1', identity_version: 0, status: 'needs_confirmation',
  candidates: [0, 1].map(n => ({ bangumi_id: 1000 + id + n * 100, title_zh: `标准作品${id}${n ? '另一版本' : ''}`,
    title_jp: `Original ${id}`, season: id === 1 ? '2008-07' : '2026-01', status_type: 'TV',
    ticket: `signed-${id}-${n}`, content_rating: 'non_adult' })) })
const finish = async (route, options) => { try { await route.fulfill(options) } catch (error) { if (!route.request().failure()) throw error } }
await context.route('**/*', async route => {
  const url = new URL(route.request().url()), method = route.request().method()
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (!url.pathname.startsWith('/__9bqa__/')) return route.continue()
  const suffix = url.pathname.slice('/__9bqa__/recommendations/'.length), params = Object.fromEntries(url.searchParams)
  const request = { suffix, params, method, body: method === 'POST' ? route.request().postDataJSON() : null }
  requests.push(request)
  if (method !== 'GET') {
    assert.equal(method, 'POST'); assert.equal(suffix, 'identities/batch/')
    if (batchMode === 'hold') await new Promise(resolve => { releaseBatch = resolve })
    const payload = request.body
    const results = payload.items.map(item => {
      if (item.record_id === conflictId) return { record_id: item.record_id, bangumi_id: item.bangumi_id,
        outcome: 'needs_review', code: 'identity_changed', message: '合成候选已变化，请重新搜索并核对。' }
      const existing = linked.has(item.record_id); linked.add(item.record_id); events.add(item.record_id)
      return { record_id: item.record_id, bangumi_id: item.bangumi_id, outcome: 'success', code: existing ? 'already_linked' : 'linked', version: 1 }
    })
    epoch++
    if (batchMode === 'lost') { batchMode = 'normal'; return route.abort('failed') }
    const success_count = results.filter(row => row.outcome === 'success').length
    return finish(route, { json: { contract_version: 'recommendations.identity-batch.v1', batch_id: payload.batch_id,
      processed_count: results.length, success_count, failed_count: results.length - success_count, results } })
  }
  if (suffix === 'capabilities/') return route.fulfill({ json: { contract_version: 'recommendations.capabilities.v1',
    algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'a'.repeat(64) } })
  if (suffix === 'profile/') return route.fulfill({ json: { contract_version: 'recommendations.3a.v1', eligible_sample_count: 29 + linked.size,
    usable_sample_count: 29 + linked.size, cold_start: false, sample_policy: { minimum_score: '8.5' },
    tag_summary: [{ tag: '音乐', sample_count: 3 }], identity_service_ready: true,
    diagnostics: { high_score_record_count: 77, pending_identity_count: 48 - linked.size, linked_high_score_record_count: 29 + linked.size,
      unavailable_identity_count: 0, stale_identity_count: 0, conflict_identity_count: 0 } } })
  if (suffix === 'preferences/') return route.fulfill({ json: { contract_version: 'recommendations.3a.v1', tags: [], version: 0, available_tags: ['音乐'] } })
  if (suffix === 'feedback/hidden/') return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1', feedback_version: 0,
    count: 0, results: [], page: 1, page_size: 12, next_page: null } })
  if (suffix === 'identities/') {
    let ids = Array.from({ length: 48 }, (_, n) => n + 1).filter(id => !linked.has(id))
    if (params.record_id) {
      const options = { json: { count: 1, page: 1, next_page: null, results: [row(Number(params.record_id))] } }
      if (searchHold) { searchRoutes.push({ route, options }); return }
      return route.fulfill(options)
    }
    const offset = (Number(params.page) - 1) * 5
    return route.fulfill({ json: { count: ids.length, page: Number(params.page), next_page: offset + 5 < ids.length ? Number(params.page) + 1 : null,
      results: ids.slice(offset, offset + 5).map(row) } })
  }
  assert.equal(suffix, '')
  return route.fulfill({ json: browsePage({ snapshot: (++serial).toString(16).padStart(32, '0'), category: params.category || 'all', style: params.style || 'balanced',
    context: { algorithm_version: 'content.v1', profile_version: epoch.toString(16).padStart(64, '0'), preference_version: 0, feedback_version: 0 },
    filters: { type: params.type || 'all', minScore: params.min_score || '0', hideRecorded: params.hide_recorded === 'true' } }) })
})
const listReads = () => requests.filter(row => row.suffix === '').length
const posts = () => requests.filter(row => row.method === 'POST')
const dialog = () => page.locator('.rec-dialog--identity[open]')
const open = async () => {
  await page.getByRole('button', { name: '核对旧作品 →', exact: true }).click()
  await page.getByRole('region', { name: '核对记录 1', exact: true }).waitFor()
  await page.waitForFunction(() => getComputedStyle(document.querySelector('dialog')).transform === 'none')
}
const pick = async id => {
  const region = page.getByRole('region', { name: `核对记录 ${id}`, exact: true })
  await region.getByRole('radio').first().check()
  await region.getByRole('checkbox').check()
}
const next = async first => { await page.getByRole('button', { name: '下一页', exact: true }).click(); await page.getByRole('region', { name: `核对记录 ${first}`, exact: true }).waitFor() }
const discard = async () => {
  await page.getByRole('button', { name: '完成核对并返回', exact: true }).click()
  await page.getByRole('button', { name: '放弃草稿并关闭', exact: true }).click()
  await dialog().waitFor({ state: 'detached' })
}
const load = async () => {
  linked.clear(); events.clear(); conflictId = null; batchMode = 'normal'
  await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`)
  await page.locator('[data-rec-motion-id="101"]').waitFor()
  await open()
}
try {
  await load(); const base = listReads(), initialPosts = posts().length
  await pick(1); await next(6); await pick(6)
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await page.getByRole('region', { name: '核对记录 1', exact: true }).waitFor()
  assert.equal(await page.getByRole('region', { name: '核对记录 1', exact: true }).getByRole('checkbox').isChecked(), true)
  await page.getByText(/季度或日期不同：2008-10 → 2008-07/).waitFor()
  assert.equal(posts().length, initialPosts); assert.equal(listReads(), base)
  await page.screenshot({ path: path.join(artifacts, 'desktop.png') })
  check('cross-page choices persist without writes or recommendation reads; date differences are visible')

  const first = page.getByRole('region', { name: '核对记录 1', exact: true })
  await first.getByRole('radio').last().check(); assert.equal(await first.getByRole('checkbox').isChecked(), false)
  await first.getByRole('checkbox').check()
  batchMode = 'hold'; await page.getByRole('button', { name: '关联已核对的 2 部', exact: true }).evaluate(node => { node.click(); node.click(); node.click() })
  await page.getByRole('button', { name: '正在关联 2 部…', exact: true }).waitFor()
  await page.waitForFunction(() => window.browseQA.calls.some(row => row.method === 'POST'))
  assert.equal(posts().length, initialPosts + 1)
  await page.keyboard.press('Escape'); assert.equal(await dialog().count(), 1)
  batchMode = 'normal'; releaseBatch()
  await page.getByText(/已处理 2 部：成功 2 部，0 部待处理/).waitFor()
  await page.getByRole('region', { name: '核对记录 2', exact: true }).waitFor()
  assert.equal(listReads(), base); assert.equal(events.size, 2)
  await page.getByRole('button', { name: '完成核对并返回', exact: true }).click()
  await dialog().waitFor({ state: 'detached' }); await page.locator('[data-rec-motion-id="101"]').waitFor()
  assert.equal(listReads(), base + 1)
  check('candidate changes reset confirmation; one POST handles rapid clicks; only closing triggers one recommendation refresh')

  await load(); await pick(1); await next(6); await pick(6); conflictId = 6
  await page.getByRole('button', { name: '上一页', exact: true }).click()
  await page.getByRole('region', { name: '核对记录 1', exact: true }).waitFor()
  await page.getByRole('button', { name: '关联已核对的 2 部', exact: true }).click()
  await page.getByText(/已处理 2 部：成功 1 部，1 部待处理/).waitFor()
  await page.locator('.rec-identity-drafts summary').click()
  await page.getByRole('button', { name: '定位核对', exact: true }).click()
  const second = page.getByRole('region', { name: '核对记录 6', exact: true })
  await second.waitFor()
  assert.equal(await second.getByRole('checkbox').isChecked(), false)
  assert.equal(await second.getByRole('checkbox').isEnabled(), false)
  conflictId = null; await second.getByRole('button', { name: '搜索候选', exact: true }).click()
  await second.getByRole('radio').first().check(); await second.getByRole('checkbox').check()
  await page.getByRole('button', { name: '关联已核对的 1 部', exact: true }).click()
  await page.getByText(/已处理 1 部：成功 1 部，0 部待处理/).waitFor()
  assert.equal(events.size, 2)
  check('partial conflicts keep failed rows and require renewed search/confirmation, never resubmit successful rows in new batch')

  await load(); await pick(1); batchMode = 'lost'
  await page.getByRole('button', { name: '关联已核对的 1 部', exact: true }).click()
  await page.getByRole('button', { name: '重试原批次', exact: true }).waitFor()
  const uncertain = posts().at(-1).body
  assert.equal(events.size, 1)
  await page.getByRole('button', { name: '重试原批次', exact: true }).click()
  await page.getByText(/已处理 1 部：成功 1 部，0 部待处理/).waitFor()
  assert.deepEqual(posts().at(-1).body, uncertain); assert.equal(events.size, 1)
  check('lost acknowledgement retries identical UUID/proof/version and never duplicates committed identity events')

  await load(); await pick(1)
  const beforeCancel = posts().length, beforeCancelReads = listReads()
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '继续核对', exact: true }).click()
  assert.equal(await first.getByRole('checkbox').isChecked(), true)
  await discard(); assert.equal(posts().length, beforeCancel); assert.equal(listReads(), beforeCancelReads)
  check('draft-only close requests explicit discard and does not write or recompute recommendations')

  await load(); await pick(1); await pick(2); searchHold = true
  await first.getByRole('button', { name: '搜索候选', exact: true }).click()
  assert.equal(await page.getByRole('region', { name: '核对记录 2', exact: true }).getByRole('checkbox').isChecked(), true)
  await next(6)
  searchHold = false; for (const old of searchRoutes.splice(0)) await finish(old.route, old.options)
  assert.equal(await page.getByRole('region', { name: '核对记录 1', exact: true }).count(), 0)
  await page.getByRole('button', { name: '完成核对并返回', exact: true }).click()
  await page.getByRole('button', { name: '放弃草稿并关闭', exact: true }).click()
  check('row searches do not clear unrelated choices; paging cancels late results without cross-page contamination')

  await load()
  for (const size of [{ width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 320, height: 640 }]) {
    await page.setViewportSize(size)
    assert.equal(await dialog().evaluate(node => node.scrollWidth <= node.clientWidth + 1), true)
    const y = (await page.locator('.rec-identity-footer').boundingBox()).y
    await page.locator('.rec-identity-content').evaluate(node => { node.scrollTop = node.scrollHeight })
    assert.equal((await page.locator('.rec-identity-footer').boundingBox()).y, y)
    const rect = await page.getByRole('button', { name: '完成核对并返回', exact: true }).boundingBox()
    assert.ok(rect.y >= 0 && rect.y + rect.height <= size.height)
    await page.screenshot({ path: path.join(artifacts, `viewport-${size.width}.png`) })
  }
  await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'detached' })
  check('desktop/tablet/narrow phone retain fixed footer, independent list scroll, native Escape and no overflow')
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.failure = error.stack; throw error }
finally {
  releaseBatch?.()
  for (const old of searchRoutes.splice(0)) await finish(old.route, old.options)
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
