import { CREDIT_DEFAULTS, validateCreditConditions, validateCreditsSearch, validateCreditsWorks, validateCreditsDetail, sameCreditConditions, creditsFailure } from './recommendationCredits.js'
import { validateWantResult, wantFailure } from './recommendationWant.js'

// React-owned, session/scope-bound cache; no storage and no visibility-triggered list reads.
export function createCreditsWorkspace({ client, kind, person = null, scope = 'safe', cache = {}, isCurrent = () => true, now = Date.now, onConfirmed = () => {} }) {
  let active = true, request = null, serial = 0
  let state = { ...(cache.state || { status: 'idle', data: null, pending: false, morePending: false, stale: false, message: '' }), detail: null, operations: {} }
  const interactions = new Set(), writes = new Map()
  let notifying = false
  const listeners = new Set()
  const emit = value => { state = { ...state, ...value }; cache.state = state; listeners.forEach(fn => fn()) }
  const cancel = () => { request?.abort(); request = null; serial++; interactions.forEach(c => c.abort()); interactions.clear(); writes.clear() }
  const invalidate = (lost = false, message = '资料已到期，请重新读取后继续。') => {
    cancel(); emit({ ...(lost ? { data: null, status: 'error' } : {}), detail: null, operations: {}, stale: true, pending: false, morePending: false, message })
  }
  async function load(options = CREDIT_DEFAULTS, more = false) {
    if (!active || !isCurrent() || writes.size || more && (state.pending || state.morePending || state.stale || !state.data?.next_cursor)) return false
    if (more && state.data.expires_at * 1000 <= now()) { invalidate(); return false }
    if (person) validateCreditConditions(options)
    cancel(); const order = serial, controller = new AbortController(); request = controller
    const current = () => active && isCurrent() && serial === order && !controller.signal.aborted
    const previous = state.data
    emit({ status: previous ? 'ready' : 'loading', detail: null, operations: {}, pending: !more, morePending: more, message: '', query: person ? options : String(options) })
    try {
      const raw = person ? await client.creditsWorks(kind, person, more ? { cursor: previous.next_cursor } : options, controller.signal)
        : await client.creditsSearch(kind, options, controller.signal)
      if (!current()) return false
      const data = person ? validateCreditsWorks(raw, kind, person, more ? previous.conditions : options, scope, now())
        : validateCreditsSearch(raw, kind, scope)
      if (more) {
        const ids = new Set(previous.results.map(row => row.bangumi_id))
        if (data.credits_revision !== previous.credits_revision || data.count !== previous.count || data.expires_at !== previous.expires_at
          || data.content_scope_context.scope_revision !== previous.content_scope_context.scope_revision
          || data.results.some(row => ids.has(row.bangumi_id)) || data.results.length === 0
          || data.results.length + previous.results.length > data.count
          || data.next_cursor === null && data.results.length + previous.results.length !== data.count) throw new Error('人物与制作资料合同不兼容，请重新读取。')
        emit({ status: 'ready', data: { ...data, results: [...previous.results, ...data.results] }, stale: false })
      } else {
        if (person && data.next_cursor === null && data.results.length !== data.count) throw new Error('人物与制作资料合同不兼容，请重新读取。')
        emit({ status: 'ready', data, stale: false, loadedAt: now() })
      }
      return true
    } catch (error) {
      if (!current()) return false
      const lost = error?.scopeLost || [401, 403, 404].includes(error?.response?.status) || error?.response?.data?.code === 'credits_disabled'
      const changed = error?.response?.data?.code === 'credits_snapshot_changed' || error?.message?.startsWith('人物与制作资料合同')
      emit({ status: previous && !lost ? 'ready' : 'error', data: lost ? null : previous,
        stale: lost || changed || state.stale, message: `${previous && !lost ? '当前保留上次已确认结果。' : ''}${creditsFailure(error)}` })
      return false
    } finally { if (current()) { request = null; emit({ pending: false, morePending: false }) } }
  }
  const actionable = target => active && isCurrent() && !state.pending && !state.morePending && !state.stale
    && state.data?.expires_at * 1000 > now() && state.data.results.find(row => row.bangumi_id === target)
  const interaction = () => {
    const controller = new AbortController(), order = serial
    interactions.add(controller)
    return { signal: controller.signal, current: () => active && isCurrent() && serial === order && !controller.signal.aborted,
      done: () => interactions.delete(controller) }
  }
  const interactionFailure = error => {
    if (error?.scopeLost || [401, 403, 404].includes(error?.response?.status) && error?.response?.data?.code !== 'capacity_exceeded'
      || error?.response?.data?.code === 'credits_disabled') { invalidate(true, creditsFailure(error)); return true }
    if (error?.response?.data?.code === 'credits_snapshot_changed' || error?.message?.startsWith('人物与制作资料合同')) {
      invalidate(false, creditsFailure(error)); return true
    }
    return false
  }
  return { subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    isNotifying: () => notifying,
    activate() { active = true }, dispose() {
      const uncertain = writes.size > 0
      active = false; cancel()
      state = { ...state, detail: null, operations: {}, pending: false, morePending: false,
        ...(uncertain ? { stale: true, message: '离开时加入请求尚未确认，请重新读取本人状态。' } : {}),
        status: state.data ? 'ready' : state.status === 'loading' ? 'idle' : state.status }
      cache.state = state
    }, load,
    async select(target) {
      const row = actionable(target)
      if (!row || writes.size) return
      const req = interaction(), selected = { status: 'loading', target }
      emit({ detail: selected })
      try {
        const raw = await client.creditsDetail(kind, person, target, row.action_context, req.signal)
        if (!req.current() || state.detail !== selected) return
        const item = validateCreditsDetail(raw, kind, person, row, scope)
        if (raw.credits_revision !== state.data.credits_revision || raw.content_scope_context.scope_revision !== state.data.content_scope_context.scope_revision) throw new Error('人物与制作资料合同不兼容，请重新读取。')
        const changed = item.watchStatus !== row.watch_status
        emit({ detail: { status: 'ready', target, item }, data: { ...state.data,
          results: state.data.results.map(r => r.bangumi_id === target ? { ...r, watch_status: item.watchStatus } : r) },
          ...(changed ? { stale: true, message: '本人记录状态已变化，重新读取可更新筛选结果与数量。' } : {}) })
      } catch (error) {
        if (req.current() && !interactionFailure(error) && state.detail === selected) emit({ detail: { status: 'error', target, message: creditsFailure(error) } })
      } finally { req.done() }
    },
    closeDetail() { emit({ detail: null }) },
    want(target) {
      if (writes.has(target)) return writes.get(target)
      const row = actionable(target)
      if (!row || row.watch_status || state.detail?.target === target && state.detail.item?.watchStatus || writes.size) return Promise.resolve(null)
      const req = interaction()
      emit({ detail: state.detail?.status === 'loading' ? null : state.detail,
        operations: { ...state.operations, [target]: { status: 'adding' } } })
      const promise = Promise.resolve().then(() => req.current() ? client.creditsWant(kind, person, target, row.action_context, req.signal) : null).then(raw => {
        if (!req.current()) return null
        const data = validateWantResult(raw, target)
        if (data.content_rating !== row.content_rating) throw new Error('加入想看数据合同不兼容，请重新读取。')
        emit({ data: { ...state.data, results: state.data.results.map(r => r.bangumi_id === target ? { ...r, watch_status: data.watch_status } : r) },
          detail: state.detail?.target === target && state.detail.status === 'ready' ? { ...state.detail, item: { ...state.detail.item, watchStatus: data.watch_status } } : state.detail,
          operations: { ...state.operations, [target]: { status: 'ready', watchStatus: data.watch_status } },
          stale: true, message: '加入操作已确认。本人状态已更新，重新读取可更新筛选结果与数量。' })
        // Invalidate other views without aborting this committed acknowledgement.
        notifying = true
        try { Promise.resolve(onConfirmed(data)).catch(() => {}) } catch { /* Saved state stays confirmed. */ } finally { notifying = false }
        return data
      }).catch(error => {
        if (req.current() && !interactionFailure(error)) emit({ operations: { ...state.operations, [target]: { status: 'error', error: wantFailure(error) } } })
        return null
      }).finally(() => { if (writes.get(target) === promise) writes.delete(target); req.done() })
      writes.set(target, promise)
      return promise
    },
    more: () => load(state.data?.conditions || CREDIT_DEFAULTS, true), invalidate,
    ensure(options) {
      if (state.data && !state.stale && (person ? sameCreditConditions(options, state.data.conditions) && state.data.expires_at * 1000 > now()
        : state.query === options && now() - state.loadedAt < 900000)) return
      return load(options)
    }, clear() { cancel(); emit({ status: 'idle', data: null, detail: null, operations: {}, stale: false, message: '', pending: false, morePending: false }) } }
}
