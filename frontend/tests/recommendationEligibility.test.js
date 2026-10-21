import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { createEligibilityController, eligibilityCopy, validateEligibility } from '../src/utils/recommendationEligibility.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'

const value = (changes = {}) => ({ contract_version: 'recommendations.eligibility.manual.m1.v1',
  policy_version: 'eligibility.manual.closed.v1', effective_status: 'not_requested', authorization_source: 'none',
  manual_authorization_valid: false, eligibility_version: 0, age_assurance_status: 'not_performed', can_apply: true,
  adult_access_allowed: false, serving_integration: false, effective_scope: 'safe', decision_message: '',
  valid_until: null, eligibility_revision: 'a'.repeat(64), ...changes })
const pending = () => value({ effective_status: 'pending', authorization_source: 'manual', eligibility_version: 1, can_apply: false })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { for (let n = 0; n < 8; n++) await Promise.resolve() }

test('M1 client only reads own state or submits the exact explicit application', async () => {
  const calls = [], signal = new AbortController().signal
  const client = createRecommendationClient({ get: async (...args) => { calls.push(['GET', ...args]); return { data: value() } },
    post: async (...args) => { calls.push(['POST', ...args]); return { data: { eligibility: pending() } } } })
  await client.eligibility(signal)
  const body = { expected_version: 0, operation_id: 'isolated-id', confirmed: true }
  await client.applyEligibility(body, signal)
  assert.deepEqual(calls, [['GET', 'recommendations/eligibility/', { signal }],
    ['POST', 'recommendations/eligibility/apply/', body, { signal }]])
})

test('M1 incompatible, open serving or false verification contracts fail closed', () => {
  assert.equal(validateEligibility(value()).can_apply, true)
  for (const change of [{ contract_version: 'old' }, { age_assurance_status: 'verified' }, { adult_access_allowed: true },
    { serving_integration: true }, { effective_scope: 'include_adult' }, { eligibility_version: true },
    { manual_authorization_valid: true }, { valid_until: 'invalid' }, { can_apply: undefined }]) {
    assert.throws(() => validateEligibility(value(change)))
  }
})

test('M1 unconfirmed, read-only open/close and dispose do not submit applications', async () => {
  let writes = 0
  const controller = createEligibilityController({ eligibility: async () => value(), applyEligibility() { writes++ } })
  await controller.load(); await controller.submit()
  controller.confirm(true); controller.dispose(); await controller.submit()
  assert.equal(writes, 0)
})

test('M1 fast repeated clicks share one request, version and operation UUID', async () => {
  const task = deferred(), calls = []; let changed = 0
  const controller = createEligibilityController({ eligibility: async () => value(), applyEligibility: (body, signal) => {
    calls.push({ body, signal }); return task.promise
  } }, () => changed++, () => 'fixed-uuid')
  await controller.load(); controller.confirm(true)
  const a = controller.submit(), b = controller.submit()
  assert.equal(a, b); await flush(); assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].body, { expected_version: 0, operation_id: 'fixed-uuid', confirmed: true })
  task.resolve({ eligibility: pending() }); await a
  assert.equal(controller.getSnapshot().data.effective_status, 'pending')
  assert.equal(changed, 1); await controller.submit(); assert.equal(calls.length, 1)
})

test('M1 uncertain timeout retries the ORIGINAL request; reload only performs GET', async () => {
  const bodies = []; let count = 0, reads = 0
  const controller = createEligibilityController({ eligibility: async () => { reads++; return count ? pending() : value() },
    applyEligibility: async body => { bodies.push(body); if (++count === 1) throw new Error('timeout'); return { eligibility: pending() } } },
  () => {}, () => 'same-uuid')
  await controller.load(); controller.confirm(true); await controller.submit()
  assert.equal(controller.getSnapshot().status, 'uncertain')
  await controller.submit(); assert.deepEqual(bodies[0], bodies[1])
  await controller.load(); assert.equal(bodies.length, 2); assert.equal(reads, 2)
})

test('M1 409 conflict requires explicit reread, never overwrites another decision', async () => {
  let writes = 0
  const controller = createEligibilityController({ eligibility: async () => writes ? pending() : value(), applyEligibility: async () => {
    writes++; throw { response: { status: 409 } }
  } }, () => {}, () => 'conflict-uuid')
  await controller.load(); controller.confirm(true); await controller.submit()
  assert.equal(controller.getSnapshot().status, 'conflict')
  await controller.submit(); assert.equal(writes, 1)
  await controller.load(); assert.equal(controller.getSnapshot().data.effective_status, 'pending')
})

