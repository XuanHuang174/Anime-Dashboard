import { sameEvidenceContext } from './recommendationEpoch.js'

export const DEFAULT_REC_FILTERS = Object.freeze({ type: 'all', minScore: '0', hideRecorded: true })
export const REC_CATEGORIES = [
  { id: 'all', label: '为你发现', description: '优先与你的口味相关，兼顾不同题材组合。' },
  { id: 'familiar', label: '熟悉的心动', description: '寻找更贴近已有口味的作品，允许题材适当集中。' },
  { id: 'explore', label: '换一种口味', description: '保留喜好关联，探索一些新的题材方向。' },
]

export const COLD_START_CODES = Object.freeze(['empty_journal', 'all_unrated', 'no_qualifying_high_scores',
  'insufficient_samples', 'pending_identity', 'missing_genres', 'unavailable_samples', 'ready'])

export function validateColdStartState(value) {
  if (value === undefined) return // Rolling deployment: never invent a missing reason.
  const counts = ['minimum_samples', 'explicit_preference_count', 'journal_record_count', 'rated_record_count',
    'unrated_record_count', 'high_score_record_count', 'eligible_sample_count', 'usable_sample_count',
    'pending_identity_count', 'missing_genre_count', 'unavailable_sample_count']
  if (value?.contract_version !== 'recommendations.cold-start.v1' || !COLD_START_CODES.includes(value.code)
    || !Array.isArray(value.reasons) || value.reasons[0] !== value.code
    || !value.reasons.every(code => COLD_START_CODES.includes(code))
    || new Set(value.reasons).size !== value.reasons.length
    || !counts.every(key => Number.isInteger(value[key]) && value[key] >= 0)
    || value.minimum_samples < 1 || value.explicit_preference_count > 20
    || typeof value.is_cold_start !== 'boolean'
    || value.rated_record_count + value.unrated_record_count !== value.journal_record_count
    || value.high_score_record_count > value.rated_record_count
    || value.usable_sample_count > value.eligible_sample_count
    || value.missing_genre_count !== value.eligible_sample_count - value.usable_sample_count
    || value.is_cold_start !== (value.usable_sample_count < value.minimum_samples)
    || (value.code === 'ready') !== !value.is_cold_start
    || value.recommendation_basis !== (!value.is_cold_start ? 'personalized'
      : value.explicit_preference_count ? 'explicit_preferences' : 'none')) {
    throw new Error('推荐冷启动合同不兼容，请重新加载。')
  }
}

export function validateRecommendationSnapshot(profile, preferences, data) {
  const state = profile.cold_start_state
  if (state || data.cold_start_state) {
    if (!state || !data.cold_start_state || JSON.stringify(state) !== JSON.stringify(data.cold_start_state)
      || JSON.stringify(profile.extraction.explicit_preferences.tags) !== JSON.stringify(preferences.tags)
      || profile.extraction.explicit_preferences.version !== preferences.version) {
      throw new Error('推荐画像合同不兼容，数据已变化，请重新加载。')
    }
  }
}

// Recommendation transport state stays separate from profile readiness.
export function recommendationResultState(data) {
  return data.mode === 'cold_start' ? 'cold' : data.items.length ? 'ready' : 'empty'
}

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
    || !row.tags.every((tag) => typeof tag === 'string')
    || (row.score !== null && (!Number.isFinite(row.score) || row.score < 0 || row.score > 10))
    || (row.evidence_token !== undefined && !/^[a-f0-9]{64}$/.test(row.evidence_token))) {
    throw new Error('推荐数据合同不兼容，请重试或联系管理员。')
  }
  return { id: String(row.bangumi_id), bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp,
    originalTitle: row.title_jp, year: row.season?.slice(0, 4), season: row.season, type: row.status_type,
    score: row.score, tags: row.tags, coverUrl: getRecommendationCover(row.cover_url),
    synopsis: row.synopsis || '', reason: row.reason || '', evidence: row.evidence, watchStatus: row.watch_status,
    evidenceToken: row.evidence_token, evidenceContext: row.evidence_context }
}

