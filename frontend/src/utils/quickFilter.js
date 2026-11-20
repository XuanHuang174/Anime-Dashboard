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
  if (filter.year_from != null || filter.year_to != null) {
    const year = journalAiringYear(item.period)
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
    return filter.tags.every((tag) => tags.includes(tag))
      && titleKeywords.every(
        hasKeyword,
      )
  }

  return filter.tags.some((tag) => tags.includes(tag))
    || titleKeywords.some(
        hasKeyword,
    )
}

export function quickFilterSummary(filter) {
  if (!filter) return ''
  const included = [...filter.tags, ...(filter.title_keywords ?? []).map(word => `标题:${word}`)]
  return [included.length ? `${filter.match_mode === 'all' ? '全部满足' : '任意匹配'}：${included.join('、')}` : '全部作品',
    filter.exclude_tags?.length ? `排除标签：${filter.exclude_tags.join('、')}` : '',
    filter.exclude_title_keywords?.length ? `排除标题词：${filter.exclude_title_keywords.join('、')}` : '',
    filter.year_from != null || filter.year_to != null ? `年份：${filter.year_from ?? '不限'}—${filter.year_to ?? '不限'}` : ''].filter(Boolean).join('；')
}
