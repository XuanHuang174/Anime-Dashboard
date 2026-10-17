import { createLatestRequestGate, mapRecommendationPage, recommendationError, recommendationResultState } from './recommendations.js'
import { requireNextRecommendationEpoch } from './recommendationEpoch.js'
import { loadRecommendationSnapshot } from './recommendationSnapshot.js'
import { BROWSE_UPDATED, browseConditions, mapBrowsePage, requireBrowseTransition, sameConditions } from './recommendationBrowse.js'

const discardEvidence = item => ({ ...item, evidence: null, reason: '', evidenceToken: undefined, evidenceContext: undefined })
const rollbackSignal = error => [409, 503].includes(error?.response?.status)
  && error.response.data?.code === 'browse_disabled' && error.response.data?.fallback === 'legacy_v1'
  && error.response.data?.algorithm_version === 'content.v1'

async function transportMode(client, signal) {
  if (!client.capabilities) return 'browse' // Injected fixtures/older server capability route.
  let data
  try { data = await client.capabilities(signal) } catch (error) {
    if (error?.response?.status === 404) return 'browse' // Pre-9C server still supports 9A.
    throw error
  }
  if (data?.contract_version !== 'recommendations.capabilities.v1' || data.algorithm_version !== 'content.v1'
    || typeof data.browse_enabled !== 'boolean' || !/^[a-f0-9]{64}$/.test(data.rollout_revision)) {
    throw new Error('推荐浏览合同不兼容，请重新加载。')
  }
  return data.browse_enabled ? 'browse' : 'legacy'
}

// Authenticated workspace state only. No private persistence or resource cache.
export function createBrowseController(client) {
  let state = { status: 'loading', items: [] }, selection, generation = 0, active = true
  let confirmedPreferences, confirmedFeedback, busy = false, seen = new Set()
  const root = createLatestRequestGate(), continuation = createLatestRequestGate(), listeners = new Set()
  const emit = value => { state = value; for (const listener of listeners) listener() }
  const cancel = () => { generation++; root.cancel(); continuation.cancel(); busy = false }
  async function start(options = selection, acknowledged = {}, forcedTransport) {
    if (!active || !options) return
    selection = { ...options, filters: { ...options.filters } }
    if (acknowledged.confirmedPreferences) confirmedPreferences = acknowledged.confirmedPreferences
    if (acknowledged.confirmedFeedback) confirmedFeedback = { version: Math.max(
      acknowledged.confirmedFeedback.version, confirmedFeedback?.version || 0) }
    cancel(); seen = new Set()
    const epoch = generation, request = root.begin()
    emit({ key: selection.key, status: 'loading', items: [], confirmedPreferences, confirmedFeedback })
    try {
      const transport = forcedTransport || await transportMode(client, request.signal)
      if (!active || epoch !== generation || !request.isCurrent()) return
      const effectiveSelection = transport === 'legacy' ? { ...selection, style: 'balanced',
        filters: { ...selection.filters, yearFrom: '', yearTo: '' } } : selection
      const snapshot = await loadRecommendationSnapshot(client, { ...effectiveSelection, browse: transport === 'browse', signal: request.signal,
        confirmedPreferences, minimumFeedbackVersion: confirmedFeedback?.version })
      if (!active || epoch !== generation || !request.isCurrent()) return
      if (transport === 'browse' && (!sameConditions(snapshot.data.effective_conditions, browseConditions(selection))
        || snapshot.data.page !== 1 || snapshot.data.batch !== 1)) throw new Error('推荐浏览合同不兼容，请重新加载。')
      if (transport === 'legacy' && (snapshot.data.algorithm_version !== 'content.v1' || snapshot.data.page !== 1)) {
        throw new Error('推荐浏览合同不兼容，请重新加载。')
      }
      seen = new Set(snapshot.items.map(item => item.bangumiId))
      confirmedPreferences = null; confirmedFeedback = null
      emit({ key: selection.key, transport, status: recommendationResultState(snapshot.data), ...snapshot, moreLoading: false, batchLoading: false })
    } catch (error) {
      if (!active || epoch !== generation || !request.isCurrent()) return
      if (!forcedTransport && rollbackSignal(error)) return start(selection, acknowledged, 'legacy')
      root.cancel()
      emit({ key: selection.key, status: 'error', items: [], confirmedPreferences, confirmedFeedback,
        snapshotExpired: error?.response?.status === 409, error: error?.response?.status === 409 ? BROWSE_UPDATED : recommendationError(error) })
    }
  }
  async function advance(action) {
    if (!active || busy || state.status !== 'ready' || state.snapshotExpired || state.snapshotDirty) return false
    const captured = state.data, legacy = state.transport === 'legacy'
    const target = legacy ? action === 'more' && captured.next_page
      : action === 'more' ? captured.next_page_cursor : captured.next_batch_cursor
    if (!target) return false
    busy = true
    const epoch = generation, request = continuation.begin()
    emit({ ...state, moreLoading: action === 'more', batchLoading: action === 'batch', moreError: '', batchError: '' })
    try {
      const data = legacy ? mapRecommendationPage(await client.list({ ...selection, page: target,
        stateToken: captured.state_token }, request.signal)) : mapBrowsePage(await client.browse({ cursor: target }, request.signal))
      if (!active || epoch !== generation || !request.isCurrent()) return false
      if (legacy) {
        requireNextRecommendationEpoch(captured, data)
        if (data.items.some(item => seen.has(item.bangumiId))) { const error = new Error(BROWSE_UPDATED); error.snapshotChanged = true; throw error }
      } else requireBrowseTransition(captured, data, action, seen)
      data.items.forEach(item => seen.add(item.bangumiId))
      emit({ ...state, data, items: action === 'more' ? [...state.items, ...data.items] : data.items,
        moreLoading: false, batchLoading: false, moreError: '', batchError: '' })
      return true
    } catch (error) {
      if (!active || epoch !== generation || !request.isCurrent()) return false
      if (!legacy && rollbackSignal(error)) { await start(selection, {}, 'legacy'); return false }
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
