export const choices = Array.from({ length: 9 }, (_, n) => (6 + n * .5).toFixed(1))
export const preferences = (score = '8.5', version = 1, tags = ['日常'], enabled = true) => ({
  contract_version: 'recommendations.3a.v1', taxonomy_version: 'genres.v8', version, tags,
  available_tags: ['日常', '音乐', '科幻', '治愈'], profile_min_score: score,
  effective_profile_min_score: enabled ? score : '8.5', profile_policy_version: 'profile-sampling.v1',
  profile_min_score_control: { enabled, default: '8.5', minimum: '6.0', maximum: '10.0', step: '0.5', choices },
})
export const coldState = (count = 3, tags = 1) => ({
  contract_version: 'recommendations.cold-start.v1', code: count >= 3 ? 'ready' : 'insufficient_samples',
  reasons: [count >= 3 ? 'ready' : 'insufficient_samples'], is_cold_start: count < 3,
  recommendation_basis: count >= 3 ? 'personalized' : tags ? 'explicit_preferences' : 'none',
  minimum_samples: 3, explicit_preference_count: tags, journal_record_count: 4, rated_record_count: 4,
  unrated_record_count: 0, high_score_record_count: count, eligible_sample_count: count, usable_sample_count: count,
  pending_identity_count: 0, missing_genre_count: 0, unavailable_sample_count: 0,
})
export const policy = (prefs = preferences(), score = prefs.effective_profile_min_score, preview = false) => ({
  profile_policy_version: 'profile-sampling.v1', watch_status: 'completed', minimum_score: score,
  configured_minimum_score: prefs.profile_min_score, customization_enabled: prefs.profile_min_score_control.enabled,
  source: preview ? 'unsaved_preview' : prefs.profile_min_score_control.enabled ? 'user_setting' : 'server_default',
  minimum_samples: 3, requires_safe_bangumi_identity: true, requires_usable_tags: true,
})
export const previewData = (score, prefs = preferences(), count = 3) => ({
  contract_version: 'recommendations.profile-preview.v1', is_preview: true, saved: false,
  profile_min_score: score, preference_version: prefs.version, sample_policy: policy(prefs, score, true),
  eligible_sample_count: count, usable_sample_count: count, cold_start: count < 3, cold_start_state: coldState(count, prefs.tags.length),
  readiness: count >= 3 ? 'ready' : 'needs_more_samples', diagnostics: { pending_identity_count: 0 },
})
export const profileData = (prefs = preferences(), count = 3) => ({
  contract_version: 'recommendations.3a.v1', eligible_sample_count: count, usable_sample_count: count,
  cold_start: count < 3, cold_start_state: coldState(count, prefs.tags.length), tag_summary: [{ tag: '音乐', sample_count: count }],
  sample_policy: policy(prefs), extraction: { explicit_preferences: prefs,
    trusted_profile: { usable_work_count: count, tag_summary: [{ tag: '音乐', sample_count: count }] },
    common_topics: { requires_confirmation: true, used_for_recommendation: false, tag_summary: [] } },
})
export const versions = (score = '7.0', version = 1) => ({ algorithm_version: 'content.v1', ranking_policy_version: 'frozen-v1',
  taxonomy_version: 'genres.v8', feature_version: 'genres-idf.v4', feature_rule_version: 'journal-genres.v3',
  selection_rule_version: 'association-evidence.v1', profile_version: 'c'.repeat(64), preference_version: version,
  catalog_feature_version: 'd'.repeat(64), profile_policy_version: 'profile-sampling.v1', profile_min_score: score })
export function pageData(prefs = preferences()) {
  const context = versions(prefs.effective_profile_min_score, prefs.version)
  return { contract_version: 'recommendations.3b.v1', algorithm_version: 'content.v1',
    mode: 'personalized', count: 0, page: 1, page_size: 12, next_page: null, state_token: 'a'.repeat(64), results: [],
    evidence_context: context, cold_start_state: coldState(3, prefs.tags.length) }
}
export function itemData(score = '7.0', minimumScore = score) {
  const context = versions(minimumScore)
  return { id: '100', bangumiId: 100, title: '推荐作品', tags: ['音乐'], reason: `你给来源作品打了 ${score} 分，共有音乐题材。`,
    evidenceToken: 'a'.repeat(64), evidenceContext: context, evidence: { kind: 'rated_work', contract_version: 'recommendations.evidence.v1',
      source: { bangumi_id: 1, title: '来源作品', personal_score: score }, shared_tags: ['音乐'],
      versions: context, feature_rule_version: context.feature_rule_version, interpretation: 'shared_genre_association',
      is_probability: false, is_exhaustive_ranking_explanation: false } }
}
