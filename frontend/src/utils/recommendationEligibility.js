// Manual authorization is NOT age verification. M1 never sends content_scope.
export function validateEligibility(data) {
  const statuses = ['not_requested', 'pending', 'active', 'rejected', 'revoked', 'expired', 'legacy_unavailable', 'account_inactive']
  if (!data || data.contract_version !== 'recommendations.eligibility.manual.m1.v1'
    || data.policy_version !== 'eligibility.manual.closed.v1' || !statuses.includes(data.effective_status)
    || !['none', 'legacy', 'manual'].includes(data.authorization_source)
    || !Number.isInteger(data.eligibility_version) || data.eligibility_version < 0
    || typeof data.can_apply !== 'boolean' || typeof data.manual_authorization_valid !== 'boolean'
    || data.age_assurance_status !== 'not_performed' || data.adult_access_allowed !== false
    || data.serving_integration !== false || data.effective_scope !== 'safe'
    || typeof data.decision_message !== 'string'
    || !/^[a-f0-9]{64}$/.test(data.eligibility_revision || '')
    || (data.valid_until !== null && (typeof data.valid_until !== 'string' || !Number.isFinite(Date.parse(data.valid_until))))
    || (data.authorization_source === 'manual' && data.effective_status === 'pending' && data.can_apply)
    || (data.manual_authorization_valid && (data.can_apply || data.authorization_source !== 'manual' || data.effective_status !== 'active' || !data.valid_until))) {
    throw new Error('访问申请合同暂不兼容，请稍后重新读取。')
  }
  return data
}

export function eligibilityCopy(data) {
  if (!data) return '申请状态尚未读取。'
  if (data.effective_status === 'account_inactive') return '账号不可用，不能申请访问授权。'
  if (data.authorization_source === 'legacy') return '历史记录不构成人工授权，请重新提交申请。'
  return { not_requested: '尚未申请受限内容访问。', pending: '申请已提交，等待超级管理员审批。',
    active: '访问申请已批准。NSFW 推荐尚未开放，暂不能开启。',
    rejected: data.decision_message || '本次申请未通过，可重新申请。', revoked: '访问授权已撤销，可重新申请。',
    expired: '访问授权已到期，可重新申请。', account_inactive: '账号不可用，不能申请访问授权。' }[data.effective_status]
    || '当前授权不可用，请重新读取。'
}

export function createEligibilityController(client, onChanged = () => {}, uuid = () => crypto.randomUUID()) {
  let state = { status: 'idle', data: null, confirmed: false, error: '' }, generation = 0, request = null, disposed = false, operation = null
  const listeners = new Set()
  const update = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  const cancel = () => { generation++; request?.controller.abort(); request = null }
  const start = () => {
    const task = { generation, controller: new AbortController() }; request = task
    task.current = () => !disposed && generation === task.generation && request === task
    return task
  }
  const load = () => {
    if (disposed || state.status === 'writing') return Promise.resolve(null)
    cancel(); operation = null
    const task = start(); update({ status: 'loading', confirmed: false, error: '' })
    task.promise = Promise.resolve().then(() => task.current() ? client.eligibility(task.controller.signal) : null).then(raw => {
      if (!task.current()) return null
      const data = validateEligibility(raw); update({ status: 'ready', data }); return data
    }).catch(() => { if (task.current()) update({ status: 'error', data: null, error: '无法读取申请状态，请重试；当前推荐仍为安全范围。' })
    }).finally(() => { if (request === task) request = null })
    return task.promise
  }
  const submit = () => {
    if (disposed) return Promise.resolve(null)
    if (state.status === 'writing') return request.promise
    if (!['ready', 'uncertain'].includes(state.status) || !state.data?.can_apply || !state.confirmed) return Promise.resolve(null)
    if (!operation) operation = { expected_version: state.data.eligibility_version, operation_id: uuid(), confirmed: true }
    const task = start(); update({ status: 'writing', error: '' })
    task.promise = Promise.resolve().then(() => task.current() ? client.applyEligibility(operation, task.controller.signal) : null).then(raw => {
      if (!task.current()) return null
      const data = validateEligibility(raw?.eligibility); operation = null
      update({ status: 'ready', data, confirmed: false })
      try { onChanged(data) } catch {
        update({ error: '申请状态已保存，推荐提示更新失败。可重新读取申请状态。' })
      }
      return data
    }).catch(error => {
      if (!task.current()) return
      const code = error?.response?.status
      if (code === 409) { operation = null; update({ status: 'conflict', error: '申请状态已在其他位置更新，请重新读取后操作。' }) }
      else if (code >= 400 && code < 500) { operation = null; update({ status: 'error', error: '申请未被接受，请重新读取状态后重试。' }) }
      else update({ status: 'uncertain', error: '提交结果尚未确认。可重试确认原申请，不会重复提交新申请。' })
    }).finally(() => { if (request === task) request = null })
    return task.promise
  }
  return { subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    activate() { disposed = false }, load, submit,
    confirm(value) { if (!disposed && state.status === 'ready') update({ confirmed: !!value }) },
    dispose() { disposed = true; cancel(); operation = null },
  }
}
