import { checkScopeResponse } from './recommendationContentScope.js'

export const REMINDERS_VERSION = 'recommendations.series.reminders.v1'
export const REMINDERS_POLICY = 'series-reminders.completed-direct.v1'
const id = n => Number.isInteger(n) && n > 0 && n <= 2147483647
const text = value => typeof value === 'string' && value.length <= 500
const invalid = () => { throw new Error('后续提醒合同不兼容，请重新读取。') }
export function validateReminders(data, scope = 'safe') {
  checkScopeResponse(data, scope)
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

// No optimistic writes or persisted browse data; an uncertain PATCH is never replayed.
export function createSeriesReminderController(client, isCurrent = () => true) {
  const scope = client.contentScope || 'safe'
  let active = true, generation = 0, request, state = { status: 'loading', data: null }, listener = () => {}
  const emit = next => { state = next; listener(state) }
  const current = value => active && isCurrent() && generation === value
  async function refresh(saved = false) {
    if (!active || state.status === 'saving') return
    request?.abort(); request = new AbortController(); const captured = ++generation
    emit({ status: 'loading', data: null, saved })
    try {
      const raw = await client.seriesReminders(request.signal)
      if (current(captured)) emit({ status: 'ready', data: validateReminders(raw, scope), saved })
    } catch (error) { if (current(captured)) emit({ status: 'error', data: null, saved,
      message: saved ? '设置已保存，最新提醒读取失败。请重新读取。' : '后续提醒暂时无法读取，请重试。' }) }
  }
  async function change(action, fields = {}) {
    if (!active || !isCurrent() || state.status !== 'ready') return
    const data = state.data
    if (data.expires_at * 1000 <= Date.now()) { await refresh(); return }
    request?.abort(); request = new AbortController(); const captured = ++generation
    emit({ status: 'saving', data })
    try {
      const raw = await client.changeSeriesReminders({ ...fields, action, context: data.context, expected_version: data.version }, request.signal)
      if (!current(captured)) return
      checkScopeResponse(raw, scope)
      if (raw.contract_version !== REMINDERS_VERSION || raw.saved !== true || raw.action !== action || raw.version !== data.version + 1) invalid()
      emit({ status: 'loading', data: null }); await refresh(true)
    } catch (error) {
      if (current(captured)) emit({ status: 'error', data: null,
        message: error?.response?.data?.code === 'series_snapshot_changed' ? '提醒依据已更新，请重新读取后操作。'
          : '设置结果尚未确认。请重新读取查看当前状态；不会自动重复操作。' })
    }
  }
  return { refresh, change, getState: () => state,
    subscribe(fn) { listener = fn; fn(state); return () => { listener = () => {} } },
    dispose() { active = false; generation++; request?.abort(); listener = () => {} } }
}
