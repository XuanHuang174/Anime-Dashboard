// Dependency injection keeps test fixtures out of the production bundle.
export function createRecommendationClient(client) {
  return {
    profile: (signal) => client.get('recommendations/profile/', { signal }).then((r) => r.data),
    preferences: (signal) => client.get('recommendations/preferences/', { signal }).then((r) => r.data),
    savePreferences: (changes, signal) => client.patch('recommendations/preferences/', changes, { signal }).then((r) => r.data),
    list: ({ category, filters, page = 1, stateToken }, signal) => client.get('recommendations/', { signal,
      params: { category, type: filters.type, min_score: filters.minScore, hide_recorded: filters.hideRecorded,
        page, page_size: 12, ...(stateToken ? { state_token: stateToken } : {}) } }).then((r) => r.data),
    detail: (identity, signal) => {
      if (!Number.isInteger(identity) || identity < 1) return Promise.reject(new Error('无效作品身份'))
      return client.get(`recommendations/${identity}/`, { signal }).then((r) => r.data)
    },
  }
}
