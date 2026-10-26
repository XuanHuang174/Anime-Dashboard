import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { DEFAULT_BROWSE_FILTERS, browseConditions, sameConditions, filterChips, mapBrowsePage, requireBrowseTransition } from '../src/utils/recommendationBrowse.js'
import { browsePage } from './fixtures/browseContract.js'

test('low score selection sends a real server ceiling in browse and legacy; defaults stay unchanged', async () => {
  const calls = []
  const client = createRecommendationClient({ get: async (path, config) => { calls.push(config.params); return { data: {} } } })
  const selection = { category: 'all', style: 'balanced', filters: { ...DEFAULT_BROWSE_FILTERS, minScore: 'lt6' } }
  await client.browse(selection); await client.list(selection)
  assert.equal(calls[0].min_score, '0'); assert.equal(calls[0].max_score, '6.0')
  assert.equal(calls[1].max_score, '6.0')
  assert.equal(calls[0].max_score_exclusive, true); assert.equal(calls[1].max_score_exclusive, true)
  const conditions = browseConditions(selection)
  assert.equal(conditions.max_score, '6.0')
  assert.equal(conditions.max_score_exclusive, true)
  assert.equal(filterChips(selection.filters).find(c => c.id === 'minScore').label, 'Bangumi 评分 < 6.0')
  assert.equal(sameConditions(conditions, { ...conditions, max_score_exclusive: undefined }), false)
  assert.equal(sameConditions(conditions, { ...conditions, max_score: '10.0' }), false)
  assert.ok(!('max_score' in browseConditions({ ...selection, filters: DEFAULT_BROWSE_FILTERS })))
})

test('new optional condition is validated and cannot be mixed between pages', () => {
  const fixture = browsePage()
  const raw = { ...fixture, results: fixture.results.map(row => ({ ...row, score: 5.5 })) }
  const conditions = { ...raw.effective_conditions, max_score: '6.0', max_score_exclusive: true }
  assert.equal(mapBrowsePage({ ...raw, effective_conditions: conditions }).effective_conditions.max_score, '6.0')
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: { ...conditions, max_score: 'oops' } }))
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: { ...conditions, min_score: '8.0' } }))
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: conditions, results: raw.results.map(row => ({ ...row, score: 0 })) }))
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: conditions, results: raw.results.map(row => ({ ...row, score: 6 })) }))
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: { ...conditions, max_score_exclusive: 'true' } }))
  assert.throws(() => mapBrowsePage({ ...raw, effective_conditions: { ...raw.effective_conditions, max_score_exclusive: true } }))
  assert.equal(mapBrowsePage({ ...raw, effective_conditions: conditions, results: raw.results.map(row => ({ ...row, score: 5.9 })) }).items.length, raw.results.length)
  const current = mapBrowsePage(raw)
  assert.throws(() => requireBrowseTransition(current, { ...current, effective_conditions: conditions }, 'more', new Set()))
})

test('recommendation page reuses the shared Memphis back-to-top and hides it for modals', () => {
  const page = readFileSync(new URL('../src/pages/Recommendations.jsx', import.meta.url), 'utf8')
  const control = readFileSync(new URL('../src/components/BackToTop.jsx', import.meta.url), 'utf8')
  assert.match(page, /<BackToTop isModalOpen=\{Boolean\(panel\)\} theme="memphis"/)
  assert.match(control, /window.scrollY > 300/)
  assert.match(control, /tabIndex=\{isVisible \? 0 : -1\}/)
})

test('slow-loading copy and score selector are updated without enabling NSFW', () => {
  const loading = readFileSync(new URL('../src/components/recommendations/RecommendationLoading.jsx', import.meta.url), 'utf8')
  const panel = readFileSync(new URL('../src/components/recommendations/RecommendationBrowseControls.jsx', import.meta.url), 'utf8')
  assert.match(loading, /这次加载较慢，请稍候。预计还需 5s~15s。/)
  assert.match(panel, /<option value="lt6">6.0 以下（不含未知评分）<\/option>/)
  assert.match(panel, /来源不明的 0 值不归入低分作品/)
  assert.doesNotMatch(panel, /include_nsfw/)
})
