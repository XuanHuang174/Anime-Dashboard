import { validateSeries, validateSeriesDetail, seriesFailure } from './recommendationSeries.js'
import { validateWantResult, wantFailure } from './recommendationWant.js'

// All state is in memory and belongs to one account/source/scope. No speculative writes.
export function createSeriesWorkspace({ client, source, scope, isCurrent = () => true, onConfirmed = () => {} }) {
  let state = { status: 'loading', data: null, detail: null, operations: {} }, epoch = 0, disposed = false
  const listeners = new Set(), requests = new Set(), pending = new Set()
  let loading = false
  const emit = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  const request = () => {
    const controller = new AbortController(), version = epoch
    requests.add(controller)
    return { signal: controller.signal, current: () => !disposed && !controller.signal.aborted && version === epoch && isCurrent(),
      done: () => requests.delete(controller) }
  }
  const cancel = () => { epoch++; requests.forEach(controller => controller.abort()); requests.clear(); pending.clear(); loading = false }
  const invalidate = error => { cancel(); emit({ status: 'error', data: null, detail: null, operations: {}, message: seriesFailure(error) }) }
  const changed = error => error?.scopeLost || [401, 403, 404].includes(error?.response?.status)
    || error?.response?.data?.code === 'series_snapshot_changed'
  const controller = {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    getSnapshot: () => state,
    activate() { disposed = false },
    dispose() { disposed = true; cancel() },
    invalidate,
    async load(probe = false) {
      if (disposed || !isCurrent() || loading) return
      const context = probe ? state.data?.context : null
      if (probe && !context) return
      if (!probe) { cancel(); emit({ status: 'loading', data: null, detail: null, operations: {} }) }
      loading = true
      const req = request()
      try {
        const data = validateSeries(await client.series(source, context, req.signal), source, scope)
        if (req.current()) emit({ status: 'ready', data, message: null })
      } catch (error) { if (req.current()) invalidate(error) }
      finally { req.done(); if (req.current()) loading = false }
    },
    async select(target) {
      if (state.status !== 'ready' || !isCurrent()) return
      const req = request(), selected = { status: 'loading', target }
      emit({ detail: selected })
      try {
        const data = await client.seriesDetail(source, target, state.data.context, req.signal)
        if (!req.current() || state.detail !== selected) return
        const item = validateSeriesDetail(data, state.data, target, scope)
        emit({ detail: { status: 'ready', target, item } })
      } catch (error) {
        if (req.current()) {
          if (changed(error)) invalidate(error)
          else if (state.detail === selected) emit({ detail: { status: 'error', target, message: seriesFailure(error) } })
        }
      } finally { req.done() }
    },
    closeDetail() { emit({ detail: null }) },
    async want(target) {
      if (state.status !== 'ready' || !isCurrent() || pending.has(target)) return
      const row = target === source ? state.data.selected_seed : state.data.results.find(item => item.bangumi_id === target)
      if (!row || row.watch_status || row.record_state === 'needs_review') return
      const req = request()
      pending.add(target)
      emit({ operations: { ...state.operations, [target]: { status: 'adding' } } })
      try {
        const raw = await client.seriesWant(source, target, state.data.context, req.signal)
        if (!req.current()) return
        const data = validateWantResult(raw, target)
        const update = item => item.bangumi_id === target ? { ...item, watch_status: data.watch_status, record_state: 'linked', can_add_from_series: false } : item
        emit({ data: { ...state.data, selected_seed: update(state.data.selected_seed), results: state.data.results.map(update) },
          detail: state.detail?.status === 'ready' && state.detail.target === target ? { ...state.detail, item: { ...state.detail.item, watchStatus: data.watch_status, recordState: 'linked' } } : state.detail,
          operations: { ...state.operations, [target]: { status: 'ready', watchStatus: data.watch_status } } })
        // A committed acknowledgement must never turn into a failed write if UI notification fails.
        try { await onConfirmed(data) } catch { /* A later probe reconciles own states. */ }
      } catch (error) {
        if (req.current()) {
          if (changed(error) && error?.response?.data?.code !== 'capacity_exceeded') invalidate(error)
          else emit({ operations: { ...state.operations, [target]: { status: 'error', error: wantFailure(error) } } })
        }
      } finally { if (req.current()) pending.delete(target); req.done() }
    },
  }
  return controller
}
