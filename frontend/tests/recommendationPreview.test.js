import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { RECOMMENDATION_PREVIEW_ITEMS, PREVIEW_EXISTING_RECORDS } from './fixtures/recommendationPreviewData.js'
import { addPreviewWant, DEFAULT_REC_FILTERS, filterRecommendationPreview, getRecommendationViewState } from './fixtures/recommendationPreviewLogic.js'
import { getRecommendationCover } from '../src/utils/recommendations.js'

test('recommendation covers accept only exact R2 host and fixed JPEG namespace', () => {
  const safe = 'https://media.xh-anime.com/covers/51.jpg'
  assert.equal(getRecommendationCover(safe), safe)
  assert.equal(getRecommendationCover(`${safe}?v=0123456789abcdef`), `${safe}?v=0123456789abcdef`)
  for (const value of [null, '', 'javascript:alert(1)', 'http://media.xh-anime.com/covers/1.jpg',
    'https://media.xh-anime.com.evil.test/covers/1.jpg', 'https://media.xh-anime.com@evil.test/covers/1.jpg',
    'https://user:password@media.xh-anime.com/covers/1.jpg', 'https://media.xh-anime.com:8443/covers/1.jpg',
    'https://media.xh-anime.com/other/1.jpg', `${safe}?token=secret`, `${safe}#unsafe`]) {
    assert.equal(getRecommendationCover(value), '', String(value))
  }
})

test('adult and unknown classifications fail closed in every category', () => {
  for (const category of ['all', 'familiar', 'explore']) {
    const results = filterRecommendationPreview(RECOMMENDATION_PREVIEW_ITEMS, category, DEFAULT_REC_FILTERS)
    assert.ok(results.every((item) => item.nsfw === false))
    assert.ok(!results.some((item) => item.id === 'demo-adult' || item.id === 'demo-unknown'))
  }
})

test('category, type, score and recorded filters compose without mutating fixtures', () => {
  const results = filterRecommendationPreview(RECOMMENDATION_PREVIEW_ITEMS, 'explore',
    { type: '剧场版', minScore: '8', hideRecorded: false })
  assert.deepEqual(results.map((item) => item.id), ['demo-238'])
  const unrecorded = filterRecommendationPreview(RECOMMENDATION_PREVIEW_ITEMS, 'all',
    { ...DEFAULT_REC_FILTERS, hideRecorded: true }, PREVIEW_EXISTING_RECORDS)
  assert.ok(unrecorded.every((item) => !PREVIEW_EXISTING_RECORDS[item.id]))
  assert.equal(RECOMMENDATION_PREVIEW_ITEMS.length, 12)
})

test('explicit cold/error/loading states never fall through to fabricated results', () => {
  for (const scenario of ['cold', 'error', 'loading', 'empty']) {
    assert.equal(getRecommendationViewState(scenario, 10), scenario)
  }
  assert.equal(getRecommendationViewState('retrying', 10), 'loading')
  assert.equal(getRecommendationViewState('ready', 0), 'empty')
  assert.equal(getRecommendationViewState('ready', 10), 'ready')
})

test('adding demo want is idempotent and preserves existing ratings and reviews', () => {
  const existing = RECOMMENDATION_PREVIEW_ITEMS.find((item) => item.id === 'demo-12')
  assert.equal(addPreviewWant(PREVIEW_EXISTING_RECORDS, existing), PREVIEW_EXISTING_RECORDS)
  assert.equal(PREVIEW_EXISTING_RECORDS['demo-12'].score, 9.1)
  assert.match(PREVIEW_EXISTING_RECORDS['demo-12'].review, /不能覆盖/)
  const fresh = RECOMMENDATION_PREVIEW_ITEMS[0]
  const next = addPreviewWant(PREVIEW_EXISTING_RECORDS, fresh)
  assert.equal(next[fresh.id].status, 'want')
  assert.equal(next[fresh.id].demoOnly, true)
  assert.equal(next['demo-12'], PREVIEW_EXISTING_RECORDS['demo-12'])
  assert.equal(addPreviewWant(next, fresh), next)
  for (const bad of [null, { id: 'server-123', nsfw: false }, { id: 'demo-bad', nsfw: true }]) {
    assert.equal(addPreviewWant(next, bad), next)
  }
})

test('real page has no mock fallback or acceptance controls and route remains protected', () => {
  const page = readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /recommendationPreview|REC_SCENARIOS|rec-qa-panel|localStorage|sessionStorage/)
  assert.match(page, /useRecommendations/)
  assert.match(page, /useRecommendationDetail\(client\)/)
  const detail = readFileSync(new URL('../src/utils/recommendationDetail.js', import.meta.url), 'utf8')
  assert.match(detail, /client.detail/)
  assert.doesNotMatch(detail, /recommendationPreview|localStorage|sessionStorage/)
  const router = readFileSync(new URL('../src/router/AppRouter.jsx', import.meta.url), 'utf8')
  assert.match(router, /path="\/recommendations" element=\{<ProtectedRoute><Suspense/)
  assert.match(router, /<Recommendations\s*\/><\/Suspense><\/ProtectedRoute>/)
  const banner = readFileSync(new URL('../src/components/JournalRecommendationBanner.jsx', import.meta.url), 'utf8')
  assert.match(banner, /to="\/recommendations"/)
  assert.doesNotMatch(banner, /界面预览/)
})

test('styles use the independent namespace and static non-flashing skeletons', () => {
  const css = readFileSync(new URL('../src/styles/recommendations.css', import.meta.url), 'utf8')
  assert.doesNotMatch(css, /@keyframes|animation:/)
  const selectors = css.split('\n').filter((line) => line.includes('{') && !line.trim().startsWith('@'))
  assert.ok(selectors.every((line) => line.trim().startsWith('.memphis-rec-container')))
  assert.match(css, /repeat\(4, minmax\(0, 1fr\)\)/)
  assert.match(css, /repeat\(3, minmax\(0, 1fr\)\)/)
  assert.match(css, /repeat\(2, minmax\(0, 1fr\)\)/)
})
