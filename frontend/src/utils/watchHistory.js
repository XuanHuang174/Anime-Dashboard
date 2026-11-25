export const WATCH_LABELS = { plan: '想看', watching: '在看', completed: '看过', on_hold: '搁置' }
export const SOURCE_LABELS = { record_update: '手账更新', history_command: '历程操作', collection_import: '收藏导入', backup_restore: '备份恢复' }
export const ACTION_LABELS = { change: '更新观看记录', transition: '调整状态与进度', 'start-rewatch': '开始重看', 'update-round': '更正轮次', note: '写下随记', memory: '补录往事', undo: '撤销最近操作' }

export function datePart(value = '') {
  return { precision: value ? ['year', 'month', 'day'][value.split('-').length - 1] : 'unknown', value }
}
export function validWatchDate(value) {
  if (!/^[1-9]\d{3}(?:-\d{2}(?:-\d{2})?)?$/.test(value)) return false
  const [y, m, d] = value.split('-').map(Number)
  if (m !== undefined && (m < 1 || m > 12)) return false
  return d === undefined || d >= 1 && d <= new Date(Date.UTC(y, m, 0)).getUTCDate()
}
export function formatWatchDate(value) {
  if (!value) return '日期未记录'
  if (!validWatchDate(value)) return value
  const [year, month, day] = value.split('-')
  return `${year}年${month ? `${Number(month)}月` : ''}${day ? `${Number(day)}日` : ''}`
}
export function watchProgress(row) { return `${row.watched_eps} / ${row.total_eps || '?'} 集` }
export function eventWatchDate(event) { return event.occurred_on || (event.kind === 'start-rewatch' ? event.after.round.started_on : '') }
export function watchEventSummary(event) {
  const before = event.before.record, after = event.after.record, parts = []
  if (before.watch_status !== after.watch_status) parts.push(`${WATCH_LABELS[before.watch_status]} → ${WATCH_LABELS[after.watch_status]}`)
  if (before.watched_eps !== after.watched_eps || before.total_eps !== after.total_eps) parts.push(`进度 ${watchProgress(before)} → ${watchProgress(after)}`)
  if (event.kind === 'update-round') {
    for (const [key, label] of [['started_on', '开始日期'], ['completed_on', '完成日期']]) {
      if (event.before.round[key] !== event.after.round[key]) parts.push(`${label}：${formatWatchDate(event.before.round[key])} → ${formatWatchDate(event.after.round[key])}`)
    }
    if (event.before.round.rating !== event.after.round.rating) parts.push(`轮次评分：${event.before.round.rating ?? '未记录'} → ${event.after.round.rating ?? '未记录'}`)
    if (event.before.round.note !== event.after.round.note) parts.push(`第 ${event.after.round.number} 轮备注已更新`)
  }
  return parts.join('；') || (event.kind === 'memory' ? '补充过去的回忆，不改变当前进度' : event.kind === 'undo' ? '保留操作轨迹，恢复此前观看信息' : '当前观看进度保持不变')
}
export function initialWatchForm(action, current, version, status) {
  return { action, version, round_number: current.number, watch_status: status || current.status,
    watched_eps: String(current.watched_eps), total_eps: String(current.total_eps),
    started_on: datePart(action === 'update-round' ? current.started_on : ''),
    completed_on: datePart(current.completed_on), occurred_on: datePart(),
    rating: action === 'update-round' && current.rating !== null ? String(current.rating) : '',
    note: action === 'update-round' ? current.note : '' }
}
export function buildWatchCommand(form) {
  const dates = {}
  const selected = form.action === 'update-round' ? ['started_on', 'completed_on'] : form.action === 'start-rewatch' ? ['started_on'] : ['occurred_on']
  for (const key of selected) {
    const part = form[key]
    const value = part.precision === 'unknown' ? '' : part.value
    if (part.precision !== 'unknown' && (!validWatchDate(value) || value.split('-').length !== { year: 1, month: 2, day: 3 }[part.precision])) throw new Error('请填写有效日期，或将日期精度改为“未记录”。')
    dates[key] = value
  }
  const body = { action: form.action, version: form.version, note: form.note.trim(), ...dates }
  if (['note', 'memory'].includes(form.action) && !body.note) throw new Error('请先写下随记内容。')
  if (form.action === 'memory' && !dates.occurred_on) throw new Error('补录往事需要填写实际日期或大致年月。')
  if (form.action === 'transition') {
    for (const key of ['watched_eps', 'total_eps']) {
      if (!/^\d+$/.test(form[key]) || !Number.isSafeInteger(Number(form[key]))) throw new Error('观看集数请填写非负整数。')
      body[key] = Number(form[key])
    }
    if (body.total_eps && body.watched_eps > body.total_eps) throw new Error('已看集数不能超过总集数。')
    body.watch_status = form.watch_status
  }
  if (form.action === 'update-round') {
    body.round_number = form.round_number
    if (form.rating !== '' && (!/^\d+(?:\.\d)?$/.test(form.rating) || Number(form.rating) > 10)) throw new Error('轮次评分请填写 0–10 分，最多一位小数；也可留空。')
    body.rating = form.rating === '' ? null : Number(form.rating)
  }
  return body
}

