import { checkScopeResponse, controlledScope } from './recommendationContentScope.js'

export const SERIES_SECTIONS = { chronology: '故事前后', branches: '番外与分支', editions: '总集与其他版本', related: '其他关联' }
export const SERIES_KINDS = { prequel: ['前传', 'chronology'], sequel: ['续集', 'chronology'], summary: ['总集篇', 'editions'], full_story: ['全集', 'editions'],
  side_story: ['番外篇', 'branches'], alternative_setting: ['不同世界观', 'branches'], alternative_version: ['不同演绎', 'branches'], spin_off: ['衍生', 'branches'], parent_story: ['主线故事', 'branches'],
  adaptation: ['改编', 'related'], character: ['角色出演', 'related'], same_setting: ['相同世界观', 'related'], collaboration: ['联动', 'related'], other: ['其他', 'related'] }
export const COVERAGE = { observed: '已读取关联资料', empty_observed: '当前来源未列出关联', not_loaded: '尚未获取关系资料', partial: '资料获取不完整', failed: '最近获取失败', stale: '资料较早，可能有更新' }
const id = value => Number.isInteger(value) && value > 0 && value <= 2147483647
const text = (value, max = 2000) => typeof value === 'string' && value.length <= max
const own = row => ['linked', 'needs_review', 'no_confirmed_record'].includes(row?.record_state)
  && (row.record_state === 'linked' ? ['plan', 'watching', 'completed', 'on_hold'].includes(row.watch_status) : row.watch_status === null)
const fail = () => { throw new Error('系列数据合同不兼容，请重新读取。') }
export function validateSeries(data, source, scope = 'safe') {
  checkScopeResponse(data, scope)
  if (data?.contract_version !== 'recommendations.series.workspace.v1' || !id(source) || data.selected_seed?.bangumi_id !== source
    || !text(data.selected_seed.title_zh, 500) || !text(data.selected_seed.title_jp, 500) || !own(data.selected_seed)
    || !Object.hasOwn(COVERAGE, data.coverage) || !Object.hasOwn(COVERAGE, data.last_attempt_status)
    || !Number.isSafeInteger(data.series_revision) || data.series_revision < 0 || !/^[a-f0-9]{64}$/.test(data.catalog_revision || '')
    || data.ordering !== 'release_date_not_watch_order' || data.depth !== 1 || data.limit !== 200 || typeof data.truncated !== 'boolean'
    || !text(data.context, 4096) || !data.context || !text(data.scope_note) || !text(data.record_note)
    || data.content_scope_context?.content_scope !== scope || !/^[a-f0-9]{64}$/.test(data.content_scope_context?.scope_revision || '')
    || !Array.isArray(data.results) || data.results.length > 200) fail()
  const keys = new Set()
  for (const row of data.results) {
    const spec = typeof row.kind === 'string' && Object.hasOwn(SERIES_KINDS, row.kind) ? SERIES_KINDS[row.kind] : null
    const key = `${row.bangumi_id}:${row.kind}`
    if (!id(row.bangumi_id) || row.bangumi_id === source || keys.has(key) || !spec || spec[0] !== row.relation_label || spec[1] !== row.section
      || !text(row.title_zh, 500) || !text(row.title_jp, 500) || !text(row.release_format, 40)
      || !['year', 'month', 'day', 'unknown'].includes(row.release_precision) || !text(row.release_date, 10)
      || ({ year: 4, month: 7, day: 10, unknown: 0 })[row.release_precision] !== row.release_date.length
      || row.release_date && !/^\d{4}(-\d{2})?(-\d{2})?$/.test(row.release_date)
      || !['archive', 'manual'].includes(row.origin) || !own(row) || typeof row.can_add_from_series !== 'boolean'
      || row.can_add_from_series !== (row.record_state !== 'needs_review' && row.watch_status === null)) fail()
    keys.add(key)
  }
  return data
}
export function validateSeriesDetail(data, series, target, scope) {
  if (data?.contract_version !== 'recommendations.series.detail.v1') fail()
  validateSeries({ ...data, contract_version: 'recommendations.series.workspace.v1' }, series.selected_seed.bangumi_id, scope)
  const row = data.item
  if (data.series_revision !== series.series_revision || data.catalog_revision !== series.catalog_revision
    || data.content_scope_context.scope_revision !== series.content_scope_context.scope_revision
    || ![series.selected_seed.bangumi_id, ...series.results.map(item => item.bangumi_id)].includes(target)
    || row?.bangumi_id !== target || !own(row) || !text(row.title_zh, 500) || !text(row.title_jp, 500)
    || !text(row.synopsis, 100000) || !text(row.season, 40) || !text(row.status_type, 40) || !text(row.cover_url, 4096)
    || !Array.isArray(row.tags) || row.tags.some(tag => !text(tag, 80)) || row.tags.length > 100
    || !(row.content_rating === 'non_adult' || row.content_rating === 'adult' && controlledScope(data.content_scope_context))) fail()
  return { bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp, originalTitle: row.title_jp, season: row.season,
    type: row.status_type, synopsis: row.synopsis, tags: row.tags, coverUrl: row.content_rating === 'adult' ? '' : row.cover_url,
    contentRating: row.content_rating, watchStatus: row.watch_status, recordState: row.record_state }
}
export function seriesStatus(row) {
  return ({ plan: '已在想看', watching: '在看', completed: '看过', on_hold: '搁置' })[row.watch_status]
    || (row.record_state === 'needs_review' ? '记录身份待核对' : '无已确认记录')
}
export function seriesFailure(error) {
  if (error?.response?.data?.code === 'series_snapshot_changed') return '系列资料已更新或上下文已过期，请重新读取。'
  if (error?.scopeLost || [401, 403].includes(error?.response?.status)) return '访问范围已变化，请返回原页重新确认。'
  if (error?.response?.status === 404) return '当前范围内无法读取这部作品；不会按名称猜测身份。'
  if (error?.response?.status === 503) return '系列关系暂不可用，原有推荐和手账仍可使用。'
  return error?.message?.startsWith('系列数据合同') ? error.message : '系列资料读取失败，请重试。'
}
export function seriesBackground(location) {
  const path = typeof location?.pathname === 'string' ? location.pathname.replace(/\/$/, '') : null
  return ['/dashboard', '/recommendations', '/recommendations/similar', '/recommendations/tags'].includes(path) ? location : null
}
