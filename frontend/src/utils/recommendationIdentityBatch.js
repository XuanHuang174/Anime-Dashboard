import { createLatestRequestGate, validateIdentityReview, recommendationError } from './recommendations.js'

export const MAX_IDENTITY_BATCH = 20
export function identitySeasonDiffers(original, candidate) {
  const normalize = value => {
    const text = (value || '').trim(), match = /^(\d{4})-(0?[1-9]|1[0-2])$/.exec(text)
    return match ? `${match[1]}-${Number(match[2])}` : text
  }
  return normalize(original) !== normalize(candidate)
}
export function validateIdentityBatch(data, request) {
  const rows = data?.results
  if (data?.contract_version !== 'recommendations.identity-batch.v1' || data.batch_id !== request.batch_id
    || !Array.isArray(rows) || rows.length !== request.items.length
    || new Set(rows.map(row => row.record_id)).size !== rows.length
    || rows.some(row => !request.items.some(item => item.record_id === row.record_id && item.bangumi_id === row.bangumi_id)
      || !['success', 'needs_review', 'retryable'].includes(row.outcome)
      || row.outcome === 'success' && (!['linked', 'already_linked', 'authoritative'].includes(row.code)
        || !Number.isInteger(row.version) || row.version < 0))
    || data.processed_count !== rows.length || data.success_count !== rows.filter(row => row.outcome === 'success').length
    || data.failed_count !== rows.length - data.success_count) throw new Error('批量关联确认合同不兼容，结果尚未确认。')
  return data
}
const sameMetadata = (a, b) => ['title', 'original_title', 'season', 'identity_version'].every(key => a[key] === b[key])
const sameCandidate = (a, b) => ['bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type'].every(key => a?.[key] === b?.[key])

