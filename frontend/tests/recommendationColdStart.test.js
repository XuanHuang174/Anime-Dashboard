import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { COLD_START_CODES, validateColdStartState, validateRecommendationProfile,
  validateRecommendationSnapshot, mapRecommendationPage, recommendationResultState } from '../src/utils/recommendations.js'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationColdStartGuide, RecommendationTasteNote } = await server.ssrLoadModule('/src/components/recommendations/RecommendationProfileGuide.jsx')
const { RecommendationEmptyState } = await server.ssrLoadModule('/src/components/recommendations/RecommendationStates.jsx')
const cold = { contract_version: 'recommendations.cold-start.v1', code: 'empty_journal', reasons: ['empty_journal'],
  is_cold_start: true, recommendation_basis: 'none', minimum_samples: 3, explicit_preference_count: 0,
  journal_record_count: 0, rated_record_count: 0, unrated_record_count: 0, high_score_record_count: 0,
  eligible_sample_count: 0, usable_sample_count: 0, pending_identity_count: 0, missing_genre_count: 0, unavailable_sample_count: 0 }
const preferences = { tags: ['日常', '治愈'] }
const profile = (state = cold) => ({ contract_version: 'recommendations.3a.v1', cold_start: state.is_cold_start,
  eligible_sample_count: state.eligible_sample_count, usable_sample_count: state.usable_sample_count,
  cold_start_state: state, tag_summary: [], identity_service_ready: true,
  sample_policy: { minimum_score: '8.5', minimum_samples: 3 },
  extraction: { explicit_preferences: { tags: state.explicit_preference_count ? preferences.tags : [] } } })
const render = (Component, props) => renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Component, props)))
const guide = props => render(RecommendationColdStartGuide, { profile: profile(), status: 'cold', preferencesReady: true,
  onPreferences() {}, onIdentity() {}, ...props })

test('4B contracts validate real counts and reject incompatible or contradictory state', () => {
  validateColdStartState(cold)
  assert.equal(validateRecommendationProfile(profile()).cold_start_state.code, 'empty_journal')
  for (const change of [{ code: 'network_error' }, { is_cold_start: false }, { usable_sample_count: -1 },
    { minimum_samples: 0 }, { rated_record_count: 1 }, { recommendation_basis: 'personalized' },
    { reasons: ['all_unrated'] }, { contract_version: 'fake' }]) {
    assert.throws(() => validateColdStartState({ ...cold, ...change }))
  }
  assert.throws(() => validateRecommendationProfile({ ...profile(), usable_sample_count: 2 }))
})

test('profile/preferences/list snapshot changes fail clearly instead of mixing identities or preferences', () => {
  const p = profile()
  validateRecommendationSnapshot(p, { tags: [] }, { cold_start_state: cold })
  assert.throws(() => validateRecommendationSnapshot(p, preferences, { cold_start_state: cold }))
  assert.throws(() => validateRecommendationSnapshot(p, { tags: [] }, {}))
  assert.throws(() => validateRecommendationSnapshot(p, { tags: [] }, { cold_start_state: { ...cold, unrated_record_count: 1 } }))
  validateRecommendationSnapshot({ ...p, cold_start_state: undefined }, {}, {}) // older backend, no invented classification
})

test('every blocking reason renders its own actionable copy without fake matching percentages', () => {
  const expected = { empty_journal: /手账暂时为空/, all_unrated: /都尚未评分/, no_qualifying_high_scores: /按真实感受评分/,
    insufficient_samples: /至少需要/, pending_identity: /无需重新评分/, missing_genres: /不会用目录标签覆盖/,
    unavailable_samples: /不会绕过安全规则/ }
  for (const code of COLD_START_CODES.filter(code => code !== 'ready')) {
    const state = { ...cold, code, reasons: [code], unrated_record_count: 2, pending_identity_count: 2,
      missing_genre_count: 2, unavailable_sample_count: 2, usable_sample_count: 1 }
    const html = guide({ profile: profile(state) })
    assert.match(html, expected[code]); assert.match(html, /选择 3–5 个喜欢的题材/)
    assert.match(html, /href="\/dashboard"/); assert.doesNotMatch(html, /匹配率|匹配百分比|\d+%/)
    if (code === 'pending_identity') assert.match(html, /核对旧作品/)
  }
})

test('explicit-preference fallback is labeled and identity/schema unavailability disables confirmation', () => {
  const state = { ...cold, code: 'pending_identity', reasons: ['pending_identity', 'missing_genres'],
    recommendation_basis: 'explicit_preferences', explicit_preference_count: 2, missing_genre_count: 1 }
  const html = guide({ profile: { ...profile(state), identity_service_ready: false }, status: 'ready' })
  assert.match(html, /根据你选择的题材推荐/); assert.match(html, /尚未形成完整/)
  assert.match(html, /另有 1 部/); assert.match(html, /disabled=""[^>]*>核对旧作品/)
})

test('loading/error/normal-personalized states never show a cold-start guide', () => {
  assert.equal(guide({ status: 'error' }), '')
  assert.equal(guide({ status: 'loading' }), '')
  assert.equal(guide({ profile: undefined }), '')
  assert.equal(guide({ profile: { ...profile(), cold_start: false } }), '')
  const old = guide({ profile: { ...profile(), cold_start_state: undefined } })
  assert.match(old, /尚未提供细分原因/); assert.doesNotMatch(old, /手账暂时为空/)
})

test('taste note separates analyzable samples, automatic topics, explicit preferences and honest failures', () => {
  const html = render(RecommendationTasteNote, { profile: { ...profile(), usable_sample_count: 2,
    tag_summary: [{ tag: '音乐' }] }, preferences, status: 'ready', onPreferences() {} })
  assert.match(html, /可分析样本<\/dt><dd>2 部/)
  assert.match(html, /自动识别题材<\/dt><dd>1 个/)
  assert.match(html, /手动偏好<\/dt><dd>2 个/)
  assert.match(html, /来自高分作品/); assert.match(html, /你的明确选择/)
  for (const status of ['loading', 'error']) {
    const output = render(RecommendationTasteNote, { status, onRetry() {} })
    assert.doesNotMatch(output, /0 部|0 个|可分析样本/)
    assert.match(output, status === 'error' ? /重试读取/ : /正在读取/)
  }
})

test('filtered empty remains empty even with insufficient samples plus preferences; transport error stays error', () => {
  const state = { ...cold, recommendation_basis: 'explicit_preferences', explicit_preference_count: 2 }
  const data = { contract_version: 'recommendations.3b.v1', mode: 'preference_based', cold_start_state: state,
    count: 0, page: 1, page_size: 12, state_token: 'a'.repeat(64), next_page: null, results: [] }
  assert.equal(recommendationResultState(mapRecommendationPage(data)), 'empty')
  assert.throws(() => mapRecommendationPage({ ...data, mode: 'personalized' }))
  const error = render(RecommendationEmptyState, { state: 'error', onRetry() {}, onReset() {} })
  const empty = render(RecommendationEmptyState, { state: 'empty', onRetry() {}, onReset() {} })
  assert.match(error, /暂时无法加载结果/); assert.match(empty, /试着放宽评分或作品类型/)
  assert.doesNotMatch(error + empty, /去手账评分|选择喜好|空手账/)
})
