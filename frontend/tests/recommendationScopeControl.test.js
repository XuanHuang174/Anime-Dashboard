import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { createScopeControlReader, readScopeControl, withAvailableScope } from '../src/utils/recommendationScopeControl.js'
import { controlledScope, checkScopeResponse } from '../src/utils/recommendationContentScope.js'

const value = (open = false) => ({ contract_version: 'recommendations.eligibility.manual.m2.v1',
  policy_version: 'eligibility.manual.m2.v1', effective_status: open ? 'active' : 'not_requested',
  authorization_source: open ? 'manual' : 'none', manual_authorization_valid: open,
  eligibility_version: open ? 2 : 0, age_assurance_status: 'not_performed', can_apply: !open,
  adult_access_allowed: open, serving_integration: open, effective_scope: 'safe', decision_message: '',
  valid_until: open ? '2099-01-01T00:00:00Z' : null, eligibility_revision: 'a'.repeat(64) })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }

test('scope presentation supports old servers without inventing permissions', () => {
  assert.equal(readScopeControl(value()).available, false)
  const data = value(true), control = readScopeControl(data)
  assert.equal(control.available, true)
  assert.deepEqual(readScopeControl({ ...data, scope_control: control }), control)
  assert.throws(() => readScopeControl({ ...data, scope_control: null }))
  for (const patch of [{ available: false }, { available: 'true' }, { revision: 'b'.repeat(64) },
    { default_scope: 'include_adult' }, { selection_mode: 'persistent' }, { reason: 'bypass' }, { contract_version: 'future' }]) {
    assert.throws(() => readScopeControl({ ...data, scope_control: { ...control, ...patch } }))
  }
})

test('general-adult session contract needs no manual grant but cannot invent serving or age verification', () => {
  const data = { ...value(), contract_version: 'recommendations.eligibility.session.v1',
    policy_version: 'eligibility.session.v1', can_apply: false, serving_integration: true, adult_access_allowed: true }
  assert.equal(readScopeControl(data).available, true)
  for (const patch of [{ can_apply: true }, { serving_integration: false }, { age_assurance_status: 'verified' },
    { effective_status: 'account_inactive' }, { policy_version: 'eligibility.manual.m2.v1' }]) {
    assert.throws(() => readScopeControl({ ...data, ...patch }))
  }
})

test('general-adult evidence accepts zero grant version only with the matching content policy', () => {
  const scope = { content_scope: 'include_adult', content_scope_contract_version: 'recommendations.content-scope.n2-2.v1',
    content_scope_policy_version: 'content-scope.general-adult.v1', content_release_policy_version: 'release-review.general-adult.v2',
    eligibility_version: 0, content_review_version: 1, serving_control_version: 1, release_revision: 'a'.repeat(64),
    scope_revision: 'b'.repeat(64), scope_valid_until: '2099-01-01T00:00:00Z' }
  assert.equal(controlledScope(scope), true)
  assert.equal(checkScopeResponse({ content_scope_context: scope }, 'include_adult').content_scope_context, scope)
  for (const patch of [{ content_release_policy_version: 'release-review.ledger.closed.v1' },
    { content_scope_policy_version: 'content-scope.controlled.m2.v1' }, { content_review_version: 0 },
    { scope_valid_until: '2000-01-01T00:00:00Z' }]) assert.equal(controlledScope({ ...scope, ...patch }), false)
  assert.throws(() => checkScopeResponse({ results: [{ content_rating: 'adult' }] }, 'safe'))
})

test('unavailable, loading and failed availability cannot apply a stale true draft', () => {
  const draft = { type: 'TV', includeAdult: true }
  for (const availability of [false, null, undefined, 'true', 1]) {
    assert.deepEqual(withAvailableScope(draft, availability), { type: 'TV', includeAdult: false })
  }
  assert.equal(withAvailableScope(draft, true).includeAdult, true)
  assert.equal(withAvailableScope({ includeAdult: false }, true).includeAdult, false)
  assert.equal(draft.includeAdult, true)
})

test('scope reader merges clicks and never submits an application', async () => {
  const task = deferred(); let reads = 0, writes = 0
  const reader = createScopeControlReader({ eligibility() { reads++; return task.promise }, applyEligibility() { writes++ } })
  const a = reader.load(), b = reader.load(); assert.equal(a, b)
  await flush(); assert.equal(reads, 1)
  task.resolve(value(true)); await a
  assert.equal(reader.getSnapshot().data.available, true)
  reader.dispose(); await reader.load()
  assert.equal(writes, 0); assert.equal(reads, 1)
  assert.equal(reader.submit, undefined)
})

test('closed session ignores late success and error and aborts the old GET', async () => {
  for (const rejects of [false, true]) {
    const old = deferred(), next = deferred(); let count = 0, oldSignal
    const reader = createScopeControlReader({ eligibility(signal) { oldSignal ||= signal; return (++count === 1 ? old : next).promise } })
    const a = reader.load(); await flush(); reader.dispose(); reader.activate()
    const b = reader.load(); await flush(); assert.equal(oldSignal.aborted, true)
    if (rejects) old.reject(new Error('late')); else old.resolve(value(true))
    await a; assert.equal(reader.getSnapshot().status, 'loading')
    next.resolve(value()); await b; assert.equal(reader.getSnapshot().data.available, false)
  }
})

test('failed or incompatible reads stay closed; retry is GET-only', async () => {
  let reads = 0
  const reader = createScopeControlReader({ eligibility: async () => ++reads === 1 ? {} : value() })
  await reader.load(); assert.equal(reader.getSnapshot().status, 'error'); assert.equal(reader.getSnapshot().data, null)
  await reader.load(); assert.equal(reader.getSnapshot().status, 'ready'); assert.equal(reads, 2)
})

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationContentScopeToggleView: View } = await server.ssrLoadModule('/src/components/recommendations/RecommendationContentScopeToggle.jsx')
const render = (state, includeAdult = false) => renderToStaticMarkup(React.createElement(View, { state, includeAdult }))

test('compact switch has no application, approval, age claim or parent-submit action', () => {
  const html = render({ status: 'ready', data: readScopeControl(value(true)) })
  assert.match(html, /role="switch"/); assert.match(html, /包含 NSFW 作品/)
  assert.match(html, /仅对本次浏览生效/)
  assert.doesNotMatch(html, /checked=""|申请|审批|年龄核验|type="submit"|<form/)
  assert.match(render({ status: 'ready', data: readScopeControl(value(true)) }, true), /checked=""/)
})

test('loading, unavailable and error all show disabled unchecked switches with local retry', () => {
  for (const state of [{ status: 'loading', data: null }, { status: 'ready', data: readScopeControl(value()) }, { status: 'error', data: null }]) {
    const html = render(state, true)
    assert.match(html, /role="switch"[^>]*disabled=""/); assert.doesNotMatch(html, /checked=""/)
    assert.match(html, /role="status"/)
    if (state.status === 'error') assert.match(html, /重试/)
  }
})

test('active filter wiring uses read-only control and retains apply-only draft lifecycle', () => {
  const panel = readFileSync(new URL('../src/components/recommendations/RecommendationBrowseControls.jsx', import.meta.url), 'utf8')
  assert.match(panel, /withAvailableScope\(draft, scopeAvailable\)/)
  assert.match(panel, /RecommendationContentScopeToggle client=/)
  assert.doesNotMatch(panel, /RecommendationAccessApplication|applyEligibility|localStorage/)
  const control = readFileSync(new URL('../src/components/recommendations/RecommendationContentScopeToggle.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(control, /applyEligibility|onApply|localStorage|\.post\(/)
})
