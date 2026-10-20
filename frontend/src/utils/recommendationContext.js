// Compare only server-owned own-profile inputs; no persistence/private payload.
import { evidenceSamplingScore } from './recommendationSampling.js'
export const CONTEXT_KEYS = Object.freeze(['algorithm_version', 'ranking_policy_version', 'taxonomy_version',
  'feature_version', 'feature_rule_version', 'selection_rule_version', 'profile_version', 'preference_version',
  'feedback_version', 'profile_policy_version', 'profile_min_score'])

export function contextIdentity(current) {
  if (!current?.context || typeof current.context !== 'object') return null
  return JSON.stringify([current.rollout_revision ?? null, current.browse_enabled ?? null,
    ...CONTEXT_KEYS.map(key => current.context[key] ?? null)])
}

export function contextChanged(data, current) {
  if (data?.contract_version !== 'recommendations.context.v1' || !/^[a-f0-9]{64}$/.test(data.rollout_revision)
    || typeof data.browse_enabled !== 'boolean'
    || !data.context || !CONTEXT_KEYS.every(key => ['preference_version', 'feedback_version'].includes(key)
      ? Number.isInteger(data.context[key]) && data.context[key] >= 0 : typeof data.context[key] === 'string' && data.context[key].length > 0)
    || !['content.v1', 'content.v2'].includes(data.context.algorithm_version)
    || evidenceSamplingScore(data.context) === null
    || !/^[a-f0-9]{64}$/.test(data.context.profile_version)) throw new Error('推荐上下文校验暂不可用')
  return CONTEXT_KEYS.some(key => data.context[key] !== current.context[key])
    || Boolean(current.rollout_revision && current.rollout_revision !== data.rollout_revision)
    || typeof current.browse_enabled === 'boolean' && current.browse_enabled !== data.browse_enabled
}
