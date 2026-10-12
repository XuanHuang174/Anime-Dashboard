// Legacy UI fixtures for regression tests only, not the production request layer.
export const DEFAULT_REC_FILTERS = Object.freeze({ type: 'all', minScore: '0', hideRecorded: false })
export const REC_CATEGORIES = [
  { id: 'all', label: '为你发现' },
  { id: 'familiar', label: '熟悉的心动' },
  { id: 'explore', label: '换一种口味' },
]
export const REC_SCENARIOS = [
  { id: 'ready', label: '正常结果' }, { id: 'loading', label: '加载中' },
  { id: 'cold', label: '喜好样本不足' }, { id: 'empty', label: '空结果' },
  { id: 'error', label: '请求失败' },
]

export function getRecommendationCover(value) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'media.xh-anime.com'
      || url.username || url.password || url.port || url.hash
      || !/^\/covers\/[1-9]\d*\.jpg$/.test(url.pathname)) return ''
    for (const [key, content] of url.searchParams) {
      if (key !== 'v' || !/^[a-f0-9]{16}$/.test(content)) return ''
    }
    return url.href
  } catch { return '' }
}

export function filterRecommendationPreview(items, category, filters, records = {}) {
  return items.filter((item) => item.nsfw === false
    && (category === 'all' || item.lane === category)
    && (filters.type === 'all' || item.type === filters.type)
    && Number.isFinite(item.score) && item.score >= Number(filters.minScore)
    && (!filters.hideRecorded || !Object.hasOwn(records, item.id)))
}

export function getRecommendationViewState(scenario, count) {
  if (scenario === 'loading' || scenario === 'retrying') return 'loading'
  if (scenario === 'cold' || scenario === 'error' || scenario === 'empty') return scenario
  return count ? 'ready' : 'empty'
}

export function addPreviewWant(records, item) {
  // Never change an existing status/rating/review, even on a repeated click.
  if (!item || item.nsfw !== false || !item.id?.startsWith('demo-')
      || Object.hasOwn(records, item.id)) return records
  return { ...records, [item.id]: { status: 'want', score: null, review: '', demoOnly: true } }
}
