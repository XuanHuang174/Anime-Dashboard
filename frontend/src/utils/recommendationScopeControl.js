import { validateEligibility } from './recommendationEligibility.js'

export function withAvailableScope(draft, available) {
  return { ...draft, includeAdult: available === true && draft.includeAdult === true }
}

export function readScopeControl(raw) {
  const data = validateEligibility(raw)
  // Rolling deployment: old servers retain the same permission checks.
  const control = Object.hasOwn(data, 'scope_control') ? data.scope_control : {
    contract_version: 'recommendations.scope-control.v1', selection_mode: 'session_draft',
    default_scope: 'safe', available: data.adult_access_allowed,
    reason: data.adult_access_allowed ? 'available' : data.serving_integration ? 'access_unavailable' : 'service_closed',
    revision: data.eligibility_revision,
  }
  const reason = control?.available ? 'available' : data.serving_integration ? 'access_unavailable' : 'service_closed'
  if (!control || control.contract_version !== 'recommendations.scope-control.v1'
    || control.selection_mode !== 'session_draft' || control.default_scope !== 'safe'
    || typeof control.available !== 'boolean' || control.available !== data.adult_access_allowed
    || control.reason !== reason || control.revision !== data.eligibility_revision) {
    throw new Error('内容范围合同暂不兼容。')
  }
  return { ...control }
}

export function scopeControlCopy(state) {
  if (state.status === 'idle' || state.status === 'loading') return '正在读取可用状态…'
  if (state.status === 'error') return '无法读取可用状态，当前保持普通推荐。'
  if (state.data?.available) return '仅对本次浏览生效，应用筛选后更新。'
  if (state.data?.reason === 'service_closed') return 'NSFW 推荐暂未开放，普通推荐不受影响。'
  return '当前访问条件暂不可用，普通推荐不受影响。'
}

// This controller has no mutation method and never calls the application API.
export function createScopeControlReader(client) {
  let state = { status: 'idle', data: null }, generation = 0, request = null, disposed = false
  const listeners = new Set()
  const update = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  const cancel = () => { generation++; request?.controller.abort(); request = null }
  const load = () => {
    if (disposed) return Promise.resolve(null)
    if (request) return request.promise
    const task = { generation: ++generation, controller: new AbortController() }
    request = task
    const current = () => !disposed && request === task && task.generation === generation
    update({ status: 'loading', data: null })
    task.promise = Promise.resolve().then(() => current() ? client.eligibility(task.controller.signal) : null)
      .then(raw => {
        if (!current()) return null
        const data = readScopeControl(raw); update({ status: 'ready', data }); return data
      }).catch(() => { if (current()) update({ status: 'error', data: null }); return null })
      .finally(() => { if (request === task) request = null })
    return task.promise
  }
  return { load, getSnapshot: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    activate() { disposed = false },
    dispose() { disposed = true; cancel() },
  }
}
