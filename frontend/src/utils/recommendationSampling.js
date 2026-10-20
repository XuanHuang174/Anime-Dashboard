// Sampling contracts only. Never infer preference, identity or score from content.
export const PROFILE_POLICY_VERSION = 'profile-sampling.v1'
export const PROFILE_SCORE_CHOICES = Object.freeze(Array.from({ length: 9 }, (_, n) => (6 + n * .5).toFixed(1)))

export function profileScore(value) {
  if (!['string', 'number'].includes(typeof value) || !/^(?:[6-9](?:\.[05])?|10(?:\.0)?)$/.test(String(value))) return null
  return Number(value).toFixed(1)
}

export function samplingPreferences(data) {
  const fields = ['profile_min_score', 'effective_profile_min_score', 'profile_policy_version', 'profile_min_score_control']
  if (fields.every(key => data?.[key] === undefined)) return null // Pre-P1 rolling compatibility.
  const score = profileScore(data?.profile_min_score), effective = profileScore(data?.effective_profile_min_score)
  const control = data?.profile_min_score_control
  if (!score || !effective || data.profile_policy_version !== PROFILE_POLICY_VERSION
    || typeof control?.enabled !== 'boolean' || profileScore(control.default) === null
    || control.minimum !== '6.0' || control.maximum !== '10.0' || control.step !== '0.5'
    || !Array.isArray(control.choices) || control.choices.length !== PROFILE_SCORE_CHOICES.length
    || control.choices.some((value, n) => value !== PROFILE_SCORE_CHOICES[n])
    || effective !== (control.enabled ? score : profileScore(control.default))) {
    throw new Error('推荐偏好合同不兼容，画像取样设置需要重新读取。')
  }
  return { score, effective, version: PROFILE_POLICY_VERSION, control }
}

export function validateSamplingPolicy(policy, { preview = false } = {}) {
  const fields = ['profile_policy_version', 'configured_minimum_score', 'customization_enabled', 'source']
  if (!preview && fields.every(key => policy?.[key] === undefined)) return null
  const effective = profileScore(policy?.minimum_score), configured = profileScore(policy?.configured_minimum_score)
  if (policy?.profile_policy_version !== PROFILE_POLICY_VERSION || !effective || !configured
    || typeof policy.customization_enabled !== 'boolean' || policy.minimum_samples !== 3
    || policy.watch_status !== 'completed' || policy.requires_safe_bangumi_identity !== true || policy.requires_usable_tags !== true
    || policy.source !== (preview ? 'unsaved_preview' : policy.customization_enabled ? 'user_setting' : 'server_default')
    || !preview && policy.customization_enabled && effective !== configured) {
    throw new Error('推荐画像合同不兼容，取样依据需要重新读取。')
  }
  return { effective, configured, version: PROFILE_POLICY_VERSION, enabled: policy.customization_enabled }
}

export function validateProfilePreview(data, score, preferences) {
  const saved = samplingPreferences(preferences), policy = validateSamplingPolicy(data?.sample_policy, { preview: true })
  if (!saved || data?.contract_version !== 'recommendations.profile-preview.v1' || data.is_preview !== true || data.saved !== false
    || data.preference_version !== preferences.version || profileScore(data.profile_min_score) !== score
    || policy.effective !== score || policy.configured !== saved.score || policy.enabled !== saved.control.enabled
    || !Number.isInteger(data.eligible_sample_count) || !Number.isInteger(data.usable_sample_count)
    || data.usable_sample_count < 0 || data.eligible_sample_count < data.usable_sample_count
    || data.cold_start !== (data.usable_sample_count < 3)
    || data.cold_start_state?.usable_sample_count !== data.usable_sample_count
    || data.cold_start_state?.eligible_sample_count !== data.eligible_sample_count
    || data.cold_start_state?.is_cold_start !== data.cold_start
    || !Number.isInteger(data.diagnostics?.pending_identity_count) || data.diagnostics.pending_identity_count < 0) {
    throw new Error('推荐预览合同不兼容，设置或画像依据已变化，请重新读取设置。')
  }
  return data
}

export function evidenceSamplingScore(versions) {
  if (versions?.profile_min_score === undefined && versions?.profile_policy_version === undefined) return '8.5'
  return versions?.profile_policy_version === PROFILE_POLICY_VERSION ? profileScore(versions.profile_min_score) : null
}

export function sameSavedSampling(left, right) {
  const a = samplingPreferences(left), b = samplingPreferences(right)
  return !a && !b || Boolean(a && b && a.score === b.score && a.version === b.version)
}
