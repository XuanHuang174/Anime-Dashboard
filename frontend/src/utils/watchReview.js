import { validWatchDate, formatWatchDate } from './watchHistory.js'

export const REVIEW_SECTIONS = { works: '全部作品', completed: '完成轮次', started: '开始观看', rewatch: '重看完成', activity: '观看足迹', notes: '随记与补录', uncertain: '可能相关', undated: '日期待补充' }
export const REVIEW_KINDS = { started: '开始观看', completed: '完成观看', progress: '记录进度', pause: '暂停观看', resume: '恢复观看', note: '随记', memory: '补录往事' }
const natural = n => Number.isSafeInteger(n) && n >= 0
const score = n => typeof n === 'string' && n.trim() !== '' && Number.isFinite(Number(n)) && Number(n) >= 0 && Number(n) <= 10
const day = value => typeof value === 'string' && value.length === 10 && validWatchDate(value)
const month = value => typeof value === 'string' && value.length === 7 && validWatchDate(value)
const monthDays = (year, m) => new Date(Date.UTC(year, m, 0)).getUTCDate()

export function currentReviewMonth(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).formatToParts(now)
  return `${parts.find(p => p.type === 'year').value}-${parts.find(p => p.type === 'month').value}`
}
export function shiftReviewMonth(value, amount) {
  if (!month(value)) throw new Error('请填写有效月份。')
  const [year, m] = value.split('-').map(Number), index = year * 12 + m - 1 + amount
  const nextYear = Math.floor(index / 12)
  if (nextYear < 1000 || nextYear > 9999) throw new Error('月份超出可选择范围。')
  return `${nextYear}-${String(index % 12 + 1).padStart(2, '0')}`
}
export function reviewParams(draft) {
  if (draft.mode === 'month') {
    if (!month(draft.month)) throw new Error('请选择有效的回顾月份。')
    return { month: draft.month }
  }
  if (draft.mode !== 'custom' || !day(draft.start) || !day(draft.end)) throw new Error('请填写完整、有效的起止日期。')
  if (draft.start > draft.end) throw new Error('结束日期不能早于开始日期。')
  if ((Date.parse(`${draft.end}T00:00:00Z`) - Date.parse(`${draft.start}T00:00:00Z`)) / 86400000 >= 366) throw new Error('单次回顾最多选择 366 天。')
  return { start: draft.start, end: draft.end }
}
export function reviewPeriod(params) {
  if (params.month) {
    const [year, m] = params.month.split('-').map(Number)
    return { mode: 'month', start: `${params.month}-01`, end: `${params.month}-${monthDays(year, m)}`, timezone: 'Asia/Shanghai', end_inclusive: true }
  }
  return { mode: 'custom', start: params.start, end: params.end, timezone: 'Asia/Shanghai', end_inclusive: true }
}
export const reviewTitle = period => period.mode === 'month' ? `${formatWatchDate(period.start.slice(0, 7))}观看回顾` : `${formatWatchDate(period.start)} — ${formatWatchDate(period.end)}`
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
const samePeriod = (a, b) => ['mode', 'start', 'end', 'timezone', 'end_inclusive'].every(k => a?.[k] === b?.[k])

