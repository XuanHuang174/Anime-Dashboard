// Only opaque invalidations cross tabs. The controller lives inside its account's
// React host; polling checks tiny presentation/revision metadata, never the graph.
export function connectSeriesReminderUpdates(controller, target = window, document = globalThis.document) {
  let disposed = false, debounce, poll, expiry
  const visible = () => document.visibilityState !== 'hidden'
  const flush = () => { if (!disposed && visible()) controller.maybeRefresh() }
  const changed = event => {
    if (event?.reminders === false) return
    controller.invalidate()
    target.clearTimeout(debounce); debounce = target.setTimeout(flush, 250)
  }
  const storage = event => {
    if (event.key === 'anime-dashboard:recommendation-invalidate:v1') {
      try { if (event.newValue && target.localStorage?.getItem('anime-dashboard:series-reminder-skip:v1') === event.newValue) return } catch { /* optional storage hint */ }
      changed()
    } else if (event.key === 'anime-dashboard:feedback-invalidate:v1') changed()
    else if (event.key === 'anime-dashboard:series-reminders-changed:v1') {
      // Another tab's setting may belong to a different account. Re-read OUR own
      // tiny preference/version first; unchanged versions do not invalidate data.
      controller.checkPresentation(true).then(flush)
    }
  }
  const foreground = () => { if (visible()) controller.foreground() }
  const schedule = () => { poll = target.setTimeout(() => { foreground(); if (!disposed) schedule() }, 30000) }
  let armed = null
  const unsubscribe = controller.subscribe(() => {
    const deadline = controller.deadline()
    if (deadline === armed) return
    armed = deadline; target.clearTimeout(expiry)
    if (Number.isFinite(deadline)) expiry = target.setTimeout(() => {
      armed = null; controller.expire(); flush()
    }, Math.min(2147483647, Math.max(0, deadline - Date.now())))
  })
  target.addEventListener('recommendation-data-changed', changed)
  target.addEventListener('series-reminders-data-changed', changed)
  target.addEventListener('storage', storage)
  target.addEventListener('focus', foreground)
  document.addEventListener('visibilitychange', foreground)
  schedule()
  return () => {
    disposed = true; unsubscribe()
    target.clearTimeout(debounce); target.clearTimeout(poll); target.clearTimeout(expiry)
    target.removeEventListener('recommendation-data-changed', changed)
    target.removeEventListener('series-reminders-data-changed', changed)
    target.removeEventListener('storage', storage); target.removeEventListener('focus', foreground)
    document.removeEventListener('visibilitychange', foreground)
  }
}

export function reminderRecordChanged(before, after) {
  if (!before || !after) return true
  const basis = row => [row.watch_status, row.metadata_initialized,
    row.anime?.id, row.anime?.bgm_id, row.anime?.chinese_title, row.anime?.japanese_title,
    row.private_chinese_title, row.private_japanese_title, row.recommendation_identity]
  return JSON.stringify(basis(before)) !== JSON.stringify(basis(after))
}
