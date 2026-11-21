export function journalAiringYear(value) {
  const match = typeof value === 'string' && /^([1-9]\d{3})(?:[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?|年(0?[1-9]|1[0-2])月)?$/.exec(value.trim())
  if (!match) return null
  if (match[3]) {
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
    if (date.getUTCMonth() + 1 !== Number(match[2]) || date.getUTCDate() !== Number(match[3])) return null
  }
  return Number(match[1])
}

export function matchesCustomQuickFilter(item, filter) {
  if (!filter) return true
  const rated = item.score !== null && item.score !== undefined && item.score !== '' && typeof item.score !== 'boolean' && Number.isFinite(Number(item.score))
  const low = filter.score_min, high = filter.score_max
  if (filter.score_state === 'unrated' && rated) return false
  if ((filter.score_state === 'rated' || low != null || high != null) && !rated) return false
  if (rated && (low != null && Number(item.score) < Number(low) || high != null && Number(item.score) > Number(high))) return false
  if (filter.watch_statuses?.length && !filter.watch_statuses.includes(item.status)) return false
  const hasReview = Boolean(item.review?.trim())
  if (filter.review_state === 'with' && !hasReview || filter.review_state === 'without' && hasReview) return false
  if (filter.year_from != null || filter.year_to != null) {
    const year = journalAiringYear(item.filterPeriod ?? item.period)
    if (year === null || filter.year_from != null && year < filter.year_from || filter.year_to != null && year > filter.year_to) return false
  }

  const tags = item.tags ?? []
  const titles = [item.title ?? '', item.japaneseTitle ?? ''].map(title => title.toUpperCase())
  const titleKeywords = filter.title_keywords ?? []
  const hasKeyword = keyword => titles.some(title => title.includes(keyword.toUpperCase()))
  if ((filter.exclude_tags ?? []).some(tag => tags.includes(tag))
    || (filter.exclude_title_keywords ?? []).some(hasKeyword)) return false
  if (!(filter.tags?.length || titleKeywords.length)) return true

  if (filter.match_mode === 'all') {
    return (filter.tags ?? []).every((tag) => tags.includes(tag))
      && titleKeywords.every(
        hasKeyword,
      )
  }

  return (filter.tags ?? []).some((tag) => tags.includes(tag))
    || titleKeywords.some(
        hasKeyword,
    )
}

export function quickFilterSummary(filter) {
  if (!filter) return ''
  const included = [...(filter.tags ?? []), ...(filter.title_keywords ?? []).map(word => `标题:${word}`)]
  return [included.length ? `${filter.match_mode === 'all' ? '全部满足' : '任意匹配'}：${included.join('、')}` : '',
    filter.exclude_tags?.length ? `排除标签：${filter.exclude_tags.join('、')}` : '',
    filter.exclude_title_keywords?.length ? `排除标题词：${filter.exclude_title_keywords.join('、')}` : '',
    filter.year_from != null || filter.year_to != null ? `年份：${filter.year_from ?? '不限'}—${filter.year_to ?? '不限'}` : '',
    filter.score_state === 'unrated' ? '仅未评分' : filter.score_min != null || filter.score_max != null ? `个人评分：${filter.score_min ?? '不限'}—${filter.score_max ?? '不限'}（含端点）` : filter.score_state === 'rated' ? '仅已评分' : '',
    filter.watch_statuses?.length ? `观看：${filter.watch_statuses.map(value => WATCH_STATES[value]).join('、')}` : '',
    filter.review_state === 'with' ? '有长评' : filter.review_state === 'without' ? '无长评' : ''].filter(Boolean).join('；') || '尚未设置筛选条件'
}

export function transferQuickTag(form, key, tag) {
  if (!['tags', 'exclude_tags'].includes(key)) return form
  if (form[key].includes(tag)) return { ...form, [key]: form[key].filter(value => value !== tag) }
  if (form[key].length >= 8) return form
  const opposite = key === 'tags' ? 'exclude_tags' : 'tags'
  return { ...form, [key]: [...form[key], tag], [opposite]: form[opposite].filter(value => value !== tag) }
}

export const WATCH_STATES = { plan: '想看', watching: '在看', completed: '看过', on_hold: '搁置' }
export const quickKeywords = value => [...new Set(value.split(/[，,、\n]/).map(word => word.trim()).filter(Boolean))]
export const emptyQuickFilter = () => ({ name: '', tags: [], title_keywords: '', exclude_tags: [], exclude_title_keywords: '', match_mode: 'any', year_from: '', year_to: '', score_min: '', score_max: '', score_state: 'any', watch_statuses: [], review_state: 'any' })
export function quickFilterPayload(form) {
  const number = value => value === '' || value == null ? null : Number(value)
  return { ...form, name: form.name.trim(), title_keywords: quickKeywords(form.title_keywords), exclude_title_keywords: quickKeywords(form.exclude_title_keywords),
    year_from: number(form.year_from), year_to: number(form.year_to), score_min: number(form.score_min), score_max: number(form.score_max) }
}
export function quickFilterDraftError(form, requireName = true) {
  const data = quickFilterPayload(form)
  if (requireName && !data.name) return '请填写筛选名称。'
  if ([form.year_from, form.year_to].some(value => value !== '' && !/^[1-9]\d{3}$/.test(String(value)))) return '年份必须是 1000—9999 的四位整数。'
  if (data.year_from != null && data.year_to != null && data.year_from > data.year_to) return '年份起点不能大于终点。'
  if ([data.score_min, data.score_max].some(value => value != null && (!Number.isFinite(value) || value < 0 || value > 10 || Math.abs(value * 10 - Math.round(value * 10)) > 1e-8))) return '评分需为 0–10，最多一位小数。'
  if (data.score_min != null && data.score_max != null && data.score_min > data.score_max) return '评分下限不能高于上限。'
  if (data.score_state === 'unrated' && (data.score_min != null || data.score_max != null)) return '仅未评分不能同时设置评分区间。'
  if (data.tags.some(tag => data.exclude_tags.includes(tag))) return '标签不能同时包含与排除。'
  if (data.title_keywords.length > 4 || data.exclude_title_keywords.length > 4 || [...data.title_keywords, ...data.exclude_title_keywords].some(word => [...word].length > 30)) return '每组最多 4 个标题词，每词最多 30 字。'
  if (data.title_keywords.some(word => data.exclude_title_keywords.some(excluded => word.toUpperCase() === excluded.toUpperCase()))) return '标题词不能同时包含与排除。'
  if (!(data.tags.length || data.exclude_tags.length || data.title_keywords.length || data.exclude_title_keywords.length || data.year_from != null || data.year_to != null || data.score_min != null || data.score_max != null || data.score_state !== 'any' || data.watch_statuses.length || data.review_state !== 'any')) return '请至少设置一项筛选条件。'
  return ''
}
