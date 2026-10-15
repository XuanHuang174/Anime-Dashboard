// Presentation only: never infer evidence from titles, synopsis, tags or reviews.
export function recommendationEvidence(item) {
  const evidence = item?.evidence
  const tags = evidence?.shared_tags
  if (!Array.isArray(tags) || !tags.length
    || !tags.every(tag => typeof tag === 'string' && tag.trim() && item.tags?.includes(tag))
    || new Set(tags).size !== tags.length) return null
  if (evidence.kind === 'explicit_preferences' && evidence.source === null) {
    return { kind: 'explicit_preferences', tags }
  }
  const source = evidence.source
  if (evidence.kind !== 'rated_work' || !Number.isInteger(source?.bangumi_id) || source.bangumi_id < 1
    || typeof source.title !== 'string' || !source.title.trim() || source.title.length > 500
    || !['string', 'number'].includes(typeof source.personal_score)
    || !/^\d{1,2}(?:\.\d)?$/.test(String(source.personal_score))) return null
  const score = Number(source.personal_score)
  if (!Number.isFinite(score) || score < 8.5 || score > 10) return null
  return { kind: 'rated_work', tags, source: { bangumiId: source.bangumi_id, title: source.title, score } }
}

export function recommendationReason(item) {
  return recommendationEvidence(item) && typeof item.reason === 'string' && item.reason.trim()
    ? item.reason : '推荐依据暂不可用，请刷新推荐后重试。'
}

export function recommendationAlgorithmLabel(version) {
  if (version === 'content.v1') return '推荐 V1'
  if (version === 'content.v2') return '推荐 V2'
  return '推荐'
}
