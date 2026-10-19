// Local synthetic backend + actual production React components/native Dialog.
// No production writes, real clipboard, Magnet launches or outbound network.
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
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage(), errors = [], external = [], requests = [], snapshots = new Map(), owned = new Set()
let epoch = 0, serial = 0, hold = false, held = [], failure = null, resourceHold = false, resourceRoutes = []
let browseEnabled = true
const phaseR3 = process.env.BROWSE_ACCEPTANCE_PHASE === 'r3'
const phaseR2 = process.env.BROWSE_ACCEPTANCE_PHASE === 'r2' || phaseR3
const phaseR1 = process.env.BROWSE_ACCEPTANCE_PHASE === 'r1' || phaseR2
let layoutCase = 'ready', profileHold = false, profileHeld = []
const coldState = () => ({ contract_version: 'recommendations.cold-start.v1', code: 'empty_journal', reasons: ['empty_journal'],
  minimum_samples: 3, explicit_preference_count: layoutCase === 'explicit' ? 1 : 0, journal_record_count: 0,
  rated_record_count: 0, unrated_record_count: 0, high_score_record_count: 0, eligible_sample_count: 0,
  usable_sample_count: 0, pending_identity_count: 0, missing_genre_count: 0, unavailable_sample_count: 0,
  is_cold_start: true, recommendation_basis: layoutCase === 'explicit' ? 'explicit_preferences' : 'none' })
const layoutProfile = () => ({ contract_version: 'recommendations.3a.v1', eligible_sample_count: 63, usable_sample_count: 63,
  cold_start: false, sample_policy: { minimum_score: '8.5', minimum_samples: 3 },
  tag_summary: ['音乐', '日常', '治愈'].map(tag => ({ tag, sample_count: 20 })), identity_service_ready: true,
  diagnostics: { high_score_record_count: 77, pending_identity_count: 14, linked_high_score_record_count: 63,
    unavailable_identity_count: 0, stale_identity_count: 0, conflict_identity_count: 0 },
  ...(['cold', 'explicit'].includes(layoutCase) ? { eligible_sample_count: 0, usable_sample_count: 0,
    cold_start: true, tag_summary: [], diagnostics: undefined, cold_start_state: coldState(),
    extraction: { explicit_preferences: { tags: layoutCase === 'explicit' ? ['音乐'] : [], version: 0 } } } : {}) })
const phaseD3 = process.env.BROWSE_ACCEPTANCE_PHASE === 'v2d3'
const phaseD2 = process.env.BROWSE_ACCEPTANCE_PHASE === 'v2d2' || phaseD3
const phase9c = process.env.BROWSE_ACCEPTANCE_PHASE === '9c' || phaseD2 || phaseR1
const recipe = { contract_version: 'recommendations.execution.v1', algorithm_version: 'content.v2',
  ranking_policy_version: 'balanced-rerank.v3', recall_policy_version: 'source-pattern-recall.v2',
  runtime_version: 'pattern-prefix-runtime.v1', control_policy_version: 'browse-controls.v2',
  candidate_limit: 400, result_limit: 120, batch_size: 24, default_page_size: 12,
  control_policy_sha256: 'c'.repeat(64), recipe_revision: 'd'.repeat(64), serving_enabled: phaseD3 }
const prepared = () => phaseD2 && browseEnabled
const artifacts = path.resolve(phaseR3 ? '../.tools/acceptance-r3-browser' : phaseR2 ? '../.tools/acceptance-r2-browser' : phaseR1 ? '../.tools/acceptance-r1-browser' : phaseD3 ? '../.tools/acceptance-v2d3-browser' : phaseD2 ? '../.tools/acceptance-v2d2-browser' : phase9c ? '../.tools/acceptance-9c-browser' : '../.tools/acceptance-9b-browser')
await mkdir(artifacts, { recursive: true })
page.setDefaultTimeout(10000)
page.on('pageerror', error => errors.push(error.message))
const finish = async (route, options) => { try { await route.fulfill(options) } catch (error) { if (!route.request().failure()) throw error } }
const evidenceContext = () => ({ algorithm_version: prepared() ? 'content.v2' : 'content.v1', profile_version: epoch.toString(16).padStart(64, '0'), preference_version: 0, feedback_version: 0,
  ...(phaseR2 ? { ranking_policy_version: 'content-ranking.v1', taxonomy_version: 'genres.v4', feature_version: 'genres-idf.v4',
    feature_rule_version: 'journal-genres.v3', selection_rule_version: 'evidence-selection.v1', catalog_feature_version: 'f'.repeat(64) } : {}),
  ...(prepared() ? { ranking_policy_version: recipe.ranking_policy_version, recall_policy_version: recipe.recall_policy_version,
    runtime_version: recipe.runtime_version, control_policy_version: recipe.control_policy_version,
    recipe_revision: recipe.recipe_revision, browse_context_version: epoch.toString(16).padStart(64, '0'),
    catalog_feature_version: 'f'.repeat(64) } : {}) })
