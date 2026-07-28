export function matchesCustomQuickFilter(item, filter) {
  if (!filter) return true

  const tags = item.tags ?? []
  const title = item.title ?? ''
  const titleKeywords = filter.title_keywords ?? []

  if (filter.match_mode === 'all') {
    return filter.tags.every((tag) => tags.includes(tag))
      && titleKeywords.every(
        (keyword) => title.toUpperCase().includes(keyword.toUpperCase()),
      )
  }

  return filter.tags.some((tag) => tags.includes(tag))
    || titleKeywords.some(
      (keyword) => title.toUpperCase().includes(keyword.toUpperCase()),
    )
}
