// Dependency injection keeps test fixtures out of the production bundle.
import { validDetailContext } from '../utils/recommendationExecution.js'
import { checkScopeResponse } from '../utils/recommendationContentScope.js'
export function createRecommendationClient(transport, scope = 'safe', onLost = () => {}) {
  if (!['safe', 'include_adult'].includes(scope)) throw new Error('未知内容范围')
  const scoped = path => /^recommendations\/(?:$|context\/|explore\/|feedback\/|identities\/|[1-9]\d*\/)/.test(path)
  let lastScopeRevision = null, sequence = 0, lastScopeSequence = 0
  const call = (method, path, args, config = {}) => {
    const order = ++sequence
    const controlled = scoped(path)
    if (controlled && scope === 'include_adult') config = { ...config, params: { ...config.params, content_scope: scope } }
    const promise = method === 'get' ? transport.get(path, config) : transport[method](path, args, config)
    return promise.then(response => {
      if (config.signal?.aborted) { const error = new Error('请求已取消'); error.name = 'AbortError'; throw error }
      if (controlled) checkScopeResponse(response.data, scope)
      if (controlled && scope === 'include_adult') {
        const revision = response.data.content_scope_context.scope_revision
        if (lastScopeRevision && revision !== lastScopeRevision && order < lastScopeSequence) {
          const error = new Error('旧内容范围响应已丢弃'); error.name = 'AbortError'; throw error
        }
        if (path === 'recommendations/context/' && lastScopeRevision && revision !== lastScopeRevision) {
          const error = new Error('访问范围已更新'); error.scopeLost = true; throw error
        }
        if (order >= lastScopeSequence) { lastScopeRevision = revision; lastScopeSequence = order }
      }
      return response
    }).catch(error => {
      const explorationChanged = path.startsWith('recommendations/explore/') && error.response?.status === 409
        && error.response?.data?.code === 'exploration_snapshot_changed'
      if (controlled && scope === 'include_adult' && !config.signal?.aborted
        && (error.scopeLost || error.response?.status === 403 || error.response?.status === 409 && !explorationChanged)) onLost()
      throw error
    })
  }
  const client = { get: (p, c) => call('get', p, null, c), post: (p, a, c) => call('post', p, a, c), patch: (p, a, c) => call('patch', p, a, c) }
  return {
    forScope: (value, callback) => createRecommendationClient(transport, value, callback),
    eligibility: signal => client.get('recommendations/eligibility/', { signal }).then(r => r.data),
    applyEligibility: (changes, signal) => client.post('recommendations/eligibility/apply/', changes, { signal }).then(r => r.data),
    capabilities: signal => client.get('recommendations/capabilities/', { signal }).then(r => r.data),
    explorationContract: signal => client.get('recommendations/explore/contract/', { signal }).then(r => r.data),
    explorationOptions: signal => client.get('recommendations/explore/options/', { signal }).then(r => r.data),
    explorationRecord: (recordId, signal) => {
      if (!Number.isSafeInteger(recordId) || recordId < 1) return Promise.reject(new Error('手账记录身份无效'))
      return client.get(`recommendations/explore/record/${recordId}/`, { signal }).then(r => r.data)
    },
    explorationCatalog: (q, signal) => client.get('recommendations/explore/catalog/', { params: { q }, signal }).then(r => r.data),
    explorationBrowse: (query, signal) => client.get('recommendations/explore/', {
      params: query.cursor ? { cursor: query.cursor } : query.intent === 'tags'
        ? { ...query, include_tags: query.include_tags.join(','), exclude_tags: query.exclude_tags.length ? query.exclude_tags.join(',') : undefined } : query, signal }).then(r => r.data),
    explorationContext: (snapshotContext, signal) => {
      if (typeof snapshotContext !== 'string' || !snapshotContext.length || snapshotContext.length > 8192) return Promise.reject(new Error('探索上下文无效'))
      return client.get('recommendations/explore/context/', { params: { snapshot_context: snapshotContext }, signal }).then(r => r.data)
    },
    explorationDetail: (identity, detailContext, signal, evidenceToken) => {
      if (!Number.isInteger(identity) || identity < 1 || identity > 2147483647
        || typeof detailContext !== 'string' || !detailContext.length || detailContext.length > 8192
        || evidenceToken !== undefined && !/^[a-f0-9]{64}$/.test(evidenceToken)) return Promise.reject(new Error('探索详情上下文无效'))
      return client.get(`recommendations/explore/${identity}/`, { signal, params: { detail_context: detailContext,
        ...(evidenceToken ? { evidence_token: evidenceToken } : {}) } }).then(r => r.data)
    },
    context: signal => client.get('recommendations/context/', { signal }).then(r => r.data),
    profile: (signal) => client.get('recommendations/profile/', { signal }).then((r) => r.data),
    previewProfile: (profileMinScore, signal) => client.get('recommendations/profile/preview/', {
      params: { profile_min_score: profileMinScore }, signal }).then(r => r.data),
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
      params: { category, type: filters.type, min_score: filters.minScore === 'lt6' ? '0' : filters.minScore,
        ...(filters.minScore === 'lt6' ? { max_score: '6.0', max_score_exclusive: true } : {}), hide_recorded: filters.hideRecorded,
        page, page_size: 12, ...(stateToken ? { state_token: stateToken } : {}) } }).then((r) => r.data),
    browse: ({ category, style, filters, cursor }, signal) => client.get('recommendations/', { signal,
      params: cursor ? { cursor } : { browse: true, category, style, type: filters.type,
        min_score: filters.minScore === 'lt6' ? '0' : filters.minScore,
        ...(filters.minScore === 'lt6' ? { max_score: '6.0', max_score_exclusive: true } : {}), hide_recorded: filters.hideRecorded, page_size: 12,
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
