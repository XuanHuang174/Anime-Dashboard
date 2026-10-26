import { contextChanged, contextIdentity } from './recommendationContext.js'

const CHANGE_KEY = 'anime-dashboard:recommendation-invalidate:v1'
const CHANGE_EVENT = 'recommendation-data-changed'

export function notifyRecommendationChange({ local = true, reminders = true } = {}, target = globalThis.window) {
  if (!target) return
  // Only an opaque invalidation nonce. No owner ID, titles, scores, tags or tokens.
  const nonce = `${Date.now()}-${Math.random()}`
  try {
    if (!reminders) target.localStorage.setItem('anime-dashboard:series-reminder-skip:v1', nonce)
    target.localStorage.setItem(CHANGE_KEY, nonce)
  } catch { /* storage may be disabled */ }
  if (local) {
    const event = new Event(CHANGE_EVENT)
    Object.defineProperty(event, 'reminders', { value: reminders })
    target.dispatchEvent(event)
  } else if (reminders) target.dispatchEvent(new Event('series-reminders-data-changed'))
}

export function subscribeRecommendationReload({ onReload, canReload = () => true,
  probe, getContext = () => null, target = globalThis.window, document = globalThis.document,
  delay = 250, minimumInterval = 30000, now = () => Date.now() }) {
  let dirty = false, timer = null, disposed = false, wanted = false, forceCheck = false, request = null, generation = 0, lastCheck = now(), reason = 'changed'
  const cancelProbe = () => { generation++; request?.abort(); request = null }
  const flush = () => {
    if (disposed || timer !== null || document.visibilityState === 'hidden' || !canReload()) return
    if (!dirty && (!wanted || !probe || !contextIdentity(getContext()) || request || !forceCheck && now() - lastCheck < minimumInterval)) return
    timer = target.setTimeout(async () => {
      timer = null
      if (disposed || document.visibilityState === 'hidden' || !canReload()) return
      if (dirty) { dirty = false; wanted = false; forceCheck = false; onReload(reason); return }
      const baseline = getContext(), identity = contextIdentity(baseline)
      if (!wanted || !probe || !identity || request || !forceCheck && now() - lastCheck < minimumInterval) return
      const controller = new AbortController(), captured = generation
      request = controller; lastCheck = now(); wanted = false; forceCheck = false
      try {
        const data = await probe(controller.signal)
        if (disposed || captured !== generation || request !== controller || contextIdentity(getContext()) !== identity) return
        if (document.visibilityState === 'hidden' || !canReload()) { wanted = true; return }
        if (contextChanged(data, baseline)) { dirty = true; reason = 'changed'; flush() }
      } catch { /* Offline/404/429/503/malformed probes NEVER clear a valid browse session. */ }
      finally { if (request === controller) request = null }
    }, delay)
  }
  const mark = () => { cancelProbe(); dirty = true; reason = 'changed'; flush() }
  const verify = () => { cancelProbe(); wanted = true; forceCheck = true; flush() }
  // An opaque cross-tab nonce may belong to a DIFFERENT signed-in account.
  const storage = event => { if (event.key === CHANGE_KEY) { if (probe) verify(); else mark() } }
  const visible = () => {
    if (document.visibilityState === 'hidden') { cancelProbe(); return }
    const expires = getContext()?.expires_at
    if (Number.isInteger(expires) && expires > 0 && now() >= expires * 1000) {
      cancelProbe(); dirty = true; reason = 'expired'
    } else wanted = true
    flush()
  }
  const pageshow = event => { if (event.persisted) { if (probe) verify(); else visible() } }
  target.addEventListener(CHANGE_EVENT, mark)
  target.addEventListener('storage', storage)
  target.addEventListener('focus', visible)
  target.addEventListener('pageshow', pageshow)
  document.addEventListener('visibilitychange', visible)
  return {
    flush,
    acknowledge() {
      cancelProbe(); dirty = false; wanted = false; forceCheck = false; lastCheck = now()
      if (timer !== null) { target.clearTimeout(timer); timer = null }
    },
    dispose() {
      disposed = true; cancelProbe(); if (timer !== null) target.clearTimeout(timer)
      target.removeEventListener(CHANGE_EVENT, mark)
      target.removeEventListener('storage', storage)
      target.removeEventListener('focus', visible)
      target.removeEventListener('pageshow', pageshow)
      document.removeEventListener('visibilitychange', visible)
    },
  }
}
