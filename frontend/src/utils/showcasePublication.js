export const PUBLICATION_VERSION = 'showcase.publication.v1'

export function validatePublicationList(data) {
  if (data?.contract_version !== PUBLICATION_VERSION || typeof data.can_publish !== 'boolean'
    || !Number.isInteger(data.count) || data.count < 0 || !Array.isArray(data.results)
    || data.results.length > 12 || data.results.length > data.count
    || !(data.next_page === null || Number.isInteger(data.next_page) && data.next_page > 1)
    || (!data.can_publish && (data.count || data.results.length))) throw new Error('公共库确认合同不兼容，请重新读取。')
  const ids = new Set()
  for (const item of data.results) {
    if (!Number.isInteger(item.record_id) || item.record_id < 1 || ids.has(item.record_id)
      || !Number.isInteger(item.anime_id) || item.anime_id < 1 || typeof item.title !== 'string'
      || typeof item.season !== 'string' || typeof item.detail !== 'string' || typeof item.requires_confirmation !== 'boolean'
      || (item.requires_confirmation ? typeof item.ticket !== 'string' || !item.ticket : item.ticket !== null)) {
      throw new Error('公共库确认合同不兼容，请重新读取。')
    }
    ids.add(item.record_id)
  }
  return data
}

export function createPublicationClient(api) {
  return {
    pending: (page, signal) => api.get('records/showcase-publication/', { params: { page }, signal }).then(r => r.data),
    publish: (item, signal) => api.post(`records/${item.record_id}/publish-showcase/`, {
      confirm_publication: true, ticket: item.ticket,
    }, { signal }).then(r => r.data),
  }
}

// Read/confirmation sessions are independent of recommendation cursors and writes.
export function createPublicationController(client, isCurrent = () => true) {
  let state = { canPublish: null, items: [], count: 0, page: 1, nextPage: null, loading: false, busy: null, error: '', saved: false }
  let disposed = false, generation = 0, readRequest, writeRequest
  const listeners = new Set()
  const update = changes => { state = { ...state, ...changes }; for (const fn of listeners) fn() }
  const live = () => !disposed && isCurrent()
  async function read(page = 1, { afterSave = false } = {}) {
    if (!live() || state.busy || state.canPublish === false) return
    readRequest?.abort()
    const request = new AbortController(), epoch = ++generation
    readRequest = request
    update({ loading: true, error: '' })
    try {
      const data = validatePublicationList(await client.pending(page, request.signal))
      if (!live() || epoch !== generation) return
      update({ canPublish: data.can_publish, items: data.results, count: data.count, page,
        nextPage: data.next_page, loading: false, saved: afterSave || state.saved })
    } catch (error) {
      if (!live() || epoch !== generation) return
      if ([401, 404].includes(error?.response?.status) && state.canPublish === null) {
        update({ canPublish: false, loading: false }); return // old backend / signed-out rolling transition
      }
      update({ loading: false, error: afterSave ? '公开已完成，列表更新失败。请重新读取，不必再次提交。'
        : '公共库待公开列表读取失败，请重试。', saved: afterSave || state.saved })
    }
  }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    activate() { disposed = false }, read,
    publish(item) {
      if (!live() || state.busy || state.loading || state.error || !state.canPublish || !item.requires_confirmation
        || !state.items.some(row => row === item)) return Promise.resolve(null)
      readRequest?.abort(); generation += 1
      const request = new AbortController(), epoch = generation
      writeRequest = request
      update({ busy: item.record_id, error: '', saved: false })
      return Promise.resolve().then(() => live() && epoch === generation ? client.publish(item, request.signal) : null).then(async result => {
        if (!live() || epoch !== generation) return null
        if (result?.contract_version !== PUBLICATION_VERSION || result.record_id !== item.record_id
          || result.anime_id !== item.anime_id || !['published', 'already_public'].includes(result.status)) throw new Error('invalid acknowledgement')
        update({ busy: null, items: state.items.filter(row => row.record_id !== item.record_id),
          count: Math.max(0, state.count - 1), saved: true })
        await read(1, { afterSave: true })
        return result
      }).catch(error => {
        if (live() && epoch === generation) update({ busy: null, error: error?.response?.status === 409
          ? '资料或确认凭据已变化，请重新读取列表后再次确认。'
          : error?.response?.status === 403 ? '该作品不能通过此入口公开，请重新读取或联系管理员。'
            : '公开请求尚未确认，请先重新读取列表。已成功的公开不会重复执行。' })
        return null
      })
    },
    dispose() { disposed = true; generation += 1; readRequest?.abort(); writeRequest?.abort() },
  }
}
