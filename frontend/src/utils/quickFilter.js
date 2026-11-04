export function matchesCustomQuickFilter(item, filter) {
  if (!filter) return true

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
    filter.exclude_title_keywords?.length ? `排除标题词：${filter.exclude_title_keywords.join('、')}` : ''].filter(Boolean).join('；')
}