test('M1 canceled GET/POST successes, errors and finally cannot cross close/account sessions', async () => {
  const old = deferred(), current = deferred(); let count = 0, changed = 0, oldSignal
  const controller = createEligibilityController({ eligibility: signal => { oldSignal ||= signal; return (++count === 1 ? old : current).promise } })
  const first = controller.load(); await flush(); controller.dispose(); controller.activate()
  const second = controller.load(); await flush()
  assert.equal(oldSignal.aborted, true)
  old.resolve(value()); await first; assert.equal(controller.getSnapshot().status, 'loading')
  current.resolve(pending()); await second; assert.equal(controller.getSnapshot().data.can_apply, false)
  for (const rejects of [false, true]) {
    const task = deferred()
    const write = createEligibilityController({ eligibility: async () => value(), applyEligibility: () => task.promise }, () => changed++, () => 'old-write')
    await write.load(); write.confirm(true); const promise = write.submit(); await flush(); write.dispose()
    if (rejects) task.reject(new Error('old-error')); else task.resolve({ eligibility: pending() })
    await promise
    assert.equal(changed, 0)
  }
})

test('M1 read failure is not displayed as not-requested and never writes', async () => {
  const controller = createEligibilityController({ eligibility: async () => { throw new Error('offline') } })
  await controller.load()
  assert.equal(controller.getSnapshot().status, 'error')
  assert.equal(controller.getSnapshot().data, null)
})

test('M1 server-confirmed application is not misreported as failed if local notification throws', async () => {
  let writes = 0
  const controller = createEligibilityController({ eligibility: async () => value(), applyEligibility: async () => {
    writes++; return { eligibility: pending() }
  } }, () => { throw new Error('local refresh notification failed') }, () => 'confirmed-uuid')
  await controller.load(); controller.confirm(true); await controller.submit()
  assert.equal(controller.getSnapshot().status, 'ready')
  assert.equal(controller.getSnapshot().data.effective_status, 'pending')
  assert.match(controller.getSnapshot().error, /申请状态已保存/)
  await controller.submit(); assert.equal(writes, 1)
})

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationAccessApplicationView: View } = await server.ssrLoadModule('/src/components/recommendations/RecommendationAccessApplication.jsx')
const { RecommendationFilterPanel: FilterPanel } = await server.ssrLoadModule('/src/components/recommendations/RecommendationBrowseControls.jsx')
const render = (state) => renderToStaticMarkup(React.createElement(View, { state }))

test('M1 application view covers pending, approved, rejected, expired and unavailable without an NSFW switch', () => {
  for (const data of [value(), pending(), value({ effective_status: 'active', authorization_source: 'manual', can_apply: false,
    manual_authorization_valid: true, valid_until: '2026-12-01T00:00:00Z' }),
    value({ effective_status: 'rejected', decision_message: '<script>not executable</script>' }),
    value({ effective_status: 'expired' }), value({ authorization_source: 'legacy' })]) {
    const html = render({ status: 'ready', data, confirmed: false, error: '' })
    assert.match(html, /NSFW 推荐暂未开放/)
    assert.doesNotMatch(html, /role="switch"|<script>|已验证成年/)
    if (!data.can_apply) assert.doesNotMatch(html, /申请开启<\/button>/)
    assert.ok(eligibilityCopy(data))
  }
  const error = render({ status: 'error', data: null, error: '无法读取申请状态' })
  assert.match(error, /重新读取申请状态/); assert.doesNotMatch(error, /尚未申请受限内容访问/)
})

test('M1 application controls never submit parent filter form or mutate filters', () => {
  const html = render({ status: 'ready', data: value(), confirmed: false, error: '' })
  assert.doesNotMatch(html, /<form|type="submit"/)
  assert.match(html, /type="button"[^>]*disabled=""[^>]*>申请开启/)
  const panel = readFileSync(new URL('../src/components/recommendations/RecommendationBrowseControls.jsx', import.meta.url), 'utf8')
  assert.match(panel, /RecommendationAccessApplication client=\{client\}/)
  const controller = readFileSync(new URL('../src/utils/recommendationEligibility.js', import.meta.url), 'utf8')
  assert.doesNotMatch(controller, /content_scope\s*:|setInterval|localStorage/)
})

test('filter uses a separate scroll body and always-visible footer, including validation errors', () => {
  const html = renderToStaticMarkup(React.createElement(FilterPanel, { initialFilters: { type: 'all', minScore: '0', hideRecorded: true, yearFrom: '', yearTo: '' } }))
  assert.match(html, /class="rec-filter-form"/)
  assert.match(html, /class="rec-filter-scroll"/)
  assert.match(html, /<footer class="rec-filter-footer">[\s\S]*type="submit"[^>]*>应用筛选/)
  assert.equal((html.match(/type="submit"/g) || []).length, 1)
  const css = readFileSync(new URL('../src/styles/recommendations.css', import.meta.url), 'utf8')
  assert.match(css, /\.rec-filter-scroll\s*\{[^}]*overflow-y: auto/)
  assert.match(css, /\.rec-filter-footer\s*\{[^}]*flex-shrink: 0/)
  assert.match(css, /\.rec-dialog--filters\[open\][^}]*overflow: hidden/)
})
