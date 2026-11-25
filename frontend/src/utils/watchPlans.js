import { datePart, formatWatchDate, validWatchDate } from './watchHistory.js'

export const PLAN_PRIORITIES = { 1: '优先看', 2: '正常安排', 3: '暂不着急' }
export const PLAN_STATES = { pending: '待开始', ongoing: '进行中', completed: '已完成', archived: '已移出' }
export const PLAN_PURPOSES = { first: '首次观看', continue: '继续观看', resume: '恢复观看', rewatch: '准备重看' }
const statuses = ['plan', 'watching', 'completed', 'on_hold']
const natural = n => Number.isSafeInteger(n) && n >= 0
export const planDateLabel = value => value ? formatWatchDate(value) : '时间未定'
export function plannedTimePassed(value, today = new Date()) {
  if (!value || !validWatchDate(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const end = new Date(year, month ? month - 1 : 11, day ?? (month ? new Date(year, month, 0).getDate() : 31), 23, 59, 59, 999)
  return end < today
}
export function planForm(plan, version) {
  return { version, action: plan && ['pending', 'ongoing'].includes(plan.state) ? 'update' : 'enroll', priority: plan?.priority ?? 2, planned: datePart(plan?.planned_for || ''), note: plan?.note || '' }
}
export function dateValue(part) {
  if (part.precision === 'unknown') return ''
  if (!validWatchDate(part.value) || part.value.split('-').length !== { year: 1, month: 2, day: 3 }[part.precision]) throw new Error('请填写有效日期，或将精度改为“未定”。')
  return part.value
}
export function buildPlanCommand(form) {
  if (!Object.hasOwn(PLAN_PRIORITIES, form.priority) || typeof form.priority !== 'number') throw new Error('请选择有效优先级。')
  if (form.note.length > 500) throw new Error('计划备注最多 500 字。')
  return { action: form.action, version: form.version, priority: form.priority, planned_for: dateValue(form.planned), note: form.note.trim() }
}
export function validPlan(plan) {
  return plan === null || Boolean(plan && plan.version === 1 && Number.isInteger(plan.priority) && Object.hasOwn(PLAN_PRIORITIES, plan.priority) && natural(plan.position)
    && Object.hasOwn(PLAN_STATES, plan.state) && Object.hasOwn(PLAN_PURPOSES, plan.purpose)
    && typeof plan.planned_for === 'string' && (!plan.planned_for || validWatchDate(plan.planned_for)) && typeof plan.note === 'string' && plan.note.length <= 500
    && (plan.target_round_number === null || Number.isSafeInteger(plan.target_round_number) && plan.target_round_number > 0)
    && Number.isSafeInteger(plan.anchor_round_number) && plan.anchor_round_number > 0 && natural(plan.history_base) && Number.isFinite(Date.parse(plan.enrolled_at)))
}
export function validPlanRecord(record) {
  return Boolean(record && Number.isSafeInteger(record.id) && record.id > 0 && typeof record.title === 'string' && typeof record.japanese_title === 'string'
    && typeof record.cover === 'string' && statuses.includes(record.watch_status) && natural(record.watch_revision) && natural(record.watched_eps) && natural(record.total_eps))
}
export function validPlanDetail(data, id) {
  return Boolean(data?.contract_version === 'journal.watch-plan.v1' && natural(data.version) && validPlanRecord(data.record) && data.record.id === id && validPlan(data.plan))
}
export function validPlanAcknowledgement(data, id) {
  const record = data?.canonical_record
  return validPlanDetail(data, id) && Boolean(record && record.id === id && record.watch_revision === data.record.watch_revision
    && record.watch_status === data.record.watch_status && record.watched_eps === data.record.watched_eps && record.total_eps === data.record.total_eps
    && typeof record.anime?.chinese_title === 'string' && typeof record.anime?.japanese_title === 'string' && typeof record.review === 'string')
}
export function validPlanQueue(data) {
  return Boolean(data?.contract_version === 'journal.watch-plans.v1' && natural(data.version) && natural(data.count) && Number.isInteger(data.page) && data.page > 0
    && Number.isInteger(data.page_size) && data.page_size > 0 && data.page_size <= 100 && data.counts_scope === 'owner_all_plans'
    && Object.keys(PLAN_STATES).every(key => natural(data.counts?.[key])) && Array.isArray(data.results) && data.results.length <= data.count && data.results.length <= data.page_size
    && data.results.every(row => validPlanRecord(row.record) && row.plan !== null && validPlan(row.plan)) && new Set(data.results.map(row => row.record.id)).size === data.results.length)
}
export function validPlanOrder(data) {
  return Boolean(data?.contract_version === 'journal.watch-plan-order.v1' && natural(data.version) && Number.isInteger(data.priority) && Object.hasOwn(PLAN_PRIORITIES, data.priority)
    && Array.isArray(data.record_ids) && data.record_ids.every(id => Number.isSafeInteger(id) && id > 0) && new Set(data.record_ids).size === data.record_ids.length)
}
export function validPlanBatch(data, ids) {
  return Boolean(data?.contract_version === 'journal.watch-plan-batch.v1' && natural(data.version)
    && Array.isArray(data.record_ids) && JSON.stringify([...ids].sort((a,b) => a-b)) === JSON.stringify(data.record_ids)
    && Array.isArray(data.results) && data.results.length === ids.length && new Set(data.results.map(row => row.record?.id)).size === data.results.length
    && data.results.every(row => ids.includes(row.record?.id) && validPlanRecord(row.record) && row.plan !== null && validPlan(row.plan)))
}
export function movePlan(ids, source, target) {
  const from = ids.indexOf(source), to = ids.indexOf(target)
  if (from < 0 || to < 0 || from === to) return [...ids]
  const next = ids.filter(id => id !== source); next.splice(to, 0, source); return next
}

export async function loadCompletePlanGroup(client, priority, signal) {
  const rows = []; let version, count
  for (let page = 1; ; page++) {
    const { data } = await client.get('watch-plans/', { params: { page, page_size: 100, state: 'active', priority, sort: 'manual' }, signal })
    if (!validPlanQueue(data) || data.page !== page || data.results.some(row => row.plan.priority !== priority || !['pending', 'ongoing'].includes(row.plan.state))) throw new Error('完整排序队列返回不完整，请重新读取。')
    if (version === undefined) { version = data.version; count = data.count }
    if (version !== data.version || count !== data.count) throw new Error('读取过程中队列已变化，请重新读取完整队列。')
    rows.push(...data.results)
    if (rows.length >= count) break
    if (!data.results.length || rows.length > 10000) throw new Error('完整排序队列超出可读取范围。')
  }
  if (rows.length !== count || new Set(rows.map(row => row.record.id)).size !== count) throw new Error('队列存在重复或缺失，请重新读取。')
  return { version, rows }
}

export function planError(error) {
  const data = error?.response?.data
  return typeof data?.detail === 'string' ? data.detail : data && typeof data === 'object' ? Object.values(data).flat(Infinity).filter(v => typeof v === 'string').join('；') || '请求未完成，请主动重试。' : error?.message || '连接暂时中断。'
}
export function createPlanController({ client, recordId = null, getSession, onCommitted, uuid = () => crypto.randomUUID() }) {
  const owner = getSession(), listeners = new Set()
  let alive = true, serial = 0, gate = false, abort = null, minimumVersion = 0, params = { page: 1, page_size: 20, state: 'active', sort: 'manual' }
  let snapshot = { data: null, status: 'idle', error: '', pending: null, conflict: false, needsRead: false }
  const current = () => alive && owner === getSession()
  const emit = patch => { if (current()) { snapshot = { ...snapshot, ...patch }; listeners.forEach(fn => fn()) } }
  const engine = {
    getSnapshot: () => snapshot,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    activate() { if (!alive) { alive = true; snapshot = { data: null, status: 'idle', error: '', pending: null, conflict: false, needsRead: false } } },
    dispose() { alive = false; serial++; abort?.abort(); gate = false },
    async read(options = {}) {
      if (!current() || gate) return
      params = { ...params, ...options }
      const run = ++serial; abort?.abort(); abort = new AbortController()
      emit({ status: 'loading', error: '' })
      try {
        const { data } = await client.get(recordId ? `records/${recordId}/watch-plan/` : 'watch-plans/', { ...(recordId ? {} : { params: { ...params } }), signal: abort.signal })
        if (!(recordId ? validPlanDetail(data, recordId) : validPlanQueue(data))) throw new Error('计划返回不完整，请重新读取。')
        if (data.version < minimumVersion) throw new Error('读取到较旧版本，请重新读取计划。')
        if (current() && run === serial) { minimumVersion = data.version; emit({ data, status: 'ready', error: '', conflict: false, needsRead: false }) }
      } catch (error) { if (current() && run === serial) emit({ status: 'error', error: planError(error) }) }
    },
    async submit(command, retry = false) {
      if (!current() || gate || snapshot.status === 'loading' || snapshot.conflict || !snapshot.data) return false
      if (!retry && (snapshot.pending || snapshot.status !== 'ready' || snapshot.needsRead)) return false
      const pending = retry ? snapshot.pending : { path: recordId ? `records/${recordId}/watch-plan/` : command.action ? 'watch-plans/batch/' : 'watch-plans/reorder/', body: { ...command, operation_id: uuid() } }
      if (!pending) return false
      gate = true; const run = ++serial; abort?.abort(); abort = new AbortController()
      emit({ status: 'saving', error: '', pending })
      try {
        const { data } = await client.post(pending.path, pending.body, { signal: abort.signal })
        if (data?.version < minimumVersion || data?.version <= pending.body.version || (recordId ? !validPlanAcknowledgement(data, recordId) : pending.body.action ? !validPlanBatch(data, pending.body.record_ids) : !validPlanOrder(data) || data.priority !== pending.body.priority)) throw new Error('操作结果尚未完整读取，请重试原操作确认结果。')
        if (!current() || run !== serial) return false
        minimumVersion = data.version
        emit({ ...(recordId ? { data } : {}), status: 'ready', error: '', pending: null, conflict: false, needsRead: !recordId })
        try { onCommitted?.(data, pending.body) } catch { emit({ error: '计划已保存，页面同步暂未完成，请重新读取。' }) }
        return true
      } catch (error) {
        if (!current() || run !== serial) return false
        const code = error?.response?.status
        emit({ status: [400, 422, 429].includes(code) ? 'ready' : 'error', error: planError(error), pending: !code || code >= 500 ? pending : null, conflict: code === 409 })
        return false
      } finally { if (serial === run) gate = false }
    },
  }
  return engine
}
