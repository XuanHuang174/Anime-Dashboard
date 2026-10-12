const STATUSES = ['plan', 'watching', 'completed', 'on_hold']
const LABELS = { plan: '已在想看', watching: '在看中', completed: '已看过', on_hold: '已搁置' }

export function validateWantResult(data, identity) {
  if (data?.contract_version !== 'recommendations.want.v1' || data.content_rating !== 'non_adult'
    || data.bangumi_id !== identity || !Number.isInteger(data.record_id) || data.record_id < 1
    || !Number.isInteger(data.anime_id) || data.anime_id < 1 || typeof data.created !== 'boolean'
    || data.outcome !== (data.created ? 'created' : 'existing') || !STATUSES.includes(data.watch_status)) {
    throw new Error('加入想看数据合同不兼容，请重试；已有手账不会被覆盖。')
  }
  return data
}

export function wantFailure(error) {
  const code = error?.response?.data?.code
  if (code === 'identity_confirmation_required') return { code, message: '手账可能已有这部作品。请先核对旧记录的原名、季度和版本，本次没有新增或合并。' }
  if (code === 'journal_field_conflict') return { code, message: '作品标题或资料超过手账字段长度，无法无损加入。请联系管理员；不会截断标题。' }
  if (code === 'public_identity_conflict') return { code, message: '公共资料的作品身份尚未明确或存在冲突，请联系管理员核对 Bangumi ID、原名与季度。本次未新增记录，不会仅按片名自动合并。' }
  // Compatibility during rolling backend deployment; do not mislabel a
  // private-only/shared hidden record as a removed or banned anime.
  if (code === 'work_unavailable') return { code, message: '当前服务暂不允许加入这部作品，请刷新页面后重试；若持续出现，请联系管理员。本次未新增记录。' }
  if (code === 'capacity_exceeded') return { code, message: '手账已达到容量上限，请先返回手账整理记录。' }
  if (error?.response?.status === 404) return { code: 'unavailable', message: '该作品已不可用于推荐，无法加入；请刷新推荐结果。' }
  if (error?.response?.status === 429) return { code: 'retry', message: '操作较频繁，请稍后重试。' }
  if (error?.response?.status === 503) return { code: 'retry', message: '推荐关联服务暂不可用，请稍后重试；不会覆盖已有手账。' }
  return { code: 'retry', message: error?.message?.startsWith('加入想看数据合同不兼容')
    ? error.message : '加入请求未确认，请重试。即使上次已成功，再次请求也不会创建重复记录。' }
}

export function wantButtonState(item, operation = {}) {
  const watchStatus = STATUSES.includes(item.watchStatus) ? item.watchStatus : operation.watchStatus
  if (LABELS[watchStatus]) return { label: LABELS[watchStatus], disabled: true }
  if (operation.status === 'adding') return { label: '正在加入…', disabled: true }
  if (operation.error?.code === 'identity_confirmation_required') return { label: '核对旧记录', disabled: false, confirm: true }
  if (operation.error && operation.error.code !== 'retry') return { label: '暂不可加入', disabled: true }
  return { label: operation.error ? '重试加入想看' : '+ 加入想看', disabled: false }
}

// One request per Bangumi identity across hero/grid/detail. No localStorage,
// optimistic false success, journal edit payload, or mock fallback.
export function createWantController(client) {
  let state = {}, disposed = false, epoch = 0
  const listeners = new Set(), pending = new Map()
  const update = (id, operation) => { state = { ...state, [id]: operation }; for (const listener of listeners) listener() }
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    activate() {
      if (!disposed) return
      disposed = false
      state = Object.fromEntries(Object.entries(state).filter(([,value]) => value.status !== 'adding'))
      for (const listener of listeners) listener()
    },
    resetFailures() {
      state = Object.fromEntries(Object.entries(state).filter(([,value]) => !value.error))
      for (const listener of listeners) listener()
    },
    add(item) {
      const id = item.bangumiId
      if (disposed || !Number.isInteger(id) || id < 1 || id > 2147483647) return Promise.resolve(null)
      if (pending.has(id)) return pending.get(id).promise
      if (STATUSES.includes(item.watchStatus) || state[id]?.status === 'ready') return Promise.resolve(null)
      const request = { controller: new AbortController(), epoch }
      const current = () => !disposed && request.epoch === epoch && pending.get(id) === request
      pending.set(id, request)
      request.promise = Promise.resolve().then(() => current() ? client.want(id, request.controller.signal) : null).then((raw) => {
        if (!current()) return null
        const data = validateWantResult(raw, id)
        update(id, { status: 'ready', watchStatus: data.watch_status, recordId: data.record_id })
        return data
      }).catch((error) => {
        if (current()) update(id, { status: 'error', error: wantFailure(error) })
        return null
      }).finally(() => { if (pending.get(id) === request) pending.delete(id) })
      update(id, { status: 'adding' })
      return request.promise
    },
    dispose() {
      disposed = true; epoch += 1
      for (const request of pending.values()) request.controller.abort()
      pending.clear()
    },
  }
}
