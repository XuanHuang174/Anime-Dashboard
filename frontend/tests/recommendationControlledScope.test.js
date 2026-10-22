import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { controlledScope, checkScopeResponse } from '../src/utils/recommendationContentScope.js'
import { mapRecommendationItem, validateIdentityReview } from '../src/utils/recommendations.js'
import { validateWantResult } from '../src/utils/recommendationWant.js'
import { validateEligibility } from '../src/utils/recommendationEligibility.js'
import { filterChips, DEFAULT_BROWSE_FILTERS } from '../src/utils/recommendationBrowse.js'
const scope = changes => ({ content_scope: 'include_adult',
  content_scope_contract_version: 'recommendations.content-scope.n2-2.v1',
  content_scope_policy_version: 'content-scope.controlled.m2.v1', eligibility_version: 2, content_review_version: 1,
  serving_control_version: 1, release_revision: 'a'.repeat(64), scope_revision: 'b'.repeat(64),
  scope_valid_until: new Date(Date.now() + 3600000).toISOString(), ...changes })
const envelope = (changes = {}) => ({ content_scope_context: scope(), ...changes })
const row = changes => ({ bangumi_id: 12, title_zh: '合成作品', title_jp: '', score: 7.5, tags: ['音乐'],
  content_rating: 'adult', cover_url: 'https://media.xh-anime.com/covers/12.jpg', evidence_context: scope(), ...changes })

test('M2 scope is explicit, expiring and fails closed for incomplete/old contracts', () => {
  assert.ok(controlledScope(scope()))
  for (const data of [null, scope({ content_scope: 'safe' }), scope({ scope_valid_until: new Date(0).toISOString() }),
    scope({ serving_control_version: 0 }), scope({ eligibility_version: 0 }), scope({ content_review_version: 0 }),
    scope({ content_scope_policy_version: 'old' })]) assert.ok(!controlledScope(data))
  assert.throws(() => checkScopeResponse(envelope(), 'safe'))
  assert.throws(() => checkScopeResponse({}, 'include_adult'))
  assert.throws(() => checkScopeResponse({ results: [row()] }, 'safe'))
})

test('safe default client sends no new query; explicit client scopes every content route including cursors/writes', async () => {
  const calls = [], transport = {
    get: async (path, config) => { calls.push([path, config]); return { data: envelope() } },
    post: async (path, body, config) => { calls.push([path, config, body]); return { data: envelope() } },
    patch: async (path, body, config) => { calls.push([path, config, body]); return { data: envelope() } },
  }
  const client = createRecommendationClient(transport).forScope('include_adult')
  await client.browse({ cursor: 'synthetic-cursor' }); await client.detail(12); await client.want(12)
  await client.hiddenFeedback({ page: 2 }); await client.context(); await client.hideFeedback({})
  await client.undoFeedback({}); await client.identities({}); await client.confirmIdentities({})
  assert.ok(calls.every(([, config]) => config.params.content_scope === 'include_adult'))
  assert.deepEqual(calls[0][1].params, { cursor: 'synthetic-cursor', content_scope: 'include_adult' })
  calls.length = 0; await client.profile(); await client.preferences(); await client.eligibility()
  assert.ok(calls.every(([, config]) => !config.params))
  calls.length = 0
  const safe = createRecommendationClient({ get: async (path, config) => { calls.push([path, config]); return { data: {} } } })
  await safe.detail(12); assert.deepEqual(calls[0][1], { signal: undefined })
})

test('revocation, review change, wrong-scope response and 409 notify once; aborted old errors never reset new scope', async () => {
  let changed = 0, response = envelope(), error = null
  const client = createRecommendationClient({ get: async () => { if (error) throw error; return { data: response } } })
    .forScope('include_adult', () => changed++)
  await client.context(); response = envelope({ content_scope_context: scope({ scope_revision: 'c'.repeat(64) }) })
  await assert.rejects(client.context()); assert.equal(changed, 1)
  error = { response: { status: 409 } }; await assert.rejects(client.detail(12)); assert.equal(changed, 2)
  const abort = new AbortController(); abort.abort(); await assert.rejects(client.detail(12, abort.signal)); assert.equal(changed, 2)
  error = null; response = { content_scope_context: { content_scope: 'safe' } }
  await assert.rejects(client.detail(12)); assert.equal(changed, 3)
})