// Page-local drafts only. No private browser storage; one bounded POST per batch.
export function createIdentityReviewController(client, scope = 'high-score', uuid = () => crypto.randomUUID()) {
  let state = { status: 'loading', page: 1, drafts: {}, searches: {}, keywords: {}, batch: null, mayHaveChanged: false }
  let active = true, pending = null
  const listeners = new Set(), pages = createLatestRequestGate(), searches = new Map()
  const emit = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  const locked = () => pending || ['uncertain', 'retryable'].includes(state.batch?.status)
  const editable = () => active && !locked()
  const visible = id => state.data?.results.some(row => row.record_id === id)
  async function loadPage(page = state.page, recordId = null) {
    if (!active || locked()) return
    const request = pages.begin()
    for (const gate of searches.values()) gate.cancel()
    emit({ status: 'loading', page, focusedId: recordId, error: '', searches: {} })
    try {
      const data = validateIdentityReview(await client.identities({ page, page_size: 5, scope,
        ...(recordId ? { record_id: recordId } : {}) }, request.signal))
      if (!active || !request.isCurrent()) return
      if (!data.results.length && page > 1 && !recordId) return loadPage(page - 1)
      const drafts = { ...state.drafts }
      for (const item of data.results) {
        const draft = drafts[item.record_id]
        if (!draft) continue
        const candidate = item.candidates.find(row => row.bangumi_id === draft.candidate.bangumi_id)
        if (sameMetadata(draft.item, item) && sameCandidate(draft.candidate, candidate) && !draft.error) {
          drafts[item.record_id] = { ...draft, item, candidate }
        } else drafts[item.record_id] = { ...draft, item, confirmed: false, error: '资料或候选已变化，请重新搜索并核对。' }
      }
      emit({ status: 'ready', data, drafts })
    } catch (error) { if (active && request.isCurrent()) emit({ status: 'error', error: recommendationError(error) }) }
  }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    getSnapshot: () => state,
    activate() { active = true },
    loadPage,
    setKeyword(id, value) { if (editable()) emit({ keywords: { ...state.keywords, [id]: value } }) },
    choose(item, candidate) {
      if (!editable() || state.searches[item.record_id]?.busy || !visible(item.record_id)) return
      if (!state.drafts[item.record_id] && Object.keys(state.drafts).length >= MAX_IDENTITY_BATCH) {
        emit({ message: '最多暂存 20 部，请先提交或移除部分选择。' }); return
      }
      emit({ drafts: { ...state.drafts, [item.record_id]: { item, candidate, confirmed: false, error: '' } }, message: '' })
    },
    confirm(id, confirmed) {
      const draft = state.drafts[id]
      if (!editable() || !draft || draft.error || state.searches[id]?.busy) return
      emit({ drafts: { ...state.drafts, [id]: { ...draft, confirmed } } })
    },
    remove(id) { if (editable()) { const drafts = { ...state.drafts }; delete drafts[id]; emit({ drafts }) } },
    clear() { if (editable()) emit({ drafts: {}, message: '' }) },
    async search(item) {
      if (!editable()) return
      const id = item.record_id, gate = searches.get(id) || createLatestRequestGate()
      searches.set(id, gate)
      const request = gate.begin(), drafts = { ...state.drafts }; delete drafts[id]
      emit({ drafts, searches: { ...state.searches, [id]: { busy: true } } })
      try {
        const data = validateIdentityReview(await client.identities({ scope, record_id: id,
          keyword: state.keywords[id] ?? item.title.slice(0, 100), page: 1 }, request.signal))
        const updated = data.results.find(row => row.record_id === id)
        if (!updated) throw new Error('记录已变化，请重新读取。')
        if (active && request.isCurrent() && visible(id)) emit({ data: { ...state.data,
          results: state.data.results.map(row => row.record_id === id ? updated : row) } })
      } catch (error) {
        if (active && request.isCurrent()) emit({ searches: { ...state.searches,
          [id]: { busy: false, error: error?.response?.status === 404 ? '记录已变化，请刷新待核对列表。' : recommendationError(error) } } })
      } finally {
        if (active && request.isCurrent() && state.searches[id]?.busy) emit({ searches: { ...state.searches, [id]: { busy: false } } })
      }
    },
    submit() {
      if (pending) return pending.promise
      if (!active) return Promise.resolve(null)
      const retry = ['uncertain', 'retryable'].includes(state.batch?.status)
      const payload = retry ? state.batch.payload : { batch_id: uuid(), items: Object.values(state.drafts)
        .filter(draft => draft.confirmed && !draft.error).map(({ item, candidate }) => ({ record_id: item.record_id,
          bangumi_id: candidate.bangumi_id, ticket: candidate.ticket, expected_version: item.identity_version, confirmed: true })) }
      if (!payload.items.length || Object.values(state.searches).some(row => row.busy)) return Promise.resolve(null)
      pages.cancel()
      const request = { controller: new AbortController() }; pending = request
      emit({ mayHaveChanged: true, batch: { status: 'sending', payload }, message: '' })
      request.promise = Promise.resolve().then(() => active && pending === request
        ? client.confirmIdentities(payload, request.controller.signal) : null).then(raw => {
        if (!active || pending !== request) return null
        const data = validateIdentityBatch(raw, payload), drafts = { ...state.drafts }
        for (const row of data.results) {
          if (row.outcome === 'success') delete drafts[row.record_id]
          else if (drafts[row.record_id]) drafts[row.record_id] = { ...drafts[row.record_id],
            confirmed: row.outcome === 'retryable', error: row.message || '请重新搜索候选并核对。' }
        }
        emit({ drafts, batch: { status: data.results.some(row => row.outcome === 'retryable') ? 'retryable' : 'result', payload, data } })
        return data
      }).catch(() => {
        if (active && pending === request) emit({ batch: { status: 'uncertain', payload,
          error: '本次提交结果尚未确认。请重试原批次，服务端会校验已完成项，不会重复关联。' } })
        return null
      }).finally(() => {
        if (pending !== request) return
        pending = null
        if (active && state.batch?.status === 'result') loadPage()
      })
      return request.promise
    },
    dispose() { active = false; pages.cancel(); for (const gate of searches.values()) gate.cancel(); pending?.controller.abort(); pending = null },
  }
}