const row = id => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: phaseR1 && layoutCase === 'long' && id === 101 ? '长标题边界作品'.repeat(70) : `作品${id}`, title_jp: '', tags: ['音乐'], score: 7.5,
  season: '2026-04', status_type: 'TV', synopsis: '隔离测试正文。'.repeat(100), evidence: null, reason: '',
  evidence_context: evidenceContext(), evidence_token: epoch.toString(16).padStart(64, '0'), watch_status: owned.has(id) ? 'plan' : null,
  ...(phaseR2 && layoutCase === 'surface' ? { title_zh: id === 101 ? '海风与放课后的合奏' : ['放学后的星空', '春日的长信', '风经过的街角'][id % 3],
    cover_url: `https://media.xh-anime.com/covers/${id}.jpg`, tags: ['音乐', '日常'],
    reason: '因为你给《合成来源作品》打了 9.1 分，这部作品同样包含“音乐”和“日常”题材。',
    evidence: { contract_version: 'recommendations.evidence.v1', kind: 'rated_work',
      source: { bangumi_id: 81, title: '合成来源作品', personal_score: '9.1' }, shared_tags: ['音乐', '日常'],
      feature_rule_version: 'journal-genres.v3', interpretation: 'shared_genre_association',
      is_probability: false, is_exhaustive_ranking_explanation: false, versions: evidenceContext() } } : {}),
  ...(prepared() ? { detail_context: `prepared-${epoch}-${id}` } : {}) })
