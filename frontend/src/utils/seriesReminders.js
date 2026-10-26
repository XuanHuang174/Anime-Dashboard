import { checkScopeResponse } from './recommendationContentScope.js'

export const REMINDERS_VERSION = 'recommendations.series.reminders.v1'
export const REMINDERS_POLICY = 'series-reminders.completed-direct.v1'
const id = n => Number.isInteger(n) && n > 0 && n <= 2147483647
const text = value => typeof value === 'string' && value.length <= 500
const invalid = () => { throw new Error('后续提醒合同不兼容，请重新读取。') }
export function validateReminders(data, scope = 'safe') {
  checkScopeResponse(data, scope)
  if (data?.dismissal_revision !== undefined && !/^[a-f0-9]{64}$/.test(data.dismissal_revision)) invalid()
  if (data?.contract_version !== REMINDERS_VERSION || data.policy_version !== REMINDERS_POLICY
    || !/^[a-f0-9]{64}$/.test(data.fingerprint) || !Number.isInteger(data.version) || data.version < 0 || data.version > 2147483647
    || typeof data.collapsed !== 'boolean' || data.limit !== 6 || typeof data.truncated !== 'boolean'
    || !Number.isInteger(data.count) || data.count < 0 || data.count > 5000 || data.truncated !== (data.count > 6)
    || !Number.isInteger(data.hidden_count) || data.hidden_count < 0 || data.hidden_count > 5000
    || typeof data.context !== 'string' || !data.context.length || data.context.length > 4096
    || !Number.isInteger(data.expires_at) || data.expires_at * 1000 <= Date.now()
    || !Array.isArray(data.results) || data.results.length !== Math.min(6, data.count)
    || !text(data.record_note) || data.content_scope_context?.content_scope !== scope) invalid()
  const seen = new Set()
  for (const row of data.results) {
    if (!id(row.bangumi_id) || seen.has(row.bangumi_id) || !text(row.title_zh) || !text(row.title_jp)
      || !text(row.release_date) || !text(row.release_format) || row.record_state !== 'no_confirmed_record'
      || typeof row.more_sources !== 'boolean' || !Array.isArray(row.facts) || !row.facts.length || row.facts.length > 2) invalid()
    for (const fact of row.facts) {
      if (!id(fact.source_id) || !id(fact.target_id) || !id(fact.completed_id) || fact.completed_id === row.bangumi_id
        || !text(fact.source_title) || !text(fact.target_title) || !['archive', 'manual'].includes(fact.origin)
        || !(fact.kind === 'sequel' && fact.relation_label === '续集' && fact.source_id === fact.completed_id && fact.target_id === row.bangumi_id
          || fact.kind === 'prequel' && fact.relation_label === '前传' && fact.target_id === fact.completed_id && fact.source_id === row.bangumi_id)) invalid()
    }
    seen.add(row.bangumi_id)
  }
  return data
}

export const PRESENTATION_VERSION = 'recommendations.series.reminders.presentation.v1'
export function validateReminderPresentation(data, scope = 'safe') {
  checkScopeResponse(data, scope)
  if (data?.contract_version !== PRESENTATION_VERSION || typeof data.collapsed !== 'boolean'
    || !/^[a-f0-9]{64}$/.test(data.dismissal_revision)
    || !Number.isInteger(data.version) || data.version < 0 || data.version > 2147483647
    || !Number.isSafeInteger(data.series_revision) || data.series_revision < 0) invalid()
  return data
}

