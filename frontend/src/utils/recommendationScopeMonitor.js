// Access expiry is a clock boundary, NOT a network completion callback.
// No storage, business writes, ranking or resource-cache ownership here.
export function watchRecommendationScope({ deadline, probe, onLost, onContext,
  target = globalThis.window, document = globalThis.document, now = () => Date.now(),
  interval = 30000, timeout = 20000 }) {
  let stopped = false, expired = false, expiryTimer, probeTimer, request = null, lastProbe = -Infinity
  const clear = timer => { if (timer !== undefined) target.clearTimeout(timer) }
  const cancel = () => {
    if (!request) return
    const old = request; request = null; clear(old.timer); old.controller.abort()
  }
  const lose = () => {
    if (stopped || expired) return
    expired = true; clear(expiryTimer); clear(probeTimer); cancel(); onLost()
  }
  const armExpiry = () => {
    if (stopped || expired) return
    if (!Number.isFinite(deadline) || now() >= deadline) { lose(); return }
    expiryTimer = target.setTimeout(armExpiry, Math.min(2147483647, deadline - now()))
  }
  const schedule = () => {
    clear(probeTimer)
    if (!stopped && !expired && document.visibilityState !== 'hidden') probeTimer = target.setTimeout(check, interval)
  }
  function check() {
    if (stopped || expired) return
    if (now() >= deadline) { lose(); return }
    if (document.visibilityState === 'hidden' || request) return
    if (now() - lastProbe < 1000) { schedule(); return }
    lastProbe = now()
    const task = { controller: new AbortController() }; request = task
    const current = () => !stopped && !expired && request === task && !task.controller.signal.aborted
    task.timer = target.setTimeout(() => {
      if (!current()) return
      cancel(); schedule() // uncertainty is not revocation; the expiry timer remains independent
    }, timeout)
    Promise.resolve().then(() => current() ? probe(task.controller.signal) : null).then(data => {
      if (current()) onContext?.(data)
    }).catch(error => {
      if (current() && (error?.scopeLost || [403, 409].includes(error?.response?.status))) lose()
    }).finally(() => {
      if (request === task) { clear(task.timer); request = null; schedule() }
    })
  }
  const visibility = () => {
    if (now() >= deadline) { lose(); return }
    if (document.visibilityState === 'hidden') { clear(probeTimer); cancel() }
    else check()
  }
  armExpiry(); schedule()
  document.addEventListener('visibilitychange', visibility)
  target.addEventListener('focus', visibility)
  return { check, dispose() {
    stopped = true; clear(expiryTimer); clear(probeTimer); cancel()
    document.removeEventListener('visibilitychange', visibility); target.removeEventListener('focus', visibility)
  } }
}