export function validReviewSummary(data, params) {
  const s = data?.summary, c = data?.coverage, p = data?.period
  const counts = ['started_works', 'started_rounds', 'completed_works', 'completed_rounds', 'first_completed_rounds', 'rewatch_started_rounds', 'rewatch_completed_rounds', 'notes', 'memories', 'viewed_works', 'related_works', 'activity_facts']
  return Boolean(data?.contract_version === 'journal.watch-review.v1' && data.policy_version === 'actual-date-contained.v1'
    && data.counts_scope === 'owner_all_records_selected_period' && data.live === true && data.metadata_basis === 'current_journal'
    && samePeriod(p, reviewPeriod(params)) && day(p.start) && day(p.end) && p.start <= p.end
    && typeof data.snapshot === 'string' && data.snapshot.length > 0 && data.snapshot.length <= 2048 && Number.isFinite(Date.parse(data.generated_at))
    && counts.every(k => natural(s?.[k])) && s.started_works <= s.started_rounds && s.completed_works <= s.completed_rounds
    && s.first_completed_rounds + s.rewatch_completed_rounds === s.completed_rounds && s.rewatch_started_rounds <= s.started_rounds
    && s.memories <= s.notes && s.viewed_works <= s.related_works
    && natural(s.completed_round_rating?.count) && natural(s.completed_round_rating?.missing_count)
    && s.completed_round_rating.count + s.completed_round_rating.missing_count === s.completed_rounds
    && s.completed_round_rating.basis === 'effective_completed_round'
    && (s.completed_round_rating.count === 0 ? s.completed_round_rating.average === null : score(s.completed_round_rating.average))
    && ['uncertain', 'undated'].every(k => natural(c?.[k]?.facts) && natural(c?.[k]?.works) && c[k].works <= c[k].facts)
    && c.undated_scope === 'owner_all_history' && c.uncertain_scope === 'overlaps_selected_period'
    && c.includes_undated_in_summary === false && c.includes_uncertain_in_summary === false)
}
function validWork(work) {
  return work && Number.isSafeInteger(work.record_id) && work.record_id > 0 && typeof work.title === 'string'
    && typeof work.original_title === 'string' && typeof work.airing_period === 'string' && typeof work.poster_url === 'string'
    && work.metadata_basis === 'current_journal' && ['plan', 'watching', 'on_hold', 'completed'].includes(work.current_watch_status)
    && (work.current_score === null || score(work.current_score))
}
function bounds(value) {
  if (!validWatchDate(value)) return null
  const [year, m] = value.split('-').map(Number)
  return value.length === 4 ? { start: `${value}-01-01`, end: `${value}-12-31` }
    : value.length === 7 ? { start: `${value}-01`, end: `${value}-${monthDays(year, m)}` } : { start: value, end: value }
}
function validEntry(row, section, period) {
  if (!validWork(row?.work)) return false
  if (section === 'works') {
    const date = bounds(row.latest_actual_date)
    return row.entry_type === 'work' && natural(row.matched_fact_count) && row.matched_fact_count > 0
    && row.matched_kinds && Object.keys(row.matched_kinds).every(k => Object.hasOwn(REVIEW_KINDS, k))
    && Object.values(row.matched_kinds).every(natural) && Object.values(row.matched_kinds).reduce((a, b) => a + b, 0) === row.matched_fact_count
    && date && period.start <= date.start && date.end <= period.end
  }
  if (row.entry_type !== 'fact' || row.record_id !== row.work.record_id || typeof row.id !== 'string' || !row.id
    || !Object.hasOwn(REVIEW_KINDS, row.kind) || !Number.isSafeInteger(row.round_number) || row.round_number <= 0
    || typeof row.note !== 'string' || typeof row.is_rewatch !== 'boolean' || !['effective_round', 'explicit_event'].includes(row.date_source)) return false
  if (row.kind === 'completed' && ((row.rating !== null && !score(row.rating)) || !['', 'manual', 'completion_snapshot'].includes(row.rating_origin))) return false
  if (row.recorded_at !== undefined && !Number.isFinite(Date.parse(row.recorded_at))) return false
  if (section === 'undated') return row.attribution === 'undated' && row.actual_date === '' && row.date_precision === 'unknown' && row.date_bounds === null
  const b = bounds(row.actual_date)
  if (!b || row.date_bounds?.start !== b.start || row.date_bounds?.end !== b.end
    || row.date_precision !== ({ 4: 'year', 7: 'month', 10: 'day' })[row.actual_date.length]) return false
  const contained = period.start <= b.start && b.end <= period.end
  if (section === 'uncertain') return row.attribution === 'uncertain' && !contained && b.start <= period.end && b.end >= period.start
  return row.attribution === 'definite' && contained && (section === 'activity' ? !['note', 'memory'].includes(row.kind)
    : section === 'notes' ? ['note', 'memory'].includes(row.kind) : section === 'rewatch' ? row.kind === 'completed' && row.is_rewatch : row.kind === section)
}
export function validReviewEntries(data, { params, section, page, pageSize, snapshot, baseline }) {
  if (!validReviewSummary(data, params) || data.section !== section || data.page !== page || data.page_size !== pageSize
    || !natural(data.count) || !Array.isArray(data.results) || data.count_unit !== (section === 'works' ? 'works' : 'facts')
    || data.next_page !== (page * pageSize < data.count ? page + 1 : null)
    || data.results.length !== Math.min(pageSize, Math.max(0, data.count - (page - 1) * pageSize))) return false
  if (snapshot && (snapshot !== data.snapshot || baseline && (!equal(data.summary, baseline.summary) || !equal(data.coverage, baseline.coverage)))) return false
  const count = { works: data.summary.related_works, completed: data.summary.completed_rounds, started: data.summary.started_rounds,
    rewatch: data.summary.rewatch_completed_rounds, activity: data.summary.activity_facts, notes: data.summary.notes,
    uncertain: data.coverage.uncertain.facts, undated: data.coverage.undated.facts }[section]
  return data.count === count && data.results.every(row => validEntry(row, section, data.period))
    && new Set(data.results.map(row => section === 'works' ? row.work.record_id : row.id)).size === data.results.length
}
export function reviewError(error) {
  if (error.response?.status === 401) return '登录状态已过期，请重新登录。'
  if (error.response?.status === 503) return '回顾暂时无法完整读取，请稍后重试。'
  if (error.response?.status === 409) return '回顾资料正在变化，请重新读取。'
  return error.response?.status === 400 ? '日期或读取条件无效，请检查后重试。' : '回顾读取未完成，请检查连接后重试。'
}