// React-owned memory only: no private lists/tokens in localStorage or shared caches.
// Hide/restore stay server-confirmed; uncertain writes are never replayed.
export function createSeriesReminderController(client, isCurrent = () => true, now = () => Date.now()) {
  const scope = client.contentScope || 'safe', listeners = new Set()
  let active = true, visible = false, generation = 0, epoch = 0, task, prefTask, request, prefRequest, lastProbe = -Infinity, choice = false
  let state = { status: 'idle', data: null, collapsed: true, presentation: null, initialized: false, dirty: true, refreshing: false, preferenceSaving: false }
  const current = value => active && isCurrent() && generation === value
  const emit = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn(state)) }
  const deadline = () => Math.min(state.data?.expires_at * 1000 || Infinity,
    ...[state.data?.content_scope_context, state.presentation?.content_scope_context].map(value => value?.scope_valid_until ? Date.parse(value.scope_valid_until) : Infinity))
  const fresh = () => !!state.data && deadline() > now() && !state.dirty
  const invalidate = ({ discard = false } = {}) => {
    epoch++; emit({ dirty: true, ...(discard ? { data: null, presentation: null, status: 'idle' } : {}) })
  }
  const expire = () => {
    if (deadline() > now()) return
    const expiry = state.presentation?.content_scope_context?.scope_valid_until
    epoch++; emit({ data: null, dirty: true, status: 'idle',
      ...(expiry && Date.parse(expiry) <= now() ? { presentation: null } : {}) })
  }
  async function checkPresentation(force = false) {
    if (!active || !isCurrent() || state.preferenceSaving) return
    if (prefTask) return prefTask
    if (!client.reminderPresentation) { emit({ initialized: true }); return }
    if (!force && state.initialized && now() - lastProbe < 30000) return
    lastProbe = now(); prefRequest = new AbortController(); const captured = generation
    const pending = (async () => {
      try {
        const raw = validateReminderPresentation(await client.reminderPresentation(prefRequest.signal), scope)
        if (!current(captured) || raw.version < (state.presentation?.version ?? 0)) return
        const old = state.presentation
        if (old && (old.dismissal_revision !== raw.dismissal_revision || old.series_revision !== raw.series_revision
            || old.content_scope_context.scope_revision !== raw.content_scope_context.scope_revision)) {
          invalidate({ discard: old.content_scope_context.scope_revision !== raw.content_scope_context.scope_revision })
        }
        emit({ initialized: true, presentation: raw, preferenceMessage: '',
          ...(state.data ? { data: { ...state.data, version: raw.version, collapsed: raw.collapsed } } : {}),
          ...(!choice ? { collapsed: raw.collapsed } : {}) })
      } catch (error) {
        if (!current(captured)) return
        if ([401, 403].includes(error?.response?.status) || error?.scopeLost || error?.response?.data?.code === 'series_disabled') invalidate({ discard: true })
        emit({ initialized: true, preferenceMessage: '展示设置暂时无法读取，可展开后重试；不会覆盖已保存的选择。' })
      }
    })().finally(() => { if (prefTask === pending) prefTask = null })
    prefTask = pending
    return prefTask
  }
  async function refresh(saved = false) {
    if (!active || !isCurrent() || state.status === 'saving' || state.preferenceSaving) return
    if (task) return task
    expire(); request = new AbortController(); const captured = generation, dataEpoch = epoch
    emit({ status: state.data ? 'ready' : 'loading', refreshing: true, message: '', saved })
    const pending = (async () => {
      try {
        const data = validateReminders(await client.seriesReminders(request.signal), scope)
        if (!current(captured) || epoch !== dataEpoch) return
        // Presentation-only saves do not alter the list/context fingerprint.
        const version = Math.max(data.version, state.presentation?.version ?? 0)
        emit({ status: 'ready', data: { ...data, version }, dirty: false, refreshing: false,
          ...(!client.reminderPresentation && !choice ? { collapsed: data.collapsed } : {}),
          ...(state.presentation ? { presentation: { ...state.presentation, version,
            ...(data.dismissal_revision ? { dismissal_revision: data.dismissal_revision } : {}) } } : {}), saved })
      } catch (error) {
        if (!current(captured)) return
        const lost = [401, 403].includes(error?.response?.status) || error?.scopeLost || error?.response?.data?.code === 'series_disabled'
        if (lost) invalidate({ discard: true })
        emit({ status: state.data ? 'ready' : 'error', refreshing: false, saved,
          message: saved ? '设置已保存，最新提醒读取失败。请重新读取。' : '后续提醒暂时无法读取，请重试。' })
      } finally { if (current(captured)) emit({ refreshing: false }) }
    })().finally(() => { if (task === pending) task = null })
    task = pending
    return task
  }
  async function maybeRefresh() {
    expire()
    // A record may change while the first read is in flight. Coalesce its
    // invalidations, discard that response, then do one current read.
    for (let attempt = 0; attempt < 2 && visible && !state.collapsed && !fresh()
      && state.status !== 'error' && !state.message && !state.preferenceSaving; attempt++) await refresh()
  }
  async function initialize() {
    if (!state.initialized) await checkPresentation(true)
    await maybeRefresh()
  }
  async function setCollapsed(collapsed) {
    if (!active || !isCurrent() || state.preferenceSaving || state.status === 'saving') return false
    choice = true; emit({ collapsed, message: '', preferenceMessage: '' })
    if (!state.initialized || !state.presentation) await checkPresentation(true)
    if (!active || !isCurrent()) return false
    if (!client.saveReminderPresentation || !state.presentation) {
      if (!collapsed) await refresh()
      return false
    }
    const expected = state.presentation.version, captured = generation
    prefRequest = new AbortController(); emit({ preferenceSaving: true })
    let saved = false
    try {
      const data = validateReminderPresentation(await client.saveReminderPresentation({ collapsed, expected_version: expected }, prefRequest.signal), scope)
      if (!current(captured)) return false
      if (data.version !== expected + 1 || data.collapsed !== collapsed) invalid()
      const old = state.presentation
      if (old && (old.dismissal_revision !== data.dismissal_revision || old.series_revision !== data.series_revision
          || old.content_scope_context.scope_revision !== data.content_scope_context.scope_revision)) {
        invalidate({ discard: old.content_scope_context.scope_revision !== data.content_scope_context.scope_revision })
      }
      choice = false; saved = true
      emit({ presentation: data, ...(state.data ? { data: { ...state.data, collapsed, version: data.version } } : {}) })
    } catch (error) {
      if (current(captured)) {
        if ([401, 403].includes(error?.response?.status) || error?.scopeLost || error?.response?.data?.code === 'series_disabled') invalidate({ discard: true })
        emit({ preferenceMessage: '本次展开状态已保留，保存结果尚未确认；可重新读取设置，不会自动重复保存。' })
      }
    } finally { if (current(captured)) emit({ preferenceSaving: false }) }
    if (!collapsed && !fresh()) await refresh()
    return saved
  }
  async function change(action, fields = {}) {
    if (action === 'collapse' && client.saveReminderPresentation) return setCollapsed(fields.collapsed)
    if (!active || !isCurrent() || state.status !== 'ready' || state.refreshing || state.preferenceSaving) return false
    const data = state.data
    if (!fresh()) { await refresh(); return false }
    request = new AbortController(); const captured = generation
    emit({ status: 'saving', message: '' })
    try {
      const raw = await client.changeSeriesReminders({ ...fields, action, context: data.context, expected_version: data.version }, request.signal)
      if (!current(captured)) return false
      checkScopeResponse(raw, scope)
      if (raw.contract_version !== REMINDERS_VERSION || raw.saved !== true || raw.action !== action || raw.version !== data.version + 1) invalid()
      if (state.presentation) emit({ presentation: { ...state.presentation, version: raw.version } })
      emit({ status: 'ready', dirty: true }); await refresh(true)
      return true
    } catch (error) {
      if (current(captured)) emit({ status: 'error', data: null,
        message: error?.response?.data?.code === 'series_snapshot_changed' ? '提醒依据已更新，请重新读取后操作。'
          : '设置结果尚未确认。请重新读取查看当前状态；不会自动重复操作。' })
      return false
    }
  }
  return { refresh, change, initialize, setCollapsed, invalidate, maybeRefresh, expire, deadline,
    async foreground() { if (!visible || !active || !isCurrent()) return; expire(); await checkPresentation(); await maybeRefresh() },
    checkPresentation, setVisible(value) { visible = value },
    activate() { active = true }, getState: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    dispose() { active = false; generation++; request?.abort(); prefRequest?.abort(); task = prefTask = null } }
}
