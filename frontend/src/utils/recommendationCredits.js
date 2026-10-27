import { checkScopeResponse } from './recommendationContentScope.js'

export const CREDIT_KINDS = { voice_actor: '声优', studio: '制作公司' }
export const CREDIT_COVERAGE = { observed: '已读取参与资料', empty_observed: '当前来源未列出初版参与关系',
  not_loaded: '尚未收录参与资料', partial: '资料不完整', failed: '最近获取失败', stale: '资料较早，待更新', disabled: '尚未开放' }
export const CREDIT_DEFAULTS = Object.freeze({ view: 'all', sort: 'score_desc', type: 'all', year_from: null, year_to: null,
  min_score: 0, max_score: 10, max_score_exclusive: false, hide_recorded: true })
export const creditId = value => Number.isSafeInteger(value) && value > 0 && value <= 2147483647
const text = (v, max = 2000) => typeof v === 'string' && v.length <= max
const count = v => Number.isSafeInteger(v) && v >= 0
const fail = () => { throw new Error('人物与制作资料合同不兼容，请重新读取。') }
export function validateCreditsCapabilities(data) {
  if (data?.contract_version !== 'recommendations.credits.capabilities.v1'
    || typeof data.voice_actor !== 'boolean' || typeof data.studio !== 'boolean') fail()
  return data
}
export function creditsAvailable(data) { return data?.voice_actor === true || data?.studio === true }
function common(data, kind, scope) {
  checkScopeResponse(data, scope)
  if (data?.contract_version !== `recommendations.credits.${kind}.v1` || !count(data.credits_revision)
    || data.content_scope_context?.content_scope !== scope || !/^[a-f0-9]{64}$/.test(data.content_scope_context?.scope_revision || '')
    || data.coverage?.status !== 'scoped_current_sources' || data.coverage.complete_filmography !== false || !text(data.coverage.detail)) fail()
}
function entity(row, kind, person) {
  if (!row || row.kind !== kind || !creditId(row.person_id) || person !== undefined && row.person_id !== person
    || !text(row.name, 500) || !row.name.trim() || !count(row.work_count) || row.work_count < 1
    || !['observed', 'partial'].includes(row.alias_status)) fail()
}
export function validateCreditsSearch(data, kind, scope) {
  common(data, 'search', scope)
  if (data.kind !== kind || data.limit !== 15 || typeof data.has_more !== 'boolean'
    || !Array.isArray(data.results) || data.results.length > 15 || data.has_more && data.results.length !== 15) fail()
  const ids = new Set()
  for (const row of data.results) { entity(row, kind); if (ids.has(row.person_id)) fail(); ids.add(row.person_id) }
  return data
}
export function validateCreditsRecord(data, record, scope) {
  common(data, 'record', scope)
  if (data.record_id !== record || !['linked', 'unlinked', 'identity_conflict', 'stale_link', 'not_in_catalog', 'unsafe_or_unknown'].includes(data.identity_state)
    || (data.identity_state === 'linked' ? !creditId(data.bangumi_id) : data.bangumi_id !== null)) fail()
  return data
}
function evidence(row, subject, kind, person) {
  if (row?.subject_id !== subject || row.person_id !== person || row.kind !== kind
    || row.position_or_cast_type !== (kind === 'studio' ? 67 : 0) || !text(row.source_episode_note)
    || !text(row.snapshot_version, 64) || !/^dump-\d{4}-\d{2}-\d{2}\.\d{6}Z$/.test(row.snapshot_version)
    || !text(row.source_at, 40) || !Number.isFinite(Date.parse(row.source_at))
    || !Array.isArray(row.characters) || row.characters.length > 5000
    || (kind === 'studio' ? row.characters.length !== 0 : row.characters.length === 0)) fail()
  const ids = new Set()
  for (const role of row.characters) {
    if (!creditId(role.character_id) || ids.has(role.character_id) || !text(role.name, 500)
      || role.role_type !== null && !count(role.role_type) || role.source_order !== null && !count(role.source_order)
      || !text(role.source_episode_note) || !Array.isArray(role.annotation_warnings)
      || role.annotation_warnings.some(v => v !== 'unmapped_character_role')) fail()
    ids.add(role.character_id)
  }
}
export function validateCreditsSummary(data, subject, scope) {
  common(data, 'summary', scope)
  if (data.bangumi_id !== subject || !data.sections) fail()
  for (const kind of Object.keys(CREDIT_KINDS)) {
    const section = data.sections[kind]
    if (!section || !Object.hasOwn(CREDIT_COVERAGE, section.status) || !count(section.count) || section.count > 5000
      || ![0, 50, 5000].includes(section.limit) || typeof section.has_more !== 'boolean'
      || !Array.isArray(section.results) || section.results.length !== Math.min(section.count, section.limit)
      || section.has_more !== (section.count > section.limit)
      || section.last_attempt_status !== null && !Object.hasOwn(CREDIT_COVERAGE, section.last_attempt_status)) fail()
    const ids = new Set()
    for (const row of section.results) {
      if (row.kind !== kind || !creditId(row.person_id) || ids.has(row.person_id) || !text(row.name, 500)) fail()
      evidence(row.evidence, subject, kind, row.person_id); ids.add(row.person_id)
    }
  }
  return data
}
export function validateCreditConditions(options) {
  const keys = Object.keys(CREDIT_DEFAULTS)
  if (!options || Object.keys(options).length !== keys.length || keys.some(k => !Object.hasOwn(options, k))
    || !['all', 'completed'].includes(options.view) || !['score_desc', 'release_desc', 'release_asc'].includes(options.sort)
    || !['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].includes(options.type)
    || typeof options.hide_recorded !== 'boolean' || typeof options.max_score_exclusive !== 'boolean'
    || options.view === 'completed' && options.hide_recorded
    || [options.year_from, options.year_to].some(v => v !== null && (!Number.isInteger(v) || v < 1000 || v > 9999))
    || options.year_from && options.year_to && options.year_from > options.year_to
    || [options.min_score, options.max_score].some(v => !Number.isFinite(v) || v < 0 || v > 10 || Math.round(v * 10) !== v * 10)
    || options.min_score > options.max_score || options.max_score_exclusive && (options.max_score >= 10 || options.min_score >= options.max_score)) fail()
  return options
}
export function sameCreditConditions(a, b) { return Object.keys(CREDIT_DEFAULTS).every(k => a?.[k] === b?.[k]) }
export function validateCreditsWorks(data, kind, person, options, scope, now = Date.now()) {
  common(data, 'works', scope); entity(data.entity, kind, person); validateCreditConditions(data.conditions)
  if (!sameCreditConditions(data.conditions, options) || !count(data.count) || data.count > data.entity.work_count
    || data.ordering !== 'credits.catalog-period.v1' || !text(data.context, 4096) || !data.context
    || data.next_cursor !== null && (!text(data.next_cursor, 4096) || !data.next_cursor)
    || !Number.isInteger(data.expires_at) || data.expires_at * 1000 <= now || data.expires_at * 1000 > now + 901000
    || !Array.isArray(data.results) || data.results.length > 12 || data.results.length > data.count
    || data.next_cursor !== null && (data.results.length !== 12 || data.count <= 12)) fail()
  const ids = new Set()
  for (const row of data.results) {
    if (!creditId(row.bangumi_id) || ids.has(row.bangumi_id) || !text(row.title_zh, 500) || !text(row.title_jp, 500)
      || !Number.isFinite(row.score) || row.score < 0 || row.score > 10 || !text(row.season, 40) || !text(row.status_type, 40)
      || !text(row.cover_url, 4096) || !Array.isArray(row.tags) || row.tags.length > 100 || row.tags.some(t => !text(t, 80))
      || !['non_adult', 'adult'].includes(row.content_rating) || row.content_rating === 'adult' && (scope !== 'include_adult' || row.cover_url !== '')
      || ![null, 'plan', 'watching', 'completed', 'on_hold'].includes(row.watch_status)
      || !['catalog_period', 'unknown'].includes(row.release_precision) || !text(row.release_period, 40)
      || row.release_precision === 'unknown' && row.release_period !== '') fail()
    evidence(row.evidence, row.bangumi_id, kind, person); ids.add(row.bangumi_id)
  }
  return data
}
export function creditItem(row) {
  return { bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp || `作品 ${row.bangumi_id}`,
    coverUrl: row.content_rating === 'adult' ? '' : row.cover_url, contentRating: row.content_rating,
    year: row.season.match(/^[1-9]\d{3}/)?.[0] || '', type: row.status_type, score: row.score }
}
export function creditsFailure(error) {
  const code = error?.response?.data?.code
  if (code === 'credits_disabled') return '人物与制作暂未开放，原有推荐与手账仍可使用。'
  if (code === 'credits_snapshot_changed') return '资料或浏览条件已更新，请重新读取后继续。'
  if (error?.scopeLost || [401, 403].includes(error?.response?.status)) return '访问范围已变化，请重新确认内容范围。'
  if (error?.response?.status === 404) return '当前范围没有可展示的关联资料。'
  if (error?.response?.status === 400) return '查询条件无效，请检查姓名、人物链接或筛选范围。'
  return error?.message?.startsWith('人物与制作资料合同') ? error.message : '参与资料读取失败，请稍后重试。'
}
export function creditsRoute(path) {
  if (/^\/recommendations\/credits\/?$/.test(path)) return { kind: null, person: null }
  const match = /^\/recommendations\/credits\/(voice_actor|studio)\/([1-9]\d*)\/?$/.exec(path)
  return match && creditId(Number(match[2])) ? { kind: match[1], person: Number(match[2]) } : null
}
export function creditsBackground(location) {
  const path = location?.pathname?.replace(/\/$/, '')
  return ['/dashboard', '/recommendations', '/recommendations/similar', '/recommendations/tags'].includes(path) ? location : null
}
export function creditsNavigationState(location, scope) {
  return { creditsBackground: creditsBackground(location.state?.creditsBackground) || creditsBackground(location),
    creditsFrom: creditsRoute(location.pathname) ? location.pathname : null,
    creditsScope: scope === 'include_adult' ? 'include_adult' : 'safe' }
}
