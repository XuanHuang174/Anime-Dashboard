// Presentation only: never infer evidence from titles, synopsis, tags or reviews.
import { RECOMMENDATION_COPY as COPY } from './recommendationCopy.js'
import { personalScoreValue } from './recommendationScores.js'

function hasCompatibleEvidenceContext(item) {
  const evidence = item?.evidence
  // 5C rolling compatibility only when ALL 6A metadata is absent.
  if (item?.evidenceToken === undefined && item?.evidenceContext === undefined
    && evidence?.contract_version === undefined && evidence?.versions === undefined) return true
  const versions = evidence?.versions
  const keys = ['algorithm_version', 'ranking_policy_version', 'taxonomy_version', 'feature_version',
    'feature_rule_version', 'selection_rule_version', 'profile_version', 'preference_version', 'catalog_feature_version']
  return /^[a-f0-9]{64}$/.test(item.evidenceToken || '')
    && evidence?.contract_version === 'recommendations.evidence.v1'
    && evidence.interpretation === 'shared_genre_association' && evidence.is_probability === false
    && evidence.is_exhaustive_ranking_explanation === false
    && keys.every(key => versions?.[key] !== undefined && versions[key] === item.evidenceContext?.[key])
    && keys.filter(key => key !== 'preference_version').every(key => typeof versions[key] === 'string' && versions[key].trim())
    && evidence.feature_rule_version === versions.feature_rule_version
    && ['content.v1', 'content.v2'].includes(versions.algorithm_version)
    && ['profile_version', 'catalog_feature_version'].every(key => /^[a-f0-9]{64}$/.test(versions[key]))
    && Number.isInteger(versions.preference_version) && versions.preference_version >= 0
}

export function recommendationEvidence(item) {
  const evidence = item?.evidence
  const tags = evidence?.shared_tags
  if (!hasCompatibleEvidenceContext(item) || typeof item?.reason !== 'string' || !item.reason.trim()
    || !Array.isArray(tags) || !tags.length || !Array.isArray(item.tags)
    || !tags.every(tag => typeof tag === 'string' && tag.trim() && item.tags?.includes(tag))
    || new Set(tags).size !== tags.length) return null
  if (evidence.kind === 'explicit_preferences' && evidence.source === null) {
    return { kind: 'explicit_preferences', tags }
  }
  const source = evidence.source
  if (evidence.kind !== 'rated_work' || !Number.isInteger(source?.bangumi_id) || source.bangumi_id < 1
    || typeof source.title !== 'string' || !source.title.trim() || source.title.length > 500) return null
  const score = personalScoreValue(source.personal_score)
  // 0 is a real personal rating, but is NOT a high-score evidence source.
  if (score === null || score < 8.5) return null
  return { kind: 'rated_work', tags, source: { bangumiId: source.bangumi_id, title: source.title, score } }
}

export function recommendationReason(item) {
  return recommendationEvidence(item) && typeof item.reason === 'string' && item.reason.trim()
    ? item.reason : COPY.evidenceUnavailable
}

export function recommendationAlgorithmLabel(version) {
  if (version === 'content.v1') return '推荐 V1'
  if (version === 'content.v2') return '推荐 V2'
  return '推荐'
}

export function validateDetailEvidence(item, expectedToken) {
  if (expectedToken && item.evidenceToken !== expectedToken) {
    throw new Error('推荐依据合同不兼容，请重新加载推荐后查看详情。')
  }
  return item
}
