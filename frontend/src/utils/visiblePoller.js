import { retryAfterMs } from '../api/retryAfter.js'

export function createVisiblePoller({ poll, onError = () => {}, isVisible,
  now = () => performance.now(), intervalMs = 30000,
  setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  let stopped = false, pending = false, timer = null, nextAt = 0, failures = 0
  function clear() { if (timer !== null) clearTimer(timer); timer = null }
  function wake() {
    clear()
    if (stopped || pending || !isVisible()) return
    const remaining = nextAt - now()
    if (remaining > 0) { timer = setTimer(wake, Math.min(remaining, 2147483647)); return }
    pending = true
    Promise.resolve().then(poll).then(() => {
      failures = 0
      nextAt = now() + intervalMs
    }, error => {
      failures += 1
      const backoff = Math.min(300000, intervalMs * 2 ** Math.min(failures - 1, 4))
      const delay = error?.response?.status === 429 ? Math.max(backoff, retryAfterMs(error)) : backoff
      nextAt = now() + delay
      if (!stopped && failures === 1) onError(error)
    }).finally(() => { pending = false; wake() })
  }
  return { wake, stop() { stopped = true; clear() } }
}
