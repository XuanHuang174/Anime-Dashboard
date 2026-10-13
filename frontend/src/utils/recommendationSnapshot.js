import { mapRecommendationPage, validateRecommendationProfile, validateRecommendationPreferences,
  validateRecommendationSnapshot } from './recommendations.js'

export async function loadRecommendationSnapshot(client, { category, filters, signal, confirmedPreferences }) {
  const [profile, preferences, raw] = await Promise.all([client.profile(signal), client.preferences(signal),
    client.list({ category, filters }, signal)])
  validateRecommendationProfile(profile)
  validateRecommendationPreferences(preferences)
  const data = mapRecommendationPage(raw)
  validateRecommendationSnapshot(profile, preferences, data)
  if (confirmedPreferences && (preferences.version < confirmedPreferences.version
    || (preferences.version === confirmedPreferences.version
      && (preferences.tags.length !== confirmedPreferences.tags.length
        || !preferences.tags.every(tag => confirmedPreferences.tags.includes(tag)))))) {
    throw new Error('推荐画像合同不兼容，保存后的设置尚未同步，请重新加载。')
  }
  return { profile, preferences, data, items: data.items }
}
