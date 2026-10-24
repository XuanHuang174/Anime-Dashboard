import { mapRecommendationItem, recommendationError } from './recommendations.js'
import { validateDetailEvidence } from './recommendationExplanation.js'
import { validateWantResult } from './recommendationWant.js'
import { requireScopedDetail } from './recommendationExecution.js'

// Request-local state machine. No persistence, score writes or resource responses.
export function createRecommendationDetailController(client, adapter) {
  let state = { status: 'closed' }, generation = 0, session = 0, request, disposed = false
  const listeners = new Set()
  const emit = value => { state = value; for (const listener of listeners) listener() }
  const cancel = () => { generation++; request?.abort(); request = null }
  async function load(selection, expectation, confirmedMutation = false) {
    cancel()
    const epoch = generation, controller = new AbortController()
    request = controller
    const current = () => !disposed && epoch === generation
    // Deliberately discard the WHOLE item, not just its token/reason.
    emit({ status: confirmedMutation ? 'refreshing' : 'loading', selection, expectation,
      confirmedMutation, resourceOpen: false })
    try {
      const raw = adapter ? await adapter.load(client, selection, expectation, controller.signal)
        : await client.detail(selection.bangumiId, controller.signal, expectation?.token, selection.detailContext)
      if (!current()) return null
      const item = adapter ? adapter.map(raw, selection, expectation) : validateDetailEvidence(mapRecommendationItem(raw), expectation?.token, expectation?.context)
      if (!adapter) requireScopedDetail(item, selection)
      if (item.bangumiId !== selection.bangumiId) throw new Error('推荐依据合同不兼容，作品身份不一致，请重新加载。')
      emit({ status: 'ready', selection: { ...selection, detailContext: item.detailContext }, expectation, confirmedMutation, resourceOpen: false, item })
      return item
    } catch (error) {
      if (!current()) return null
      const snapshotChanged = error?.response?.status === 409 || /^(推荐依据|探索)合同不兼容/.test(error?.message || '')
      emit({ status: 'error', selection, expectation, confirmedMutation, resourceOpen: false, snapshotChanged,
        message: (confirmedMutation ? '加入操作已确认，但详情更新失败。' : '') + recommendationError(error) })
      return null
    }
  }
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    activate() { disposed = false },
    open(item) {
      if (disposed || !Number.isInteger(item?.bangumiId) || item.bangumiId < 1) return Promise.resolve(null)
      const selection = { bangumiId: item.bangumiId, title: item.title, season: item.season, session: ++session, ...adapter?.select(item) }
      if (item.detailContext !== undefined) {
        selection.detailContext = item.detailContext
        selection.executionContext = item.evidenceContext
      }
      return load(selection, { token: item.evidenceToken, context: item.evidenceContext })
    },
    afterWant(result) {
      // Only a validated successful POST acknowledgement can establish a new context.
      validateWantResult(result, result?.bangumi_id)
      if (disposed || state.status === 'closed') return Promise.resolve(null)
      // Another subject's add also changes owned/profile fingerprints. Refresh the
      // CURRENT selection; never reopen the subject of a late completed POST.
      return load(state.selection, undefined, true)
    },
    retry() {
      if (disposed || state.status !== 'error' || state.snapshotChanged) return Promise.resolve(null)
      return load(state.selection, state.expectation, state.confirmedMutation)
    },
    openResources() {
      if (!disposed && state.status === 'ready' && !state.resourceOpen) {
        emit({ ...state, resourceOpen: true, resourceSession: (state.resourceSession || 0) + 1 })
      }
    },
    closeResources() {
      if (state.status === 'ready') emit({ ...state, resourceOpen: false })
    },
    close() { cancel(); emit({ status: 'closed' }) },
    dispose() { disposed = true; cancel(); state = { status: 'closed' } },
  }
}
