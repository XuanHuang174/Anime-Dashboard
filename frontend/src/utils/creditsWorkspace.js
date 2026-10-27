import { CREDIT_DEFAULTS, validateCreditConditions, validateCreditsSearch, validateCreditsWorks, sameCreditConditions, creditsFailure } from './recommendationCredits.js'

// React-owned, session/scope-bound cache; no storage and no visibility-triggered list reads.
export function createCreditsWorkspace({ client, kind, person = null, scope = 'safe', cache = {}, isCurrent = () => true, now = Date.now }) {
  let active = true, request = null, serial = 0
  let state = cache.state || { status: 'idle', data: null, pending: false, morePending: false, stale: false, message: '' }
  const listeners = new Set()
  const emit = value => { state = { ...state, ...value }; cache.state = state; listeners.forEach(fn => fn()) }
  const cancel = () => { request?.abort(); request = null; serial++ }
  const invalidate = (lost = false, message = '资料已到期，请重新读取后继续。') => {
    cancel(); emit({ ...(lost ? { data: null, status: 'error' } : {}), stale: true, pending: false, morePending: false, message })
  }
  async function load(options = CREDIT_DEFAULTS, more = false) {
    if (!active || !isCurrent() || more && (state.pending || state.morePending || state.stale || !state.data?.next_cursor)) return false
    if (more && state.data.expires_at * 1000 <= now()) { invalidate(); return false }
    if (person) validateCreditConditions(options)
    cancel(); const order = serial, controller = new AbortController(); request = controller
    const current = () => active && isCurrent() && serial === order && !controller.signal.aborted
    const previous = state.data
    emit({ status: previous ? 'ready' : 'loading', pending: !more, morePending: more, message: '', query: person ? options : String(options) })
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
  return { subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    activate() { active = true }, dispose() {
      active = false; cancel()
      state = { ...state, pending: false, morePending: false, status: state.data ? 'ready' : state.status === 'loading' ? 'idle' : state.status }
      cache.state = state
    }, load,
    more: () => load(state.data?.conditions || CREDIT_DEFAULTS, true), invalidate,
    ensure(options) {
      if (state.data && !state.stale && (person ? sameCreditConditions(options, state.data.conditions) && state.data.expires_at * 1000 > now()
        : state.query === options && now() - state.loadedAt < 900000)) return
      return load(options)
    }, clear() { cancel(); emit({ status: 'idle', data: null, stale: false, message: '', pending: false, morePending: false }) } }
}
