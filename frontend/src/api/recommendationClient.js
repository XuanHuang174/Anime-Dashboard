// Dependency injection keeps test fixtures out of the production bundle.
import { validDetailContext } from '../utils/recommendationExecution.js'
export function createRecommendationClient(client) {
  return {
    capabilities: signal => client.get('recommendations/capabilities/', { signal }).then(r => r.data),
    profile: (signal) => client.get('recommendations/profile/', { signal }).then((r) => r.data),
    preferences: (signal) => client.get('recommendations/preferences/', { signal }).then((r) => r.data),
    savePreferences: (changes, signal) => client.patch('recommendations/preferences/', changes, { signal }).then((r) => r.data),
    identities: (params, signal) => client.get('recommendations/identities/', { params, signal }).then((r) => r.data),
    confirmIdentity: (changes, signal) => client.patch('recommendations/identities/', changes, { signal }).then((r) => r.data),
    confirmIdentities: (changes, signal) => client.post('recommendations/identities/batch/', changes, { signal }).then(r => r.data),
    hideFeedback: (changes, signal) => client.post('recommendations/feedback/', changes, { signal }).then(r => r.data),
    undoFeedback: (changes, signal) => client.post('recommendations/feedback/undo/', changes, { signal }).then(r => r.data),
    hiddenFeedback: (params, signal) => client.get('recommendations/feedback/hidden/', { params, signal }).then(r => r.data),
    want: (identity, signal) => {
      if (!Number.isInteger(identity) || identity < 1 || identity > 2147483647) return Promise.reject(new Error('无效作品身份'))
      return client.post(`recommendations/${identity}/want/`, {}, { signal }).then((r) => r.data)
    },
    list: ({ category, filters, page = 1, stateToken }, signal) => client.get('recommendations/', { signal,
      params: { category, type: filters.type, min_score: filters.minScore, hide_recorded: filters.hideRecorded,
        page, page_size: 12, ...(stateToken ? { state_token: stateToken } : {}) } }).then((r) => r.data),
    browse: ({ category, style, filters, cursor }, signal) => client.get('recommendations/', { signal,
      params: cursor ? { cursor } : { browse: true, category, style, type: filters.type,
        min_score: filters.minScore, hide_recorded: filters.hideRecorded, page_size: 12,
        ...(filters.yearFrom ? { year_from: filters.yearFrom } : {}),
        ...(filters.yearTo ? { year_to: filters.yearTo } : {}) } }).then(r => r.data),
    detail: (identity, signal, evidenceToken, detailContext) => {
      if (!Number.isInteger(identity) || identity < 1) return Promise.reject(new Error('无效作品身份'))
      if (evidenceToken !== undefined && !/^[a-f0-9]{64}$/.test(evidenceToken)) return Promise.reject(new Error('推荐依据合同不兼容'))
      if (detailContext !== undefined && !validDetailContext(detailContext)) return Promise.reject(new Error('推荐依据合同不兼容'))
      return client.get(`recommendations/${identity}/`, { signal,
        ...(evidenceToken || detailContext ? { params: { ...(evidenceToken ? { evidence_token: evidenceToken } : {}),
          ...(detailContext ? { detail_context: detailContext } : {}) } } : {}) }).then((r) => r.data)
    },
  }
}