await context.route('**/*', async route => {
  const url = new URL(route.request().url()), method = route.request().method()
  if (phaseR2 && url.origin === 'https://media.xh-anime.com' && /^\/covers\/\d+\.jpg$/.test(url.pathname)) {
    const id = Number(url.pathname.split('/').at(-1).split('.')[0])
    if (id === 105) return route.fulfill({ status: 404, body: '' })
    const colors = ['#4ecdc4', '#ffe66d', '#ff8fab', '#aacbe4']
    // Test-only vector poster: fulfilled locally, never downloaded or shipped.
    return route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800"><rect width="600" height="800" fill="${colors[id % 4]}"/><circle cx="490" cy="130" r="150" fill="#fff5ee"/><path d="M0 600L600 250V800H0Z" fill="#111"/><path d="M50 70H350M50 90H250" stroke="#111" stroke-width="5"/><circle cx="190" cy="370" r="110" fill="#fff5ee" stroke="#111" stroke-width="5"/><path d="M150 405V325L235 305V385M150 325L235 305" fill="none" stroke="#111" stroke-width="12"/><circle cx="130" cy="405" r="25" fill="#111"/><circle cx="215" cy="385" r="25" fill="#111"/><text x="40" y="650" fill="white" font-size="30" font-family="sans-serif">合成验收海报 / ${id}</text><text x="40" y="700" fill="white" font-size="17" font-family="sans-serif">LOCAL VISUAL FIXTURE</text></svg>` })
  }
  if (url.origin !== origin) { external.push(url.origin); return route.abort() }
  if (url.pathname === '/api/utils/anime-search/') {
    if (resourceHold) { resourceRoutes.push(route); return }
    return route.fulfill({ status: 503, json: { status: 'error', message: '合成资源失败' } })
  }
  if (!url.pathname.startsWith('/__9bqa__/')) return route.continue()
  requests.push({ path: url.pathname, params: Object.fromEntries(url.searchParams), method })
  const suffix = url.pathname.slice('/__9bqa__/recommendations/'.length)
  if (suffix === 'capabilities/') return route.fulfill({ json: {
    contract_version: 'recommendations.capabilities.v1', algorithm_version: prepared() ? 'content.v2' : 'content.v1',
    browse_enabled: browseEnabled, rollout_revision: 'a'.repeat(64),
    ...(prepared() ? { execution_recipe: recipe, browse_contract_version: 'recommendations.browse.v2' } : {}) } })
  if (suffix === 'profile/' && phaseR1) {
    const payload = layoutProfile()
    if (profileHold) await new Promise(resolve => profileHeld.push(resolve))
    return finish(route, layoutCase === 'error' ? { status: 503, json: {} } : { json: payload })
  }
  if (suffix === 'identities/' && phaseR1) return route.fulfill({ json: { count: 0, page: 1, next_page: null, results: [] } })
  if (suffix === 'profile/') return route.fulfill({ json: { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, usable_sample_count: 3,
    cold_start: false, sample_policy: { minimum_score: '8.5' }, tag_summary: [{ tag: '音乐', sample_count: 3 }] } })
  if (suffix === 'preferences/') return route.fulfill({ json: { contract_version: 'recommendations.3a.v1', version: 0,
    tags: phaseR1 && layoutCase === 'explicit' ? ['音乐'] : [], available_tags: ['音乐'] } })
  if (suffix === 'feedback/hidden/') return route.fulfill({ json: { contract_version: 'recommendations.feedback.v1', feedback_version: 0,
    count: 0, results: [], page: 1, page_size: 12, next_page: null } })
  if (/^\d+\/want\/$/.test(suffix)) {
    const id = Number(suffix.split('/')[0]); owned.add(id); epoch++
    return route.fulfill({ status: 201, json: { contract_version: 'recommendations.want.v1', bangumi_id: id, anime_id: id + 1000, record_id: id + 2000,
      content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' } })
  }
  if (/^\d+\/$/.test(suffix)) return route.fulfill({ json: row(Number(suffix.slice(0, -1))) })
  assert.equal(suffix, '')
  const params = Object.fromEntries(url.searchParams)
  if (params.browse || params.cursor) {
    if (!browseEnabled) return route.fulfill({ status: params.cursor ? 409 : 503, json: {
      code: 'browse_disabled', fallback: 'legacy_v1', algorithm_version: 'content.v1' } })
  } else {
    const p = Number(params.page || 1)
    const ids = Array.from({ length: 50 }, (_, n) => 101 + n).filter(id => !owned.has(id))
    return route.fulfill({ json: { contract_version: 'recommendations.3b.v1', algorithm_version: 'content.v1',
      mode: 'personalized', count: ids.length, page: p, page_size: 12, next_page: p * 12 < ids.length ? p + 1 : null,
      state_token: 'c'.repeat(64), evidence_context: evidenceContext(), results: ids.slice((p - 1) * 12, p * 12).map(row) } })
  }
  let snapshot, offset = 0
  if (params.cursor) {
    const [id, position] = params.cursor.split(':'); snapshot = snapshots.get(id); offset = Number(position)
    if (!snapshot || snapshot.epoch !== epoch) return route.fulfill({ status: 409, json: {} })
  } else {
    assert.equal(params.browse, 'true'); assert.equal(params.page_size, '12')
    const category = params.category || 'all', base = category === 'all' ? 101 : category === 'familiar' ? 301 : 501
    const ids = Array.from({ length: 50 }, (_, n) => base + n).filter(id => params.hide_recorded !== 'true' || !owned.has(id))
    const style = params.style || 'balanced'
    if (style === 'open') ids.reverse()
    const id = (++serial).toString(16).padStart(32, '0')
    snapshot = { id, ids: params.year_from === '2099' || (phaseR1 && layoutCase === 'cold') ? [] : ids, category, style, epoch,
      filters: { type: params.type || 'all', minScore: params.min_score || '0', yearFrom: params.year_from || '', yearTo: params.year_to || '', hideRecorded: params.hide_recorded === 'true' } }
    snapshots.set(id, snapshot)
  }
  const listRow = id => { const { synopsis, ...rest } = row(id); return rest }
  const response = browsePage({ ...snapshot, snapshot: snapshot.id, offset, context: evidenceContext(), row: listRow,
    makeCursor: position => `${snapshot.id}:${position}` })
  if (phaseR1 && ['cold', 'explicit'].includes(layoutCase)) Object.assign(response, {
    mode: layoutCase === 'cold' ? 'cold_start' : 'preference_based', cold_start_state: coldState() })
  if (prepared()) Object.assign(response, { algorithm_version: 'content.v2', execution_recipe: recipe,
    browse_contract_version: 'recommendations.browse.v2', control_policy_version: recipe.control_policy_version })
  if (hold) await new Promise(resolve => held.push(resolve))
  if (failure) return finish(route, { status: failure, json: {} })
  return finish(route, { json: response })
})
const report = { scope: phaseR3 ? 'r3-motion-and-existing-interaction-regression' : phaseR2 ? 'r2-journal-visual-and-interaction-regression' : phaseR1 ? 'r1-layout-and-existing-browse-regression' : phaseD2 ? 'v2d2-isolated-component-integration' : '9b-isolated-component-integration', live_writes: false, checks: [] }
const check = name => { report.checks.push({ name, passed: true }); console.log('PASS: ' + name) }
const listCalls = () => requests.filter(request => request.path.endsWith('/recommendations/'))
const settleDialog = () => page.waitForFunction(() => {
  const node = document.querySelector('dialog[open]')
  return node && getComputedStyle(node).transform === 'none' && getComputedStyle(node).opacity === '1'
})
const settleMotion = async () => {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.waitForFunction(() => window.browseQA.motion().length === 0)
}
const openFilter = async () => { await page.getByRole('button', { name: /☷ 筛选/ }).click(); await settleDialog() }
const close = () => page.getByRole('button', { name: '关闭面板', exact: true }).click()
const open = async id => { await page.getByRole('button', { name: `查看作品${id}详情`, exact: true }).click(); await settleDialog() }
const waitList = async predicate => {
  const deadline = Date.now() + 10000
  while (!predicate(listCalls().at(-1))) { assert.ok(Date.now() < deadline, 'expected recommendation request did not arrive'); await new Promise(resolve => setTimeout(resolve, 10)) }
}
const waitHeld = async () => { while (!held.length) await new Promise(resolve => setTimeout(resolve, 20)) }
const restore = async () => { hold = false; for (const done of held.splice(0)) done() }
const cards = () => page.locator('.rec-card').count()
const load = async () => { await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`); await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor() }
try {
  await load(); assert.equal(await cards(), 12)
  let count = listCalls().length
  await page.getByRole('combobox', { name: '推荐风格', exact: true }).selectOption('balanced')
  await page.getByRole('button', { name: '为你发现', exact: true }).click()
  assert.equal(listCalls().length, count)
  await openFilter(); await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2015')
  await page.getByRole('textbox', { name: '结束年份', exact: true }).fill('2026')
  assert.equal(listCalls().length, count)
  await close(); assert.equal(listCalls().length, count)
  await openFilter(); assert.equal(await page.getByRole('textbox', { name: '起始年份', exact: true }).inputValue(), '')
  await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2026')
  await page.getByRole('textbox', { name: '结束年份', exact: true }).fill('2015')
  await page.getByRole('button', { name: '应用筛选', exact: true }).click()
  await page.getByText('年份起点不能大于终点。', { exact: true }).waitFor()
  assert.equal(listCalls().length, count)
  await page.getByRole('button', { name: '恢复默认', exact: true }).last().click(); await close()
  assert.equal(listCalls().length, count)
  check('same controls are no-ops; filter draft/close/reset do not fetch; reversed years stay local')

  await openFilter(); await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2015')
  await page.getByRole('textbox', { name: '结束年份', exact: true }).fill('2026')
  await page.getByLabel('作品类型', { exact: true }).selectOption('TV')
  await page.getByLabel('最低 Bangumi 评分', { exact: true }).selectOption('7')
  await page.getByRole('button', { name: '应用筛选', exact: true }).click()
  await page.getByRole('button', { name: '移除条件：2015—2026', exact: true }).waitFor()
  await waitList(request => request?.params.year_from === '2015')
  assert.equal(listCalls().at(-1).params.year_from, '2015')
  await page.getByRole('button', { name: '移除条件：2015—2026', exact: true }).click()
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  await waitList(request => request?.params.year_from === undefined)
  assert.equal(listCalls().at(-1).params.year_from, undefined)
  await page.getByRole('button', { name: '恢复默认', exact: true }).click()
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  check('applied filter summary and single removal reflect actual server parameters')

  failure = 503; await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.getByRole('button', { name: '重试加载本批更多', exact: true }).waitFor()
  const failedPage = listCalls().at(-1).params.cursor; assert.equal(await cards(), 12)
  failure = null; await page.getByRole('button', { name: '重试加载本批更多', exact: true }).click()
  await page.getByRole('button', { name: '查看作品124详情', exact: true }).waitFor()
  assert.equal(listCalls().at(-1).params.cursor, failedPage); assert.equal(await cards(), 24)
  await page.getByRole('button', { name: '恢复默认', exact: true }).click()
  await page.getByRole('button', { name: '加载本批更多', exact: true }).waitFor()
  check('append error is local and retry uses the same cursor without duplicate cards')

  hold = true; count = listCalls().length
  await page.getByRole('button', { name: '加载本批更多', exact: true }).evaluate(node => { for (let i = 0; i < 20; i++) node.click() })
  await waitHeld(); assert.equal(listCalls().length, count + 1)
  assert.ok(await page.getByRole('button', { name: /换一批，看看/ }).isDisabled())
  await restore(); await page.getByRole('button', { name: '查看作品124详情', exact: true }).waitFor()
  assert.equal(await cards(), 24)
  failure = 503; await page.getByRole('button', { name: /换一批，看看/ }).click()
  await page.getByRole('button', { name: '重试原目标批次 →', exact: true }).waitFor()
  const failedTarget = listCalls().at(-1).params.cursor; assert.equal(await cards(), 24)
  failure = null; await page.getByRole('button', { name: '重试原目标批次 →', exact: true }).click()
  await page.getByRole('button', { name: '查看作品125详情', exact: true }).waitFor()
  assert.equal(listCalls().at(-1).params.cursor, failedTarget)
  assert.equal(await cards(), 12)
  assert.equal(await page.locator('#rec-results').evaluate(node => node === document.activeElement), true)
  check('continuation coalesces; next-batch error keeps cards; retry replaces instead of appending and restores focus')

  failure = 409; await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
  await page.getByText('浏览依据已更新，请重新加载推荐。', { exact: true }).waitFor()
  assert.equal(await cards(), 12)
  assert.ok(await page.getByRole('button', { name: '查看作品125详情', exact: true }).isDisabled())
  failure = null; await page.getByRole('button', { name: '重新加载推荐', exact: true }).click()
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  assert.equal(listCalls().at(-1).params.cursor, undefined)
  check('409 freezes the old stream until explicit first-batch reload')

  await page.getByRole('button', { name: /换一批，看看/ }).click()
  await page.getByRole('button', { name: '查看作品125详情', exact: true }).waitFor()
  await page.getByRole('button', { name: /换一批，看看/ }).click()
  await page.getByText('本轮符合条件的作品已展示完。可以调整筛选，或重新开始浏览。', { exact: true }).waitFor()
  assert.equal(await cards(), 2)
  await page.getByRole('button', { name: '重新开始浏览', exact: true }).click()
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  check('exhaustion ends the round without looping; explicit restart obtains a new first batch')

  await open(101); const trigger = page.getByRole('button', { name: '🧲 寻找资源', exact: true })
  await trigger.click(); await page.getByText('合成资源失败', { exact: false }).waitFor()
  assert.equal(await cards(), 12); await page.keyboard.press('Escape'); await close()
  hold = true; await page.getByRole('button', { name: '加载本批更多', exact: true }).click(); await waitHeld()
  await page.getByRole('combobox', { name: '推荐风格', exact: true }).selectOption('open')
  await restore(); await page.getByRole('button', { name: '查看作品150详情', exact: true }).waitFor()
  assert.equal(await cards(), 12)
  assert.equal(await page.getByRole('button', { name: '查看作品113详情', exact: true }).count(), 0)
  check('resource failure remains local; a late page cannot enter a changed-style session')

  await open(150); resourceHold = true; await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
  await page.getByRole('button', { name: '熟悉的心动', exact: true }).evaluate(node => node.click())
  await page.getByRole('button', { name: '查看作品350详情', exact: true }).waitFor()
  assert.equal(await page.locator('dialog').count(), 0)
  for (const route of resourceRoutes.splice(0)) await finish(route, { json: { status: 'success', results: [] } })
  resourceHold = false
  check('category change closes native detail/resource sessions before late search completion')

  await page.getByRole('button', { name: '为你发现', exact: true }).click()
  await page.getByRole('button', { name: '查看作品150详情', exact: true }).waitFor()
  await page.getByRole('combobox', { name: '推荐风格', exact: true }).selectOption('balanced')
  await page.getByRole('button', { name: '查看作品101详情', exact: true }).waitFor()
  await open(101); await page.getByRole('button', { name: '作品101：+ 加入想看', exact: true }).last().click()
  await page.getByRole('button', { name: '作品101：已在想看', exact: true }).last().waitFor()
  await close(); assert.equal(await page.getByRole('button', { name: '查看作品101详情', exact: true }).count(), 0)
  assert.equal(listCalls().at(-1).params.cursor, undefined)
  assert.equal(requests.filter(request => request.method === 'POST').length, 1)
  check('synthetic want confirmation rereads whole detail and resets browse to first batch without duplicate POST')

  await openFilter(); await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2099')
  await page.getByRole('button', { name: '应用筛选', exact: true }).click()
  await page.getByRole('button', { name: '调整筛选', exact: true }).waitFor()
  assert.ok(await page.getByRole('button', { name: '移除条件：2099—不限', exact: true }).isVisible())
  await page.getByRole('button', { name: '清除本次筛选', exact: true }).click()
  await page.getByRole('button', { name: '查看作品102详情', exact: true }).waitFor()
  await page.screenshot({ path: path.join(artifacts, 'desktop.png'), fullPage: true })
  check('empty results retain applied conditions and provide dedicated filter recovery')

  await page.setViewportSize({ width: 390, height: 844 }); await openFilter()
  const geometry = await page.locator('dialog[open]').evaluate(node => { const r = node.getBoundingClientRect(); return { modal: node.matches(':modal'), bottom: r.bottom, width: r.width, viewport: innerWidth } })
  assert.equal(geometry.modal, true); assert.equal(geometry.width, geometry.viewport)
  assert.ok(Math.abs(geometry.bottom - 844) < 3)
  await page.keyboard.press('Escape'); await page.locator('dialog').waitFor({ state: 'detached' })
  const rows = await page.locator('.rec-controls').evaluate(node => ({ category: node.querySelector('.rec-categories').getBoundingClientRect().bottom,
    tools: node.querySelector('.rec-browse-tools').getBoundingClientRect().top }))
  assert.ok(rows.tools >= rows.category)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.screenshot({ path: path.join(artifacts, 'mobile.png'), fullPage: true })
  check('mobile controls use a second row and native bottom-sheet focus/escape without horizontal overflow')
  if (phase9c) {
    await open(102); resourceHold = true
    await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
    browseEnabled = false
    // Simulate an already scheduled background continuation while a native
    // detail/resource dialog is open; don't bypass it for real user writes.
    await page.getByRole('button', { name: '加载本批更多', exact: true }).evaluate(node => node.click())
    await page.getByText('增强浏览已关闭，已恢复原有 V1 推荐与分页。你的手账、偏好和反馈均未改变。', { exact: true }).waitFor()
    assert.equal(await page.locator('dialog').count(), 0)
    for (const route of resourceRoutes.splice(0)) await finish(route, { json: { status: 'success', results: [] } })
    resourceHold = false
    assert.equal(await page.getByRole('combobox', { name: '推荐风格', exact: true }).count(), 0)
    assert.equal(await cards(), 12)
    await page.getByRole('button', { name: '再翻一页 →', exact: true }).click()
    await page.getByRole('button', { name: '查看作品125详情', exact: true }).waitFor()
    assert.equal(await cards(), 24)
    await openFilter(); assert.equal(await page.getByRole('textbox', { name: '起始年份', exact: true }).count(), 0); await close()
    assert.equal(requests.filter(request => request.method === 'POST').length, 1)
    check('9C disabled flag automatically clears browse context and restores legacy V1 pagination without writes')
    await page.reload()
    await page.getByRole('button', { name: '再翻一页 →', exact: true }).waitFor()
    assert.equal(await page.getByRole('combobox', { name: '推荐风格', exact: true }).count(), 0)
    browseEnabled = true
    await page.getByRole('button', { name: '恢复默认', exact: true }).click()
    await page.getByRole('button', { name: '加载本批更多', exact: true }).waitFor()
    assert.equal(await cards(), 12)
    check('9C capability discovery handles cold rollback and explicit recovery to a new browse session')
  }
  if (phaseR1) {
    owned.clear()
    const reloadLayout = async (variant, width = 1440, height = 900) => {
      layoutCase = variant; await page.setViewportSize({ width, height }); await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`)
    }
    for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768]]) {
      await reloadLayout('ready', width, height)
      await page.locator('.rec-card--hero').waitFor()
      await settleMotion()
      const geometry = await page.locator('.rec-featured-layout').evaluate(node => {
        const hero = node.querySelector('.rec-card--hero').getBoundingClientRect(), note = node.querySelector('.rec-taste-note').getBoundingClientRect()
        const actions = node.querySelector('.rec-card-actions').getBoundingClientRect()
        return { hero: { top: hero.top, right: hero.right, bottom: hero.bottom }, note: { left: note.left, top: note.top }, actionsBottom: actions.bottom, height: innerHeight }
      })
      assert.ok(geometry.hero.top < 300, JSON.stringify(geometry))
      assert.ok(geometry.actionsBottom < height && geometry.hero.bottom < height, JSON.stringify(geometry))
      assert.ok(geometry.note.left > geometry.hero.right)
      assert.equal(await cards(), 12); assert.equal(await page.locator('.rec-grid .rec-card').count(), 11)
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      await page.screenshot({ path: path.join(artifacts, `r1-${width}.png`), fullPage: false })
    }
    check('R1 desktop first screen contains featured cover/title/actions beside taste note; unchanged hero plus 11 grid cards')
    count = listCalls().length
    const posts = requests.filter(request => request.method !== 'GET').length
    await page.getByText('浏览说明', { exact: true }).click()
    assert.ok(await page.getByText('加入想看不会覆盖已有评分、长评或观看状态。', { exact: true }).isVisible())
    await page.getByText('浏览说明', { exact: true }).click()
    await page.getByText('画像说明', { exact: true }).click()
    assert.ok(await page.getByText(/手账已有 77 条高分记录/).isVisible())
    await page.getByText('画像说明', { exact: true }).click()
    await page.getByRole('button', { name: '核对旧作品 →', exact: true }).click()
    await page.locator('dialog[open]').waitFor(); await close()
    assert.equal(listCalls().length, count)
    assert.equal(requests.filter(request => request.method !== 'GET').length, posts)
    check('R1 folded help and identity review remain accessible without recommendation refresh or business writes')
    for (const width of [768, 390, 320]) {
      await reloadLayout('ready', width, 844); await page.locator('.rec-card--hero').waitFor()
      await settleMotion()
      const hero = await page.locator('.rec-card--hero').boundingBox(), note = await page.locator('.rec-taste-note').boundingBox()
      assert.ok(note.y >= hero.y + hero.height)
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      await page.screenshot({ path: path.join(artifacts, `r1-${width}.png`), fullPage: false })
    }
    check('R1 tablet and 390/320px mobile put featured card before compact taste note without overflow')
    await reloadLayout('long'); await page.locator('.rec-card--hero').waitFor()
    await settleMotion()
    const title = await page.locator('.rec-card--hero .rec-card-title').evaluate(node => ({ height: node.clientHeight, line: parseFloat(getComputedStyle(node).lineHeight) }))
    assert.ok(title.height <= title.line * 2 + 2)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    check('R1 very long featured titles stay at two lines without horizontal overflow')
    await reloadLayout('cold'); await page.locator('.rec-featured-main .rec-cold-guide').waitFor()
    assert.equal(await cards(), 0)
    assert.ok(await page.getByRole('button', { name: '选择 3–5 个喜欢的题材', exact: true }).isVisible())
    await reloadLayout('explicit'); await page.locator('.rec-card--hero').waitFor()
    assert.ok(await page.locator('.rec-explicit-basis').isVisible())
    assert.equal(await page.locator('.rec-profile-guide').getAttribute('open'), null)
    await openFilter(); await page.getByRole('textbox', { name: '起始年份', exact: true }).fill('2099')
    await page.getByRole('button', { name: '应用筛选', exact: true }).click()
    await page.getByRole('button', { name: '调整筛选', exact: true }).waitFor()
    assert.equal(await page.locator('.rec-featured-main .rec-cold-guide').count(), 0)
    check('R1 cold start, explicit preference basis and filtered empty state stay distinct with original recovery actions')
    profileHold = true; await reloadLayout('ready')
    await page.getByLabel('正在加载推荐', { exact: true }).waitFor()
    assert.equal(await page.locator('.rec-taste-metrics').count(), 0)
    assert.equal(await page.locator('.rec-skeleton .rec-grid').count(), 0)
    profileHold = false; for (const done of profileHeld.splice(0)) done()
    await page.locator('.rec-card--hero').waitFor()
    await reloadLayout('error'); await page.getByRole('button', { name: '重新加载', exact: true }).waitFor()
    assert.equal(await page.locator('.rec-taste-metrics').count(), 0)
    assert.equal(await page.locator('.rec-cold-guide').count(), 0)
    assert.equal(await cards(), 0)
    layoutCase = 'ready'; await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await page.locator('.rec-card--hero').waitFor()
    check('R1 loading and failed profile never display fake zero counts or cold-start guidance; read retry recovers')
  }
  if (phaseR2) {
    layoutCase = 'surface'; await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`)
    const hero = page.locator('.rec-card--hero')
    await hero.locator('img').waitFor()
    await page.waitForFunction(() => document.querySelector('.rec-card--hero img')?.naturalWidth > 0)
    await settleMotion()
    const theme = await page.locator('.memphis-rec-container').evaluate(node => {
      const css = getComputedStyle(node), head = getComputedStyle(node.querySelector('.rec-masthead'))
      return { ink: css.getPropertyValue('--rec-ink').trim(), teal: css.getPropertyValue('--rec-teal').trim(),
        coral: head.backgroundColor, texture: css.backgroundImage, grid: head.backgroundImage }
    })
    assert.equal(theme.ink, '#111'); assert.equal(theme.teal, '#4ecdc4'); assert.equal(theme.coral, 'rgb(255, 107, 107)')
    assert.match(theme.texture, /radial-gradient/); assert.match(theme.grid, /linear-gradient/)
    const geometry = await hero.boundingBox(); assert.ok(geometry.y < 300 && geometry.y + geometry.height < 900)
    assert.equal(await hero.locator('.rec-card-actions > button').count(), 2)
    await page.screenshot({ path: path.join(artifacts, 'r2-featured-desktop.png'), fullPage: false })
    check('R2 journal coral grid, paper dots and color tokens render with hosted-image fixture and unchanged two primary actions')
    count = listCalls().length
    await hero.locator('.rec-evidence > summary').click()
    await hero.getByText('你对《合成来源作品》的评分：', { exact: false }).waitFor()
    assert.ok(await hero.getByText('9.1 分', { exact: true }).isVisible())
    assert.ok(await hero.getByText('Bangumi 评分', { exact: true }).isVisible())
    assert.equal(listCalls().length, count)
    await hero.locator('.rec-evidence > summary').click()
    await hero.getByRole('button', { name: /的更多操作/ }).click()
    const menu = page.getByRole('menuitem', { name: '不感兴趣', exact: true })
    assert.ok(await menu.isVisible()); assert.equal(await menu.evaluate(node => node === document.activeElement), true)
    await page.keyboard.press('Escape')
    assert.equal(await hero.locator('.rec-feedback-trigger').evaluate(node => node === document.activeElement), true)
    check('R2 evidence preserves distinct public/personal scores; feedback menu remains unclipped with Escape focus return')
    await page.locator('.rec-grid').scrollIntoViewIfNeeded()
    await page.waitForFunction(() => [...document.querySelectorAll('.rec-grid img')].some(img => img.naturalWidth > 0))
    await page.getByRole('button', { name: '查看放学后的星空详情', exact: true }).first().scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(artifacts, 'r2-grid-desktop.png'), fullPage: false })
    assert.ok(await page.locator('.rec-cover-placeholder').count() >= 1)
    check('R2 regular cards retain safe missing-image fallback alongside loaded posters and readable evidence')
    await hero.getByRole('button', { name: '查看海风与放课后的合奏详情', exact: true }).click()
    const dialog = page.locator('dialog[open]')
    await dialog.locator('.rec-detail-content').waitFor()
    assert.equal(await dialog.evaluate(node => node.matches(':modal')), true)
    assert.equal(await dialog.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(255, 255, 255)')
    await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
    await page.getByText('合成资源失败', { exact: false }).waitFor()
    await page.keyboard.press('Escape')
    assert.equal(await dialog.count(), 1)
    assert.equal(await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).evaluate(node => node === document.activeElement), true)
    await dialog.locator('.rec-dialog-main').evaluate(node => { node.scrollTop = 0 })
    await page.screenshot({ path: path.join(artifacts, 'r2-detail-desktop.png'), fullPage: false })
    await close()
    check('R2 white native detail dialog preserves resource-local failure, Escape priority and trigger focus')
    for (const width of [768, 390, 320]) {
      await page.setViewportSize({ width, height: 844 }); await page.evaluate(() => scrollTo(0, 0))
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      const first = page.locator('.rec-grid .rec-card').first()
      const size = await first.locator('.rec-feedback-trigger').boundingBox()
      assert.ok(size.width >= 44 && size.height >= 44)
      await page.screenshot({ path: path.join(artifacts, `r2-featured-${width}.png`), fullPage: false })
      await first.scrollIntoViewIfNeeded()
      await page.screenshot({ path: path.join(artifacts, `r2-grid-${width}.png`), fullPage: false })
    }
    check('R2 768/390/320px hosted covers and grid controls remain responsive with 44px touch targets')
    await page.setViewportSize({ width: 390, height: 844 })
    await hero.getByRole('button', { name: '查看海风与放课后的合奏详情', exact: true }).click()
    await dialog.locator('.rec-detail-content').waitFor()
    await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).click()
    await page.getByRole('button', { name: '返回详情', exact: false }).waitFor()
    // Wait for the existing slide transition to settle, not merely `visible`.
    await page.waitForFunction(() => {
      const workspace = document.querySelector('dialog[open] .rec-dialog-workspace')?.getBoundingClientRect()
      const resource = document.querySelector('dialog[open] .rec-dialog-resource')?.getBoundingClientRect()
      return workspace && resource && Math.abs(workspace.left - resource.left) < 1 && Math.abs(workspace.width - resource.width) < 1
    })
    assert.equal(await dialog.locator('.rec-dialog-main').getAttribute('inert'), '')
    assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth))
    await page.screenshot({ path: path.join(artifacts, 'r2-resource-mobile.png'), fullPage: false })
    await page.keyboard.press('Escape')
    assert.equal(await dialog.count(), 1)
    assert.equal(await page.getByRole('button', { name: '🧲 寻找资源', exact: true }).evaluate(node => node === document.activeElement), true)
    await page.keyboard.press('Escape'); await page.locator('dialog').waitFor({ state: 'detached' })
    check('R2 mobile resource panel keeps native modal/inert boundaries and Escape closes resource before detail')
    const isolated = await page.evaluate(() => {
      const probe = document.createElement('button'); probe.className = 'rec-button'; document.body.append(probe)
      const css = getComputedStyle(probe), result = { shadow: css.boxShadow, bg: css.backgroundColor }; probe.remove(); return result
    })
    assert.equal(isolated.shadow, 'none'); assert.notEqual(isolated.bg, 'rgb(255, 230, 109)')
    check('R2 namespaced styling does not decorate same-named controls outside the recommendation page')
  }
  if (phaseR3) {
    const writes = requests.filter(request => request.method !== 'GET').length
    layoutCase = 'ready'; await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html`)
    await page.waitForFunction(() => window.browseQA?.motion().some(target => target.active && target.connected))
    await page.locator('[data-rec-motion-id="101"]').waitFor(); await settleMotion()
    const cleanStyles = () => page.locator('.rec-masthead, .rec-taste-note, .rec-card').evaluateAll(nodes =>
      nodes.every(node => !node.style.transform && !node.style.opacity && !node.style.willChange))
    assert.equal(await cleanStyles(), true)
    await page.screenshot({ path: path.join(artifacts, 'r3-settled-desktop.png'), fullPage: false })
    check('R3 real GSAP plays in StrictMode and restores original inline styles/will-change after completion')

    await page.emulateMedia({ reducedMotion: 'reduce' }); await page.reload()
    await page.locator('[data-rec-motion-id="101"]').waitFor()
    assert.deepEqual(await page.evaluate(() => window.browseQA.motion()), [])
    assert.equal(await cleanStyles(), true)
    await openFilter()
    assert.equal(await page.locator('dialog').evaluate(node => getComputedStyle(node).transform), 'none')
    await close(); const reads = listCalls().length
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    assert.deepEqual(await page.evaluate(() => window.browseQA.motion()), [])
    assert.equal(listCalls().length, reads)
    check('R3 initial reduced motion is immediate/static; opting into motion does not replay cards or refetch data')

    await page.reload()
    await page.waitForFunction(() => window.browseQA?.motion().some(target => target.active))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.locator('[data-rec-motion-id="101"]').waitFor(); await settleMotion()
    assert.equal(await cleanStyles(), true)
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.screenshot({ path: path.join(artifacts, 'r3-reduced-motion.png'), fullPage: false })
    check('R3 changing reduced-motion during entry cancels motion and keeps every card visible without residual transforms')

    await page.evaluate(() => window.browseQA.beginMotionRecording())
    await page.getByRole('button', { name: '加载本批更多', exact: true }).click()
    await page.locator('[data-rec-motion-id="124"]').waitFor()
    await page.locator('[data-rec-motion-id="113"]').evaluate(node => node.scrollIntoView({ block: 'center', behavior: 'instant' }))
    await settleMotion()
    const appendedFrames = (await page.evaluate(() => window.browseQA.endMotionRecording())).filter(target => target.id)
    assert.ok(appendedFrames.length > 0 && appendedFrames.every(target => Number(target.id) >= 113))
    assert.equal(await cards(), 24)
    await page.getByRole('button', { name: /换一批，看看/ }).click()
    await page.locator('[data-rec-motion-id="125"]').waitFor(); await settleMotion()
    assert.equal(await cards(), 12)
    assert.equal(await page.locator('#rec-results').evaluate(node => node === document.activeElement), true)
    check('R3 append animates only new cards, while next batch replaces content and keeps result-region focus')

    hold = true
    await page.getByRole('button', { name: '熟悉的心动', exact: true }).evaluate(node => node.click())
    await waitHeld()
    await page.getByRole('button', { name: '换一种口味', exact: true }).evaluate(node => node.click())
    await restore()
    await page.locator('[data-rec-motion-id="501"]').waitFor(); await settleMotion()
    const currentIds = await page.locator('[data-rec-motion-id]').evaluateAll(nodes => nodes.map(node => Number(node.dataset.recMotionId)))
    assert.ok(currentIds.every(id => id >= 501 && id <= 512))
    assert.deepEqual(await page.evaluate(() => window.browseQA.motion()), [])
    check('R3 late requests and old animation/observer callbacks cannot contaminate a changed category')

    await page.getByRole('button', { name: '查看作品501详情', exact: true }).evaluate(node => { node.focus(); node.click() })
    await page.locator('dialog[open]').waitFor()
    await page.keyboard.press('Escape'); await page.locator('dialog').waitFor({ state: 'detached' })
    await settleMotion()
    assert.equal(await page.getByRole('button', { name: '查看作品501详情', exact: true }).evaluate(node => node === document.activeElement), true)
    assert.equal(await page.evaluate(() => document.body.style.overflow), '')
    assert.ok((await page.evaluate(() => window.browseQA.motion())).every(target => target.connected))
    check('R3 closing during dialog entry is immediate, cancels local motion and restores focus/scroll lock')

    await page.reload()
    await page.waitForFunction(() => window.browseQA?.motion().some(target => target.active))
    await page.evaluate(() => { window.browseQA.startOutsideMotion(); window.browseQA.navigate('/dashboard') })
    await page.getByRole('heading', { name: '9B 隔离手账', exact: true }).waitFor()
    const remaining = await page.evaluate(() => window.browseQA.motion())
    assert.ok(remaining.length > 0 && remaining.every(target => target.className === 'qa-motion-outside' && target.connected))
    await page.evaluate(() => { window.browseQA.clearOutsideMotion(); window.browseQA.navigate('/recommendations') })
    await page.locator('[data-rec-motion-id="101"]').waitFor(); await settleMotion()
    assert.equal(await cleanStyles(), true)
    check('R3 unmount removes only recommendation animations; unrelated GSAP work survives and re-entry remains clean')

    await page.addInitScript(() => { if (location.search.includes('noio')) window.IntersectionObserver = undefined })
    await page.goto(`${origin}/tests/fixtures/recommendationBrowseHarness.html?noio=1`)
    await page.locator('[data-rec-motion-id="101"]').waitFor(); await settleMotion()
    assert.equal(await cleanStyles(), true); assert.equal(await cards(), 12)
    assert.equal(requests.filter(request => request.method !== 'GET').length, writes)
    check('R3 missing IntersectionObserver degrades to visible cards; motion adds no business writes')
  }
  assert.deepEqual(errors, []); assert.deepEqual(external, [])
  report.result = 'passed'
} catch (error) { report.result = 'failed'; report.failure = error.message; throw error }
finally {
  await writeFile(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close(); await server.close()
}
