import { validPlanRecord, planError } from './watchPlans.js'

export const curationError = planError
export const isCurationId = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
const natural = value => Number.isSafeInteger(value) && value >= 0
const text = (value, max) => typeof value === 'string' && Array.from(value).length <= max
const record = value => validPlanRecord(value) && (value.personal_score === null || typeof value.personal_score === 'string' && value.personal_score.trim() !== '' && Number.isFinite(Number(value.personal_score)) && Number(value.personal_score) >= 0 && Number(value.personal_score) <= 10)
export function validCuration(value) {
  return Boolean(value && isCurationId(value.id) && text(value.title, 80) && value.title.trim() && text(value.description, 2000)
    && typeof value.pinned === 'boolean' && typeof value.archived === 'boolean' && natural(value.position)
    && (value.cover_entry_id === null || isCurationId(value.cover_entry_id)) && (value.cover === null || record(value.cover))
    && natural(value.entry_count) && value.entry_count <= 500 && natural(value.unavailable_count) && value.unavailable_count <= value.entry_count)
}
export function validCurationEntry(value) {
  return Boolean(value && isCurationId(value.id) && natural(value.position) && text(value.reason, 2000)
    && text(value.title_snapshot?.title, 200) && text(value.title_snapshot?.original_title, 200) && text(value.title_snapshot?.period, 32)
    && typeof value.unavailable === 'boolean' && (value.unavailable ? value.record === null : record(value.record)))
}
export function validCurationPage(data, { listId = null, page = 1, page_size: size = 12, version } = {}) {
  return Boolean(data?.contract_version === 'journal.curations.v1' && natural(data.version) && (version === undefined || data.version === version)
    && data.page === page && data.page_size === size && natural(data.count) && data.count <= (listId ? 500 : 100)
    && data.next_page === (page * size < data.count ? page + 1 : null) && Array.isArray(data.results)
    && data.results.length === Math.min(size, Math.max(0, data.count - (page-1)*size))
    && (listId ? validCuration(data.collection) && data.collection.id === listId
      && (data.total_count === undefined ? data.collection.entry_count === data.count : natural(data.total_count) && data.collection.entry_count === data.total_count && data.count <= data.total_count)
      && data.results.every(validCurationEntry)
      : data.collection === undefined && data.results.every(validCuration))
    && new Set(data.results.map(row => row.id)).size === data.results.length
    && (!listId || new Set(data.results.filter(row => row.record).map(row => row.record.id)).size === data.results.filter(row => row.record).length))
}
export function validCurationReceipt(data, body) {
  return Boolean(data?.contract_version === 'journal.curations.v1' && natural(data.version) && data.version > body.version
    && typeof data.replayed === 'boolean' && isCurationId(data.list_id) && (['create', 'create-with-members', 'create-from-review'].includes(body.action) || data.list_id === body.list_id)
    && (data.collection === null ? body.action === 'delete' || data.replayed : validCuration(data.collection) && data.collection.id === data.list_id))
}
export function changedCurationFields(base, form) {
  return Object.fromEntries(Object.keys(base).filter(key => base[key] !== form[key]).map(key => [key, key === 'cover_entry_id' ? form[key] || null : form[key]]))
}
export function moveCuration(ids, id, position) {
  if (!ids.includes(id) || !Number.isInteger(position) || position < 0 || position >= ids.length) return [...ids]
  const next = ids.filter(value => value !== id); next.splice(position, 0, id); return next
}

export async function loadCompleteCuration(client, listId, version, signal, current = () => true) {
  const rows = []; let collection, count
  for (let page = 1; ; page++) {
    if (signal.aborted || !current()) throw new Error('片单读取已取消。')
    const { data } = await client.get(`curations/${listId}/`, { params: { page, page_size: 100, version }, signal })
    if (signal.aborted || !current()) throw new Error('片单读取已取消。')
    if (!validCurationPage(data, { listId, page, page_size: 100, version })) throw new Error('片单返回不完整，请重新读取。')
    if (data.collection.entry_count !== data.count) throw new Error('完整片单读取不能只包含筛选结果。')
    if (!collection) { collection = data.collection; count = data.count }
    if (JSON.stringify(collection) !== JSON.stringify(data.collection) || data.count !== count) throw new Error('读取过程中片单已变化，请重新读取。')
    rows.push(...data.results)
    if (!data.next_page) break
  }
  if (rows.length !== count || new Set(rows.map(row => row.id)).size !== count
    || new Set(rows.filter(row => row.record).map(row => row.record.id)).size !== rows.filter(row => row.record).length) throw new Error('完整片单存在重复或缺失。')
  return { collection, rows, version }
}

