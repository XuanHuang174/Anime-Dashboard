import { createLatestRequestGate } from './recommendations.js'
import { mapExplorationPage, sameExplorationContext, requireExplorationTransition } from './explorationResults.js'

export function explorationError(error) {
  const status = error?.response?.status
  if (error?.message?.startsWith('探索合同')) return error.message
  if (status === 401) return '登录已失效，请重新登录。'
  if (status === 404) return '参照作品当前不可用，请更换作品；NSFW 作品需要先应用相应筛选。'
  if (status === 429) return '请求较频繁，请稍后重试。'
  if (error?.response?.data?.code === 'exploration_disabled') return '单番相似暂未开放，请稍后再试。'
  if (status === 409) return '探索依据已变化，请重新探索。'
  return '探索读取失败，请重试。'
}
const stale = error => [400, 409].includes(error?.response?.status) || error?.message?.startsWith('探索合同')
const discard = item => ({ ...item, reason: '', evidence: null, evidenceToken: undefined, detailContext: undefined })

// Session/owner-local only, retained by the route host, never storage/global caches.
export function createExplorationBrowseController(client, scope = 'safe') {
  let state = { status: 'idle', items: [], transport: 'explore' }, selection, active = true, busy = false, seen = new Set()
  const root = createLatestRequestGate(), continuation = createLatestRequestGate(), probe = createLatestRequestGate(), listeners = new Set()
  const emit = value => { state = value; listeners.forEach(listener => listener()) }
  const cancel = () => { root.cancel(); continuation.cancel(); probe.cancel(); busy = false }
  const changed = (reason = 'changed') => {
    cancel()
    if (!state.data) return
    emit({ ...state, snapshotDirty: true, contextUpdated: true, contextExpired: reason === 'expired',
      moreLoading: false, batchLoading: false, items: state.items.map(discard) })
  }
  async function start(options = selection) {
    if (!active || !options) return
    selection = options; cancel(); seen = new Set()
    if (!options.query) { emit({ key: options.key, status: 'idle', items: [], transport: 'explore' }); return }
    const request = root.begin()
    emit({ key: options.key, status: 'loading', items: [], transport: 'explore' })
    try {
      const raw = await client.explorationBrowse(options.query, request.signal)
      if (!active || !request.isCurrent()) return
      const data = mapExplorationPage(raw, options.query, scope)
      if (data.batch !== 1 || data.page !== 1) throw new Error('探索合同不兼容，请重新探索。')
      seen = new Set(data.items.map(item => item.bangumiId))
      emit({ key: options.key, status: data.items.length ? 'ready' : 'empty', items: data.items, data, transport: 'explore' })
    } catch (error) {
      if (active && request.isCurrent()) emit({ key: options.key, status: 'error', items: [], transport: 'explore', error: explorationError(error) })
    }
  }
  async function advance(action) {
    if (!active || busy || state.status !== 'ready' || state.snapshotDirty) return false
    if (Date.now() >= state.data.snapshot_expires_at * 1000) { changed('expired'); return false }
    const captured = state.data, cursor = action === 'more' ? captured.next_page_cursor : captured.next_batch_cursor
    if (!cursor) return false
    busy = true
    const request = continuation.begin()
    emit({ ...state, moreLoading: action === 'more', batchLoading: action === 'batch', moreError: '', batchError: '' })
    try {
      const raw = await client.explorationBrowse({ cursor }, request.signal)
      if (!active || !request.isCurrent()) return false
      const data = mapExplorationPage(raw, selection.query, scope)
      requireExplorationTransition(captured, data, action, seen)
      data.items.forEach(item => seen.add(item.bangumiId))
      emit({ ...state, data, items: action === 'more' ? [...state.items, ...data.items] : data.items, moreLoading: false, batchLoading: false })
      return true
    } catch (error) {
      if (!active || !request.isCurrent()) return false
      if (stale(error)) changed()
      else emit({ ...state, moreLoading: false, batchLoading: false, [action === 'more' ? 'moreError' : 'batchError']: explorationError(error) })
      return false
    } finally { if (request.isCurrent()) busy = false }
  }
  async function verify() {
    if (!active || !state.data || state.snapshotDirty || busy || state.status === 'loading') return
    if (Date.now() >= state.data.snapshot_expires_at * 1000) { changed('expired'); return }
    const captured = state.data, request = probe.begin()
    try {
      const data = await client.explorationContext(captured.snapshot_context, request.signal)
      if (!active || !request.isCurrent() || state.data !== captured) return
      if (data?.contract_version !== 'recommendations.explore.context.v1' || data.valid !== true
        || data.snapshot_id !== captured.snapshot_id || data.snapshot_expires_at !== captured.snapshot_expires_at
        || data.query_fingerprint !== captured.evidence_context.query_fingerprint
        || data.selected_seed?.bangumi_id !== captured.query.seed_bangumi_id
        || !sameExplorationContext(data.evidence_context, captured.evidence_context)) changed()
    } catch (error) {
      if (active && request.isCurrent() && state.data === captured && (stale(error) || error?.response?.data?.code === 'exploration_disabled')) changed()
      // Offline/429/temporary 503 never discard a valid session or NSFW choice.
    }
  }
  return {
    getSnapshot: () => state, subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener) },
    activate() { active = true },
    resume(options) {
      if (state.key !== options.key || !state.data) return start(options)
      selection = options
      if (state.moreLoading || state.batchLoading) emit({ ...state, moreLoading: false, batchLoading: false })
      return verify()
    },
    start, retry: () => start(), loadMore: () => advance('more'), nextBatch: () => advance('batch'), verify,
    cancel, contextChanged: changed,
    recordChanged(identity, status) { changed(); emit({ ...state, items: state.items.map(item => item.bangumiId === identity ? { ...item, watchStatus: status } : item) }) },
    dispose() { active = false; cancel() },
  }
}