test('adult covers are forced neutral and adult want acknowledgements require scoped contract', () => {
  const mapped = mapRecommendationItem(row()); assert.equal(mapped.coverUrl, ''); assert.equal(mapped.contentRating, 'adult')
  assert.throws(() => mapRecommendationItem(row({ evidence_context: { content_scope: 'safe' } })))
  const want = envelope({ contract_version: 'recommendations.want.v1', content_rating: 'adult', bangumi_id: 12,
    record_id: 20, anime_id: 30, created: false, outcome: 'existing', watch_status: 'on_hold' })
  assert.equal(validateWantResult(want, 12), want)
  assert.throws(() => validateWantResult({ ...want, content_scope_context: null }, 12))
})

test('M2 manual approval never equals adulthood; serving flag alone does not enable selection', () => {
  const data = { contract_version: 'recommendations.eligibility.manual.m2.v1', policy_version: 'eligibility.manual.m2.v1',
    effective_status: 'active', authorization_source: 'manual', can_apply: false, manual_authorization_valid: true,
    eligibility_version: 2, age_assurance_status: 'not_performed', adult_access_allowed: true,
    serving_integration: true, effective_scope: 'safe', decision_message: '', eligibility_revision: 'a'.repeat(64),
    valid_until: new Date(Date.now() + 3600000).toISOString() }
  assert.equal(validateEligibility(data), data)
  assert.throws(() => validateEligibility({ ...data, manual_authorization_valid: false }))
  assert.throws(() => validateEligibility({ ...data, age_assurance_status: 'verified' }))
})

test('NSFW is a removable session filter, never a persistent taste preference', () => {
  assert.ok(!DEFAULT_BROWSE_FILTERS.includeAdult)
  assert.equal(filterChips({ ...DEFAULT_BROWSE_FILTERS, includeAdult: true })[0].id, 'includeAdult')
  const page = readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8')
  assert.match(page, /state\.cancelBrowse\(\); setPanel\(null\)/)
  assert.match(page, /includeAdult: false/); assert.match(page, /scope_valid_until/)
  assert.match(page, /watchRecommendationScope/)
  assert.doesNotMatch(page, /localStorage\.setItem\([^\n]*includeAdult/)
  const hook = readFileSync(new URL('../src/hooks/useRecommendations.js', import.meta.url), 'utf8')
  assert.match(hook, /!!filters.includeAdult/)
})

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationAccessApplicationView: Access } = await server.ssrLoadModule('/src/components/recommendations/RecommendationAccessApplication.jsx')
const { RecommendationFilterPanel: Filter } = await server.ssrLoadModule('/src/components/recommendations/RecommendationBrowseControls.jsx')
const { default: AdminPanel } = await server.ssrLoadModule('/src/components/AdminRecommendationAccessPanel.jsx')

test('approved/open scope offers an unchecked draft checkbox, never auto-enables or parent submits', () => {
  const html = renderToStaticMarkup(React.createElement(Access, { state: { status: 'ready', confirmed: false,
    data: { effective_status: 'active', manual_authorization_valid: true, adult_access_allowed: true } } }))
  assert.match(html, /包含 NSFW 标记作品（本次浏览）/); assert.doesNotMatch(html, /checked=""|type="submit"/)
  const closed = renderToStaticMarkup(React.createElement(Access, { state: { status: 'ready', confirmed: false,
    data: { effective_status: 'active', manual_authorization_valid: true, adult_access_allowed: false } } }))
  assert.doesNotMatch(closed, /包含 NSFW 标记作品/)
  const filter = renderToStaticMarkup(React.createElement(Filter, { initialFilters: DEFAULT_BROWSE_FILTERS }))
  assert.match(filter, /rec-filter-footer/); assert.equal((filter.match(/type="submit"/g) || []).length, 1)
})

test('module 3 reuses signed M1 approval and independent audited switch, no journal read or auto-approval', () => {
  const html = renderToStaticMarkup(React.createElement(AdminPanel))
  assert.match(html, /受限内容管理 · 模块 3/); assert.match(html, /不是年龄核验/)
  const source = readFileSync(new URL('../src/components/AdminRecommendationAccessPanel.jsx', import.meta.url), 'utf8')
  const contract = readFileSync(new URL('../src/utils/recommendationAccessAdmin.js', import.meta.url), 'utf8')
  assert.match(source, /accessApprovalPayload/); assert.match(contract, /ticket: selected.ticket/)
  assert.match(source, /operation_id: crypto.randomUUID/)
  assert.match(source, /重试确认原操作/); assert.doesNotMatch(source, /records\/|journal\/|personal_score|review:/)
  const menu = readFileSync(new URL('../src/pages/AdminDashboard.jsx', import.meta.url), 'utf8')
  assert.match(menu, /currentModule\?\.key === 'module-3'/)
})