export function validWatchHistory(data) {
  const validState = row => row && Object.hasOwn(WATCH_LABELS, row.watch_status) && Number.isSafeInteger(row.watched_eps) && row.watched_eps >= 0 && Number.isSafeInteger(row.total_eps) && row.total_eps >= 0
  const validRound = row => row && Number.isInteger(row.number) && row.number > 0 && validState({ ...row, watch_status: row.status }) && typeof row.is_current === 'boolean' && typeof row.voided === 'boolean'
    && ['started_on', 'completed_on'].every(key => typeof row[key] === 'string' && (!row[key] || validWatchDate(row[key]))) && typeof row.note === 'string'
  const validEvent = e => e && Number.isInteger(e.revision) && e.revision > 0 && e.revision <= data.version && validState(e.before?.record) && validState(e.after?.record) && validRound(e.before?.round) && validRound(e.after?.round)
    && Object.hasOwn(ACTION_LABELS, e.kind) && typeof e.note === 'string' && typeof e.occurred_on === 'string' && (!e.occurred_on || validWatchDate(e.occurred_on)) && Number.isFinite(Date.parse(e.recorded_at))
  return data?.contract_version === 'journal.watch-history.v1' && Number.isSafeInteger(data.version) && data.version >= 0
    && Number.isSafeInteger(data.count) && data.count >= 0 && Number.isInteger(data.page) && data.page > 0
    && Number.isInteger(data.page_size) && data.page_size > 0 && Array.isArray(data.rounds) && data.rounds.length > 0
    && data.rounds.filter(row => row.is_current && !row.voided).length === 1
    && data.rounds.every(validRound) && new Set(data.rounds.map(row => row.number)).size === data.rounds.length
    && Array.isArray(data.events) && data.events.length <= data.count && data.events.every(e => validEvent(e) && (!e.undone_event || e.kind === 'undo' && validEvent(e.undone_event) && e.undone_event.revision === e.revision - 1 && !e.undone_event.undone_event))
}

const errorText = error => {
  const data = error?.response?.data
  return typeof data?.detail === 'string' ? data.detail : data && typeof data === 'object' ? Object.values(data).flat(Infinity).filter(v => typeof v === 'string').join('；') || '请求未完成，请主动重试。' : error.message || '连接暂时中断。'
}
export function createWatchHistoryController({ client, recordId, getSession, onCommitted, uuid = () => globalThis.crypto.randomUUID() }) {
  const owner = getSession(), listeners = new Set()
  let alive = true, serial = 0, controller = null, gate = false
  let view = { page: 1, page_size: 5, category: 'all', compact: 'true' }
  let snapshot = { data: null, status: 'idle', error: '', pending: null, conflict: false }
  const current = () => alive && owner === getSession()
  const emit = patch => { if (current()) { snapshot = { ...snapshot, ...patch }; listeners.forEach(fn => fn()) } }
  const engine = {
    getSnapshot: () => snapshot,
    subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn) },
    activate() { if (!alive) { alive = true; gate = false; snapshot = { data: null, status: 'idle', error: '', pending: null, conflict: false } } },
    dispose() { alive = false; serial++; controller?.abort(); gate = false },
    async read(page = view.page, options = {}) {
      if (!current() || gate) return
      view = { ...view, ...options, page }
      const run = ++serial; controller?.abort(); controller = new AbortController()
      emit({ status: 'loading', error: '' })
      try {
        const { data } = await client.get(`records/${recordId}/watch-history/`, { params: { ...view }, signal: controller.signal })
        if (!validWatchHistory(data)) throw new Error('历程返回不完整，请重新读取。')
        if (current() && run === serial) emit({ data, status: 'ready', error: '', conflict: false })
      } catch (error) { if (current() && run === serial) emit({ status: 'error', error: errorText(error) }) }
    },
    async submit(command, retry = false) {
      if (!current() || gate || snapshot.status === 'loading' || snapshot.conflict || !snapshot.data) return false
      if (!retry && (snapshot.pending || snapshot.status !== 'ready')) return false
      const body = retry ? snapshot.pending : { ...command, operation_id: uuid() }
      if (!body) return false
      gate = true; const run = ++serial; controller?.abort(); controller = new AbortController()
      emit({ status: 'saving', error: '', pending: body })
      try {
        const { data } = await client.post(`records/${recordId}/watch-history/`, body, { signal: controller.signal })
        if (!validWatchHistory(data) || data.record?.id !== recordId || data.record.watch_revision !== data.version) throw new Error('操作结果尚未完整读取，请重试原操作确认结果。')
        if (!current() || run !== serial) return false
        emit({ data, status: 'ready', error: '', pending: null, conflict: false })
        try { onCommitted?.(data.record) } catch { emit({ error: '历程已保存，页面资料暂未同步，请重新打开详情。' }) }
        return true
      } catch (error) {
        if (!current() || run !== serial) return false
        const code = error?.response?.status
        const uncertain = !code || code >= 500
        emit({ status: [400, 422, 429].includes(code) ? 'ready' : 'error', error: errorText(error), pending: uncertain ? body : null, conflict: code === 409 })
        return false
      } finally { if (run === serial) gate = false }
    },
  }
  return engine
}
