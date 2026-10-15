// Compare opaque revisions only. Never derive identities, scores or preferences.
export function sameEvidenceContext(left, right) {
  if (left === undefined && right === undefined) return true // pre-6A rolling compatibility
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object'
    || Array.isArray(left) || Array.isArray(right)) return false
  const keys = Object.keys(left).sort()
  return keys.length > 0 && keys.length === Object.keys(right).length
    && keys.every(key => Object.hasOwn(right, key) && left[key] === right[key])
}

export function requireNextRecommendationEpoch(current, next) {
  if (next.page !== current.next_page || next.page_size !== current.page_size
    || next.state_token !== current.state_token
    || ['algorithm_version', 'taxonomy_version', 'feature_version', 'evidence_contract_version', 'mode', 'count']
      .some(key => current[key] !== next[key])
    || !sameEvidenceContext(current.evidence_context, next.evidence_context)) {
    const error = new Error('推荐分页合同不兼容，依据已变化，请重新加载。')
    error.snapshotChanged = true
    throw error
  }
}
