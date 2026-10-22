export const CONTROLLED_SCOPE_POLICY = 'content-scope.controlled.m2.v1'
export const GENERAL_ADULT_SCOPE_POLICY = 'content-scope.general-adult.v1'
export function controlledScope(scope) {
  const session = scope?.content_scope_policy_version === GENERAL_ADULT_SCOPE_POLICY
  return scope?.content_scope === 'include_adult'
    && scope.content_scope_contract_version === 'recommendations.content-scope.n2-2.v1'
    && (session || scope.content_scope_policy_version === CONTROLLED_SCOPE_POLICY)
    && (!session || scope.content_release_policy_version === 'release-review.general-adult.v2')
    && Number.isInteger(scope.eligibility_version) && scope.eligibility_version >= (session ? 0 : 1)
    && Number.isInteger(scope.content_review_version) && scope.content_review_version > 0
    && Number.isInteger(scope.serving_control_version) && scope.serving_control_version > 0
    && typeof scope.release_revision === 'string' && /^[a-f0-9]{64}$/.test(scope.release_revision)
    && /^[a-f0-9]{64}$/.test(scope.scope_revision || '')
    // Only the current self-service policy can explicitly have no review deadline.
    // Missing fields and legacy manual grants must still fail closed.
    && (session && scope.scope_valid_until === null
      || typeof scope.scope_valid_until === 'string' && Date.parse(scope.scope_valid_until) > Date.now())
}

export function checkScopeResponse(data, requested) {
  const scope = data?.content_scope_context
  if (requested === 'include_adult' ? !controlledScope(scope) : scope && scope.content_scope !== 'safe') {
    const error = new Error('内容范围已变化，已停止展示旧结果。'); error.scopeLost = true; throw error
  }
  if (requested === 'safe' && (data?.content_rating === 'adult' || data?.results?.some(row => row.content_rating === 'adult'))) {
    const error = new Error('安全范围返回了不兼容内容。'); error.scopeLost = true; throw error
  }
  return data
}