export function mapRecommendationPage(data) {
  if (data?.contract_version !== 'recommendations.3b.v1' || !Array.isArray(data.results)
    || !['personalized', 'preference_based', 'cold_start'].includes(data.mode)
    || (data.algorithm_version !== undefined && !['content.v1', 'content.v2'].includes(data.algorithm_version))
    || !Number.isInteger(data.count) || data.count < 0 || data.count > 120 || !Number.isInteger(data.page) || data.page < 1
    || !Number.isInteger(data.page_size) || data.page_size < 1 || data.page_size > 24
    || data.results.length > data.page_size || data.results.length > data.count
    || (data.next_page !== null && data.next_page !== data.page + 1)
    || (data.mode !== 'cold_start' && !/^[a-f0-9]{64}$/.test(data.state_token))) {
    throw new Error('推荐分页合同不兼容，请重新加载。')
  }
  validateColdStartState(data.cold_start_state)
  if (data.evidence_context !== undefined && (data.evidence_context?.algorithm_version !== data.algorithm_version
    || data.results.some(row => !sameEvidenceContext(row.evidence_context, data.evidence_context)))) {
    throw new Error('推荐分页合同不兼容，卡片依据与当前页面不一致，请重新加载。')
  }
  if (data.cold_start_state && data.mode !== ({ personalized: 'personalized',
    explicit_preferences: 'preference_based', none: 'cold_start' }[data.cold_start_state.recommendation_basis])) {
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
  if (data.diagnostics && !['high_score_record_count', 'pending_identity_count', 'linked_high_score_record_count',
    'unavailable_identity_count', 'stale_identity_count', 'conflict_identity_count'].every((key) =>
    Number.isInteger(data.diagnostics[key]) && data.diagnostics[key] >= 0)) throw new Error('推荐画像合同不兼容，请重新加载。')
  validateColdStartState(data.cold_start_state)
  if (data.cold_start_state) {
    const cold = data.cold_start_state
    if (cold.usable_sample_count !== data.usable_sample_count || cold.eligible_sample_count !== data.eligible_sample_count
      || cold.is_cold_start !== data.cold_start || cold.minimum_samples !== data.sample_policy.minimum_samples
      || !Array.isArray(data.extraction?.explicit_preferences?.tags)
      || cold.explicit_preference_count !== data.extraction.explicit_preferences.tags.length) {
      throw new Error('推荐画像合同不兼容，请重新加载。')
    }
  }
  if (data.extraction?.preference_policy) {
    const { preference_policy: policy, trusted_profile: trusted, common_topics: common } = data.extraction
    const topics = value => Array.isArray(value) && value.every(item => typeof item.tag === 'string'
      && Number.isInteger(item.sample_count) && item.sample_count > 0)
    if (policy.maximum_tags !== 20 || policy.maximum_observed_topics !== 8
      || policy.common_topics_require_confirmation !== true || policy.explicit_preferences_are_hard_filters !== false
      || !Number.isInteger(trusted?.usable_work_count) || trusted.usable_work_count !== data.usable_sample_count
      || !topics(trusted.tag_summary) || !topics(common?.tag_summary)
      || common.requires_confirmation !== true || common.used_for_recommendation !== false) {
      throw new Error('推荐画像合同不兼容，请重新加载。')
    }
  }
  return data
}

export function validateIdentityReview(data) {
  if (!Number.isInteger(data?.count) || data.count < 0 || !Number.isInteger(data.page) || data.page < 1
    || !Array.isArray(data.results) || data.results.length > 10 || (data.next_page !== null && data.next_page !== data.page + 1)
    || !data.results.every((row) => Number.isInteger(row.record_id) && row.record_id > 0
      && typeof row.title === 'string' && Number.isInteger(row.identity_version) && Array.isArray(row.candidates)
      && row.candidates.every((item) => item.content_rating === 'non_adult' && Number.isInteger(item.bangumi_id)
        && item.bangumi_id > 0 && typeof item.ticket === 'string' && typeof item.title_zh === 'string'))) {
    throw new Error('推荐身份核对合同不兼容，请重新加载。')
  }
  return data
}

export function identitySummary(profile) {
  const diagnostics = profile?.diagnostics
  if (!diagnostics || !diagnostics.pending_identity_count) return ''
  return `手账已有 ${diagnostics.high_score_record_count} 条高分记录，其中 ${diagnostics.pending_identity_count} 条尚待关联标准作品，暂未计入画像。无需重新评分。`
}

export function validateRecommendationPreferences(data) {
  if (data?.contract_version !== 'recommendations.3a.v1' || !Number.isInteger(data.version) || data.version < 0
    || !Array.isArray(data.tags) || data.tags.length > 20 || !Array.isArray(data.available_tags)
    || !data.available_tags.every((tag) => typeof tag === 'string') || !data.tags.every((tag) => data.available_tags.includes(tag))
    || new Set(data.tags).size !== data.tags.length || new Set(data.available_tags).size !== data.available_tags.length) {
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
