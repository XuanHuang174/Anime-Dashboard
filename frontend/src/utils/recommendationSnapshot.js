import { mapRecommendationPage, validateRecommendationProfile, validateRecommendationPreferences,
  validateRecommendationSnapshot } from './recommendations.js'
import { mapBrowsePage } from './recommendationBrowse.js'
import { sameSavedSampling } from './recommendationSampling.js'

export async function loadRecommendationSnapshot(client, { category, filters, style, browse = false, signal, confirmedPreferences, minimumFeedbackVersion }) {
  const [profile, preferences, raw] = await Promise.all([client.profile(signal), client.preferences(signal),
    browse ? client.browse({ category, style, filters }, signal) : client.list({ category, filters }, signal)])
  validateRecommendationProfile(profile)
  validateRecommendationPreferences(preferences)
  const data = browse ? mapBrowsePage(raw) : mapRecommendationPage(raw)
  validateRecommendationSnapshot(profile, preferences, data)
  if (minimumFeedbackVersion !== undefined && (!Number.isInteger(data.evidence_context?.feedback_version)
    || data.evidence_context.feedback_version < minimumFeedbackVersion)) {
    throw new Error('反馈已确认，但推荐版本尚未同步，请重新加载推荐。')
  }
  if (confirmedPreferences && (preferences.version < confirmedPreferences.version
    || (preferences.version === confirmedPreferences.version
      && (preferences.tags.length !== confirmedPreferences.tags.length
        || !preferences.tags.every(tag => confirmedPreferences.tags.includes(tag))
        || !sameSavedSampling(preferences, confirmedPreferences))))) {
    throw new Error('推荐画像合同不兼容，保存后的设置尚未同步，请重新加载。')
  }
  return { profile, preferences, data, items: data.items }
}