export function createCurationController({ client, getSession, uuid = () => crypto.randomUUID() }) {
  const owner = getSession(), listeners = new Set()
  let alive = true, serial = 0, abort, gate = false, minimumVersion = 0, params = { listId: null, page: 1, page_size: 12, state: 'active' }
  let state = { data: null, status: 'idle', error: '', notice: '', pending: null, conflict: false, needsRead: false }
  const current = () => alive && owner === getSession()
  const emit = patch => { if (current()) { state = { ...state, ...patch }; listeners.forEach(fn => fn()) } }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    activate() { if (!alive) state = { ...state, data: null, status: 'idle', error: '', needsRead: false }; alive = true }, dispose() { alive = false; serial++; abort?.abort(); gate = false },
    async read(options = {}) {
      if (!current() || gate || state.pending) return false
      params = { ...params, ...options }
      if (params.listId) params = Object.fromEntries(['listId','page','page_size','version','search','status','tag','year','availability'].map(key=>[key,params[key]]))
      const run = ++serial; abort?.abort(); const controller = new AbortController(); abort = controller
      emit({ status: 'loading', error: '', notice: '' })
      let query = { ...params }, notice = ''
      for (let attempt = 0; attempt < 2; attempt++) {
        const { listId, ...request } = query
        if (request.page > 1 && request.version === undefined) request.version = minimumVersion
        try {
          const { data } = await client.get(listId ? `curations/${listId}/` : 'curations/', { params: request, signal: controller.signal })
          if (!current() || run !== serial) return false
          if (!validCurationPage(data, { listId, ...request }) || data.version < minimumVersion) throw new Error('片单返回不完整或版本较旧，请重新读取。')
          if (!data.results.length && data.page > 1 && attempt === 0) { query = { ...query, page: Math.max(1, Math.ceil(data.count / data.page_size)), version: data.version }; continue }
          minimumVersion = data.version; params = { ...query, page: data.page, version: undefined }
          emit({ data, status: 'ready', error: '', notice, conflict: false, needsRead: false }); return true
        } catch (error) {
          if (!current() || run !== serial) return false
          if (error.response?.status === 409 && attempt === 0) { query = { ...query, page: 1, version: undefined }; notice = '片单已有更新，已重新读取最新第一页。'; continue }
          emit({ data: null, status: 'error', error: curationError(error), needsRead: true }); return false
        }
      }
      return false
    },
    async submit(command, retry = false) {
      if (!current() || gate || state.status === 'loading') return null
      if (!retry && (state.pending || state.status !== 'ready' || state.conflict || state.needsRead)) return null
      const body = retry ? state.pending : { ...command, operation_id: uuid() }
      if (!body) return null
      gate = true; const run = ++serial; abort?.abort(); abort = new AbortController()
      emit({ status: 'saving', error: '', pending: body })
      try {
        const { data } = await client.post('curations/', body, { signal: abort.signal })
        if (!current() || run !== serial) return null
        if (!validCurationReceipt(data, body) || data.version < minimumVersion) throw new Error('保存结果尚未完整确认，请重试原操作核对。')
        minimumVersion = data.version
        emit({ status: 'ready', error: '', pending: null, conflict: false, needsRead: true }); return { data, body }
      } catch (error) {
        if (!current() || run !== serial) return null
        const code = error.response?.status, uncertain = !code || code >= 500
        emit({ status: uncertain || code === 409 || code === 404 ? 'error' : 'ready', error: curationError(error),
          pending: uncertain ? body : null, conflict: code === 409 || code === 404, needsRead: code === 409 || code === 404 })
        return null
      } finally { if (run === serial) gate = false }
    },
  }
}
