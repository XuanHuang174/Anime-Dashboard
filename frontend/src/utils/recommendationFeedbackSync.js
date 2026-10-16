// No IDs, titles, tags, tokens, feedback lists, or user identifiers cross tabs.
import { validateHiddenFeedback } from './recommendationFeedback.js'
const KEY = 'anime-dashboard:feedback-invalidate:v1'
export function notifyFeedbackChange(target = globalThis.window) {
  if (!target) return
  const nonce = `${Date.now()}-${Math.random()}`
  try { target.localStorage.setItem(KEY, nonce) } catch { /* disabled storage */ }
  try { const channel = new target.BroadcastChannel(KEY); channel.postMessage('invalidate'); channel.close() } catch { /* fallback to storage/focus */ }
}

export function subscribeFeedbackChanges({ client, getVersion, onChanged, target = globalThis.window,
  document = globalThis.document, delay = 200 }) {
  let timer, request, generation = 0, disposed = false, channel
  const check = () => {
    if (disposed || document.visibilityState === 'hidden') return
    target.clearTimeout(timer)
    // Invalidate a previous verification immediately, not when debounce fires.
    generation++; request?.abort()
    const captured = generation
    timer = target.setTimeout(async () => {
      request = new AbortController()
      try {
        const data = validateHiddenFeedback(await client.hiddenFeedback({ page: 1, page_size: 1 }, request.signal))
        if (disposed || captured !== generation || !Number.isInteger(data?.feedback_version)) return
        if (Number.isInteger(getVersion()) && data.feedback_version > getVersion()) onChanged(data.feedback_version)
      } catch { /* A failed sync probe is not empty data. Focus/next event retries. */ }
    }, delay)
  }
  const storage = event => { if (event.key === KEY) check() }
  const message = event => { if (event.data === 'invalidate') check() }
  target.addEventListener('storage', storage); target.addEventListener('focus', check)
  document.addEventListener('visibilitychange', check)
  try { channel = new target.BroadcastChannel(KEY); channel.onmessage = message } catch { /* no channel */ }
  return () => {
    disposed = true; generation++; target.clearTimeout(timer); request?.abort(); channel?.close()
    target.removeEventListener('storage', storage); target.removeEventListener('focus', check)
    document.removeEventListener('visibilitychange', check)
  }
}
