// Work-level feedback only. No journal edits, genre inference, or private persistence.
const versionOK = value => Number.isInteger(value) && value >= 0 && value <= 2147483647
const idOK = value => Number.isInteger(value) && value > 0 && value <= 2147483647
const dateOK = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
export function validateHiddenFeedback(data) {
  if (data?.contract_version !== 'recommendations.feedback.v1' || !versionOK(data.feedback_version)
    || !Number.isInteger(data.count) || data.count < 0 || !Number.isInteger(data.page) || data.page < 1
    || !Number.isInteger(data.page_size) || data.page_size < 1 || data.page_size > 24
    || !Array.isArray(data.results) || data.results.length > data.page_size || data.results.length > data.count
    || (data.next_page !== null && data.next_page !== data.page + 1)
    || !data.results.every(row => idOK(row.bangumi_id) && row.is_active === true
      && versionOK(row.feedback_version) && row.feedback_version > 0 && row.feedback_version <= data.feedback_version
      && dateOK(row.hidden_at) && ['title_zh', 'title_jp', 'season', 'status_type'].every(key => typeof row[key] === 'string'))
    || new Set(data.results.map(row => row.bangumi_id)).size !== data.results.length) {
    throw new Error('反馈数据合同不兼容，请重新读取。')
  }
  return data
}

export function validateFeedbackResult(data, action, body) {
  const row = data?.feedback
  if (data?.contract_version !== 'recommendations.feedback.v1' || data.action !== action
    || data.operation_id !== body.operation_id || !versionOK(data.feedback_version)
    || !versionOK(data.operation_feedback_version) || data.operation_feedback_version < 1
    || data.operation_feedback_version > data.feedback_version
    || data.feedback_version < body.expected_feedback_version
    || typeof data.changed !== 'boolean' || typeof data.replayed !== 'boolean'
    || (data.replayed && data.changed) || !row || row.bangumi_id !== body.bangumi_id
    || typeof row.is_active !== 'boolean' || !versionOK(row.feedback_version) || row.feedback_version < 1
    || row.feedback_version > data.feedback_version || !dateOK(row.hidden_at) || !dateOK(row.updated_at)
    || (data.changed && (data.feedback_version !== body.expected_feedback_version + 1
      || row.feedback_version !== data.feedback_version))
    || (!data.replayed && row.is_active !== (action === 'hide'))) {
    throw new Error('反馈响应未确认，请用原操作重试。')
  }
  return data
}

// Synchronous, shared by want and feedback. A render has not yet disabled all
// copies of a button when two click handlers run in the same task.
export function createRecommendationWriteGate({ isCurrent = () => true } = {}) {
  const owners = new Map()
  return {
    acquire(id) {
      if (!isCurrent() || owners.has(id)) return null
      const owner = Symbol(); owners.set(id, owner)
      return () => { if (owners.get(id) === owner) owners.delete(id) }
    },
    isCurrent,
  }
}

