const CHANGE_KEY = 'anime-dashboard:recommendation-invalidate:v1'
const CHANGE_EVENT = 'recommendation-data-changed'

export function notifyRecommendationChange({ local = true } = {}, target = globalThis.window) {
  if (!target) return
  // Only an opaque invalidation nonce. No owner ID, titles, scores, tags or tokens.
  try { target.localStorage.setItem(CHANGE_KEY, `${Date.now()}-${Math.random()}`) } catch { /* storage may be disabled */ }
  if (local) target.dispatchEvent(new Event(CHANGE_EVENT))
}

export function subscribeRecommendationReload({ onReload, canReload = () => true,
  target = globalThis.window, document = globalThis.document, delay = 250 }) {
  let dirty = false, timer = null, disposed = false
  const flush = () => {
    if (disposed || !dirty || timer !== null || document.visibilityState === 'hidden' || !canReload()) return
    timer = target.setTimeout(() => {
      timer = null
      if (disposed || !dirty || document.visibilityState === 'hidden' || !canReload()) return
      dirty = false; onReload()
    }, delay)
  }
  const mark = () => { dirty = true; flush() }
  const storage = event => { if (event.key === CHANGE_KEY) mark() }
  const visible = () => { if (document.visibilityState !== 'hidden') mark() }
  const pageshow = event => { if (event.persisted) mark() }
  target.addEventListener(CHANGE_EVENT, mark)
  target.addEventListener('storage', storage)
  target.addEventListener('focus', visible)
  target.addEventListener('pageshow', pageshow)
  document.addEventListener('visibilitychange', visible)
  return {
    flush,
    acknowledge() {
      dirty = false
      if (timer !== null) { target.clearTimeout(timer); timer = null }
    },
    dispose() {
      disposed = true; if (timer !== null) target.clearTimeout(timer)
      target.removeEventListener(CHANGE_EVENT, mark)
      target.removeEventListener('storage', storage)
      target.removeEventListener('focus', visible)
      target.removeEventListener('pageshow', pageshow)
      document.removeEventListener('visibilitychange', visible)
    },
  }
}
