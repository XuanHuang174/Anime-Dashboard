import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { accessApprovalPayload, accessAdminFailure, isSelfApproval } from '../src/utils/recommendationAccessAdmin.js'

const selected = { ticket: 'synthetic-signed-ticket', is_self: true, self_approval_allowed: true }
const input = { confirmed: true, selfApprovalConfirmed: true, reason: '测试本人授权', days: '3' }

test('self approval requires BOTH independent explicit confirmations', () => {
  for (const value of [undefined, false, 'true', 1]) {
    assert.throws(() => accessApprovalPayload(selected, 'approve', { ...input, confirmed: value }))
    assert.throws(() => accessApprovalPayload(selected, 'approve', { ...input, selfApprovalConfirmed: value }))
  }
  assert.equal(accessApprovalPayload(selected, 'approve', input).self_approval_confirmed, true)
})

test('self capability is not inferred from username or role-looking client data', () => {
  assert.throws(() => accessApprovalPayload({ ...selected, self_approval_allowed: false }, 'approve', input))
  assert.equal(isSelfApproval({ username: 'XHcode' }, 'approve'), false)
  assert.equal(isSelfApproval(selected, 'revoke'), false)
})

test('other-account approval keeps the original confirmation body; no self flag is forged', () => {
  const body = accessApprovalPayload({ ...selected, is_self: false }, 'approve', { ...input, selfApprovalConfirmed: false })
  assert.equal(body.confirmed, true); assert.equal(body.ticket, selected.ticket)
  assert.ok(!Object.hasOwn(body, 'self_approval_confirmed'))
  assert.equal(accessApprovalPayload(selected, 'revoke', { ...input, selfApprovalConfirmed: false }).action, 'revoke')
})

test('reason and duration validated before any transport call can be constructed', () => {
  for (const days of ['', '0', '31', '1.5', 'bad']) assert.throws(() => accessApprovalPayload(selected, 'approve', { ...input, days }))
  for (const reason of ['', '  ', 'a'.repeat(201)]) assert.throws(() => accessApprovalPayload(selected, 'approve', { ...input, reason }))
  assert.throws(() => accessApprovalPayload(null, 'approve', input))
})

test('known failures have specific safe copy, never server stack or arbitrary HTML', () => {
  assert.match(accessAdminFailure({ response: { status: 400, data: { code: 'self_approval_confirmation_required' } } }), /额外勾选确认/)
  assert.match(accessAdminFailure({ response: { status: 403, data: { detail: '不能审批自己的申请。' } } }), /旧规则/)
  assert.match(accessAdminFailure({ response: { status: 409 } }), /版本或审批凭据/)
  assert.match(accessAdminFailure({ response: { status: 403 } }), /只有当前有效的超级管理员/)
  assert.doesNotMatch(accessAdminFailure({ response: { status: 400, data: { detail: '<PRIVATE_STACK>' } } }), /PRIVATE_STACK/)
})

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { SelfApprovalConfirmation } = await server.ssrLoadModule('/src/components/AdminRecommendationAccessPanel.jsx')

test('self-only confirmation renders unchecked and explains scope, audit and no false age claim', () => {
  const html = renderToStaticMarkup(React.createElement(SelfApprovalConfirmation,
    { selected, action: 'approve', checked: false, onChange() {}, disabled: false }))
  assert.match(html, /超级管理员自审/); assert.match(html, /不是年龄核验/); assert.match(html, /不会自动开启/)
  assert.equal((html.match(/type="checkbox"/g) || []).length, 1); assert.doesNotMatch(html, /checked=""/)
  assert.equal(renderToStaticMarkup(React.createElement(SelfApprovalConfirmation,
    { selected: { ...selected, is_self: false }, action: 'approve' })), '')
})

test('selection/action/cancel resets confirmation and approval uses the validated body', () => {
  const source = readFileSync(new URL('../src/components/AdminRecommendationAccessPanel.jsx', import.meta.url), 'utf8')
  assert.ok((source.match(/setSelfApprovalConfirmed\(false\)/g) || []).length >= 5)
  assert.match(source, /body: accessApprovalPayload\(selected, action/)
  assert.match(source, /selected.self_approval_allowed !== true/)
  assert.doesNotMatch(source, /禁止为自己批准/)
})

test('success path clears the receipt and reloads, without evaluating an error-only binding', () => {
  const source = readFileSync(new URL('../src/components/AdminRecommendationAccessPanel.jsx', import.meta.url), 'utf8')
  const submit = source.slice(source.indexOf('const submit = async'), source.indexOf('} catch (failure)'))
  assert.doesNotMatch(submit, /\bfailure\b/)
  assert.match(submit, /await client.post\(operation.current.path, operation.current.body\)/)
  assert.match(submit, /operation.current = null; setConfirmed\(false\); setReason\(''\); setRevision/)
})