export function createFeedbackController(client, onConfirmed = () => {}, { writeGate, uuid = () => crypto.randomUUID() } = {}) {
  let state = { version: null, operations: {}, pending: false, toast: null }, disposed = false, epoch = 0
  let pending = null, uncertain = null
  const listeners = new Set()
  const emit = patch => { state = { ...state, ...patch }; for (const fn of listeners) fn() }
  const operation = (id, value) => emit({ operations: { ...state.operations, [id]: value } })
  function submit(action, item, targetVersion) {
    const id = item?.bangumiId
    if (disposed || !idOK(id)) return Promise.resolve(null)
    if (pending) return pending.id === id && pending.action === action ? pending.promise : Promise.resolve(null)
    if (uncertain && (uncertain.id !== id || uncertain.action !== action)) return Promise.resolve(null)
    if (!uncertain && !versionOK(state.version)) {
      operation(id, { status: 'conflict', message: '请先重新读取反馈状态，再确认本次操作。' })
      return Promise.resolve(null)
    }
    const release = writeGate?.acquire(id)
    if (writeGate && !release) return Promise.resolve(null)
    let body
    try {
      body = uncertain?.body || { bangumi_id: id, expected_feedback_version: state.version, operation_id: uuid(),
        ...(action === 'undo' ? { target_feedback_version: targetVersion } : {}) }
    } catch {
      release?.()
      operation(id, { status: 'error', message: '浏览器无法生成安全操作编号，请刷新或更换浏览器。' })
      return Promise.resolve(null)
    }
    const req = { id, action, item, body, controller: new AbortController(), epoch, release }
    pending = req
    const current = () => !disposed && req.epoch === epoch && pending === req && (!writeGate || writeGate.isCurrent())
    emit({ pending: true })
    operation(id, { status: 'saving', action, message: '正在处理…' })
    req.promise = Promise.resolve().then(() => current() ? (action === 'hide' ? client.hideFeedback : client.undoFeedback)(body, req.controller.signal) : null)
      .then(raw => {
        if (!current()) return null
        const result = validateFeedbackResult(raw, action, body)
        uncertain = null
        const toast = { key: body.operation_id, item, targetVersion: result.feedback.feedback_version,
          kind: result.feedback.is_active ? 'hidden' : 'undone', busy: false }
        emit({ version: Math.max(state.version || 0, result.feedback_version), toast, uncertain: false })
        operation(id, { status: 'ready', action })
        const syncFailed = () => { if (!disposed && req.epoch === epoch) emit({ syncError: true }) }
        try { Promise.resolve(onConfirmed(result)).catch(syncFailed) } catch { syncFailed() }
        return result
      }).catch(error => {
        if (!current()) return null
        const status = error?.response?.status
        const conflict = status === 409
        // Network/5xx/invalid acknowledgement: reuse the exact UUID AND payload.
        const unknown = !status || status >= 500
        uncertain = unknown ? req : null
        emit({ uncertain: unknown })
        operation(id, { status: conflict ? 'conflict' : 'error', action, uncertain: unknown,
          message: conflict ? '反馈已在另一处变化，请重新读取后再确认；本次没有覆盖新决定。'
            : unknown ? '反馈结果尚未确认，请重试原操作；不会重复写入。'
              : status === 429 ? '操作较频繁，请稍后重试。'
                : status === 404 ? '该作品或反馈已不可用，请重新读取。' : '反馈未保存，请重试。' })
        return null
      }).finally(() => {
        req.release?.()
        if (pending === req) { pending = null; if (!disposed) emit({ pending: false }) }
      })
    return req.promise
  }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    getSnapshot: () => state,
    activate() { disposed = false },
    observeVersion(version) { if (!disposed && versionOK(version) && (state.version === null || version > state.version)) emit({ version }) },
    hide: item => submit('hide', item),
    undo: (item, targetVersion) => submit('undo', item, targetVersion),
    retry() { return uncertain ? submit(uncertain.action, uncertain.item, uncertain.body.target_feedback_version) : Promise.resolve(null) },
    dismissToast(key) { if (state.toast?.key === key) emit({ toast: null }) },
    clearErrors() {
      if (!pending && !uncertain) emit({ operations: {}, syncError: false })
    },
    dispose() {
      disposed = true; epoch++; pending?.controller.abort(); pending?.release?.(); pending = null
      // StrictMode may reactivate this instance. Keep an uncertain operation's
      // UUID, but never retain a permanent busy flag after cancelling its owner.
      state = { ...state, pending: false }
    },
  }
}

export function createHiddenFeedbackController(client, observeVersion = () => {}, getVersion = () => 0) {
  let state = { status: 'loading' }, request, epoch = 0, disposed = false, active = false
  const listeners = new Set()
  const emit = value => { state = value; for (const fn of listeners) fn() }
  async function load(page = 1) {
    if (disposed) return
    const version = state.data?.feedback_version
    const minimumVersion = Math.max(version || 0, getVersion() || 0)
    const generation = ++epoch
    request?.abort(); request = new AbortController()
    emit({ status: 'loading', requestedPage: page }) // never show stale rows during revalidation
    try {
      const data = validateHiddenFeedback(await client.hiddenFeedback({ page, page_size: 12,
        ...(page > 1 ? { feedback_version: version } : {}) }, request.signal))
      if (disposed || generation !== epoch) return
      if (data.page !== page || data.feedback_version < Math.max(minimumVersion, getVersion() || 0)
        || (page > 1 && data.feedback_version !== version)) throw new Error('反馈分页已变化，请重新读取。')
      observeVersion(data.feedback_version)
      emit({ status: 'ready', data })
    } catch (error) {
      if (disposed || generation !== epoch) return
      emit({ status: 'error', message: error?.response?.status === 409 ? '隐藏清单已变化，请重新加载第一页。' : '隐藏清单加载失败，请重试。' })
    }
  }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) }, getSnapshot: () => state,
    open() { disposed = false; active = true; return load() },
    load,
    refresh() { if (active) return load() },
    close() { active = false; epoch++; request?.abort() },
    dispose() { disposed = true; active = false; epoch++; request?.abort() },
  }
}
