import { createLatestRequestGate, recommendationError, recommendationResultState } from './recommendations.js'
import { loadRecommendationSnapshot } from './recommendationSnapshot.js'
import { BROWSE_UPDATED, browseConditions, mapBrowsePage, requireBrowseTransition, sameConditions } from './recommendationBrowse.js'

const discardEvidence = item => ({ ...item, evidence: null, reason: '', evidenceToken: undefined, evidenceContext: undefined })

// Authenticated workspace state only. No private persistence or resource cache.
export function createBrowseController(client) {
  let state = { status: 'loading', items: [] }, selection, generation = 0, active = true
  let confirmedPreferences, confirmedFeedback, busy = false, seen = new Set()
  const root = createLatestRequestGate(), continuation = createLatestRequestGate(), listeners = new Set()
  const emit = value => { state = value; for (const listener of listeners) listener() }
  const cancel = () => { generation++; root.cancel(); continuation.cancel(); busy = false }
  async function start(options = selection, acknowledged = {}) {
    if (!active || !options) return
    selection = { ...options, filters: { ...options.filters } }
    if (acknowledged.confirmedPreferences) confirmedPreferences = acknowledged.confirmedPreferences
    if (acknowledged.confirmedFeedback) confirmedFeedback = { version: Math.max(
      acknowledged.confirmedFeedback.version, confirmedFeedback?.version || 0) }
    cancel(); seen = new Set()
    const epoch = generation, request = root.begin()
    emit({ key: selection.key, status: 'loading', items: [], confirmedPreferences, confirmedFeedback })
    try {
      const snapshot = await loadRecommendationSnapshot(client, { ...selection, browse: true, signal: request.signal,
        confirmedPreferences, minimumFeedbackVersion: confirmedFeedback?.version })
      if (!active || epoch !== generation || !request.isCurrent()) return
      if (!sameConditions(snapshot.data.effective_conditions, browseConditions(selection))
        || snapshot.data.page !== 1 || snapshot.data.batch !== 1) throw new Error('推荐浏览合同不兼容，请重新加载。')
      seen = new Set(snapshot.items.map(item => item.bangumiId))
      confirmedPreferences = null; confirmedFeedback = null
      emit({ key: selection.key, status: recommendationResultState(snapshot.data), ...snapshot, moreLoading: false, batchLoading: false })
    } catch (error) {
      if (!active || epoch !== generation || !request.isCurrent()) return
      root.cancel()
      emit({ key: selection.key, status: 'error', items: [], confirmedPreferences, confirmedFeedback,
        snapshotExpired: error?.response?.status === 409, error: error?.response?.status === 409 ? BROWSE_UPDATED : recommendationError(error) })
    }
  }
  async function advance(action) {
    if (!active || busy || state.status !== 'ready' || state.snapshotExpired || state.snapshotDirty) return false
    const captured = state.data, target = action === 'more' ? captured.next_page_cursor : captured.next_batch_cursor
    if (!target) return false
    busy = true
    const epoch = generation, request = continuation.begin()
    emit({ ...state, moreLoading: action === 'more', batchLoading: action === 'batch', moreError: '', batchError: '' })
    try {
      const data = mapBrowsePage(await client.browse({ cursor: target }, request.signal))
      if (!active || epoch !== generation || !request.isCurrent()) return false
      requireBrowseTransition(captured, data, action, seen)
      data.items.forEach(item => seen.add(item.bangumiId))
      emit({ ...state, data, items: action === 'more' ? [...state.items, ...data.items] : data.items,
        moreLoading: false, batchLoading: false, moreError: '', batchError: '' })
      return true
    } catch (error) {
      if (!active || epoch !== generation || !request.isCurrent()) return false
      const expired = error?.response?.status === 409 || error?.snapshotChanged || /^推荐.*合同不兼容/.test(error?.message || '')
      emit({ ...state, moreLoading: false, batchLoading: false, snapshotExpired: expired,
        ...(expired ? { moreError: '', batchError: '', error: BROWSE_UPDATED,
          // Keep the old batch's layout, but stop presenting invalid evidence.
          items: state.items.map(discardEvidence) }
          : { [action === 'more' ? 'moreError' : 'batchError']: recommendationError(error) }) })
      return false
    } finally { if (epoch === generation && request.isCurrent()) busy = false }
  }
  return {
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    activate() { active = true }, start, retry: acknowledged => start(selection, acknowledged),
    loadMore: () => advance('more'), nextBatch: () => advance('batch'), cancel,
    recordChanged(identity, watchStatus) {
      cancel()
      emit({ ...state, snapshotDirty: true, moreLoading: false, batchLoading: false,
        items: state.items.map(item => discardEvidence(item.bangumiId === identity ? { ...item, watchStatus } : item)) })
    },
    dispose() { active = false; cancel() },
  }
}