// A single entries response carries the matching full summary, avoiding mixed cohorts.
export function createReviewController({ client, getSession, initialParams = { month: currentReviewMonth() } }) {
  const owner = getSession(), listeners = new Set()
  let active = true, serial = 0, abort, baseline = null
  let state = { status: 'idle', data: null, params: initialParams, section: 'works', page: 1, pageSize: 12, error: '', message: '' }
  const emit = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  const live = run => active && run === serial && owner === getSession()
  async function read(changes = {}) {
    if (!active || owner !== getSession()) return false
    abort?.abort(); abort = new AbortController()
    const signal = abort.signal, run = ++serial
    const params = changes.params || state.params, section = changes.section || state.section
    const pageSize = changes.pageSize || state.pageSize
    let page = changes.page ?? 1
    let token = !changes.fresh && baseline && equal(params, baseline.params) ? baseline.data.snapshot : null
    if (!token) page = 1
    emit({ status: 'loading', data: null, params, section, page, pageSize, error: '', message: '' })
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const { data } = await client.get('watch-review/entries/', { params: { ...params, section, page, page_size: pageSize, ...(token ? { snapshot: token } : {}) }, signal })
        if (!live(run)) return false
        if (!validReviewEntries(data, { params, section, page, pageSize, snapshot: token, baseline: token ? baseline?.data : null })) throw new Error('incomplete review contract')
        baseline = { params, data }
        emit({ status: 'ready', data, page, message: attempt ? '资料已更新，已重新读取本区第一页。' : '' })
        return true
      } catch (error) {
        if (!live(run) || signal.aborted) return false
        if (error.response?.status === 409 && attempt === 0) {
          baseline = null; token = null; page = 1
          continue
        }
        emit({ status: 'error', data: null, page, error: reviewError(error) })
        return false
      }
    }
    return false
  }
  return { read, refresh: () => read({ fresh: true, page: 1 }), getSnapshot: () => state,
    subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn) },
    activate: () => { active = true }, cancel: () => { abort?.abort(); serial++ },
    dispose: () => { active = false; abort?.abort(); serial++; baseline = null } }
}
