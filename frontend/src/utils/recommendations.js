export const DEFAULT_REC_FILTERS = Object.freeze({ type: 'all', minScore: '0', hideRecorded: true })
export const REC_CATEGORIES = [
  { id: 'all', label: '为你发现' }, { id: 'familiar', label: '熟悉的心动' }, { id: 'explore', label: '换一种口味' },
]

export function getRecommendationCover(value) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'media.xh-anime.com' || url.username || url.password || url.port || url.hash
      || !/^\/covers\/[1-9]\d*\.jpg$/.test(url.pathname)) return ''
    if ([...url.searchParams].some(([key, content]) => key !== 'v' || !/^[a-f0-9]{16}$/.test(content))
      || url.searchParams.getAll('v').length > 1) return ''
    return url.href
  } catch { return '' }
}

export function mapRecommendationItem(row) {
  if (row?.content_rating !== 'non_adult' || !Number.isInteger(row.bangumi_id) || row.bangumi_id < 1
    || typeof row.title_zh !== 'string' || typeof row.title_jp !== 'string' || !Array.isArray(row.tags)
    || !row.tags.every((tag) => typeof tag === 'string') || !Number.isFinite(row.score) || row.score < 0 || row.score > 10) {
    throw new Error('推荐数据合同不兼容，请重试或联系管理员。')
  }
  return { id: String(row.bangumi_id), bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp,
    originalTitle: row.title_jp, year: row.season?.slice(0, 4), season: row.season, type: row.status_type,
    score: row.score, tags: row.tags, coverUrl: getRecommendationCover(row.cover_url),
    synopsis: row.synopsis || '', reason: row.reason || '', evidence: row.evidence, watchStatus: row.watch_status }
}

export function mapRecommendationPage(data) {
  if (data?.contract_version !== 'recommendations.3b.v1' || !Array.isArray(data.results)
    || !['personalized', 'preference_based', 'cold_start'].includes(data.mode)
    || !Number.isInteger(data.count) || data.count < 0 || data.count > 120 || !Number.isInteger(data.page) || data.page < 1
    || !Number.isInteger(data.page_size) || data.page_size < 1 || data.page_size > 24
    || data.results.length > data.page_size || data.results.length > data.count
    || (data.next_page !== null && data.next_page !== data.page + 1)
    || (data.mode !== 'cold_start' && !/^[a-f0-9]{64}$/.test(data.state_token))) {
    throw new Error('推荐分页合同不兼容，请重新加载。')
  }
  return { ...data, items: data.results.map(mapRecommendationItem) }
}

export function validateRecommendationProfile(data) {
  if (data?.contract_version !== 'recommendations.3a.v1' || !Number.isInteger(data.eligible_sample_count)
    || data.eligible_sample_count < 0 || typeof data.cold_start !== 'boolean' || !Array.isArray(data.tag_summary)
    || !data.tag_summary.every((entry) => typeof entry.tag === 'string' && Number.isInteger(entry.sample_count))
    || !data.sample_policy || !Number.isFinite(Number(data.sample_policy.minimum_score))) {
    throw new Error('推荐画像合同不兼容，请重新加载。')
  }
  return data
}

export function validateRecommendationPreferences(data) {
  if (data?.contract_version !== 'recommendations.3a.v1' || !Number.isInteger(data.version) || data.version < 0
    || !Array.isArray(data.tags) || data.tags.length > 20 || !Array.isArray(data.available_tags)
    || !data.available_tags.every((tag) => typeof tag === 'string') || !data.tags.every((tag) => data.available_tags.includes(tag))) {
    throw new Error('推荐偏好合同不兼容，请重新加载。')
  }
  return data
}

// Abort alone is not sufficient: mocks, retry interceptors or settled promises may still resolve.
export function createLatestRequestGate() {
  let generation = 0
  let controller
  return {
    begin() {
      controller?.abort()
      controller = new AbortController()
      const current = ++generation
      const signal = controller.signal
      return { signal, isCurrent: () => current === generation && !signal.aborted }
    },
    cancel() { generation += 1; controller?.abort() },
  }
}

export function mergeRecommendationPages(current, next) {
  const seen = new Set(current.map((item) => item.id))
  return [...current, ...next.filter((item) => { if (seen.has(item.id)) return false; seen.add(item.id); return true })]
}

export function recommendationError(error) {
  if (/^推荐.*合同不兼容/.test(error?.message || '')) return error.message
  if (error?.response?.status === 409) return '喜好或数据已变化，请重新加载后再试。'
  if (error?.response?.status === 429) return '请求较频繁，请稍后重试。'
  if (error?.response?.status === 503) return '推荐数据暂不可用，请稍后重试。'
  return '无法加载推荐数据，请检查网络并重试。'
}
