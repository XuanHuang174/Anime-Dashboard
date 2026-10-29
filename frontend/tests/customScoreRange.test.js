import test from 'node:test'
import assert from 'node:assert/strict'
import { scoreConditions, scoreError, clearScore } from '../src/utils/browseScore.js'
import { DEFAULT_BROWSE_FILTERS, browseConditions, sameConditions, mapBrowsePage } from '../src/utils/recommendationBrowse.js'
import { similarQuery } from '../src/utils/explorationResults.js'
import { tagQuery, tagQueryPath, parseTagQuery, tagDraftFromQuery } from '../src/utils/explorationTagQuery.js'
import { creditsFilterDraft, creditsFilterOptions } from '../src/utils/creditsBrowseFilters.js'
import { CREDIT_DEFAULTS } from '../src/utils/recommendationCredits.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { browsePage } from './fixtures/browseContract.js'

const filters = { ...DEFAULT_BROWSE_FILTERS, minScore: 'custom', scoreFrom: '6.3', scoreTo: '7.5' }
test('personal, legacy, similar, tags and credits carry the same inclusive decimal bounds', async () => {
  const score = { min_score: '6.3', max_score: '7.5', max_score_exclusive: false }
  assert.deepEqual(scoreConditions(filters), score)
  const calls = [], client = createRecommendationClient({ get: async (_, config) => { calls.push(config.params); return { data: {} } } })
  await client.browse({ category: 'all', style: 'balanced', filters }); await client.list({ category: 'all', filters })
  for (const row of calls) { assert.equal(row.min_score, '6.3'); assert.equal(row.max_score, '7.5'); assert.notEqual(row.max_score_exclusive, true) }
  const similar = similarQuery(1, filters)
  for (const key of Object.keys(score)) assert.equal(similar[key], score[key])
  const tags = tagQuery({ include: ['日常'], exclude: [], match: 'all', sort: 'relevance', filters }, ['日常'])
  for (const key of Object.keys(score)) assert.equal(tags[key], score[key])
  const credits = creditsFilterOptions({ ...creditsFilterDraft(CREDIT_DEFAULTS, true), ...filters })
  assert.deepEqual(Object.keys(credits).sort(), Object.keys(CREDIT_DEFAULTS).sort())
  assert.equal(credits.min_score, 6.3); assert.equal(credits.max_score, 7.5); assert.equal(credits.max_score_exclusive, false)
})

test('custom ranges round trip through public tag links, including a ceiling of ten and equal endpoints', () => {
  for (const [from, to] of [['6.3', '7.5'], ['8.5', '10.0'], ['9.0', '9.0'], ['0.1', '10.0']]) {
    const draft = { include: ['日常'], exclude: [], match: 'all', sort: 'relevance', filters: { ...filters, scoreFrom: from, scoreTo: to } }
    const query = tagQuery(draft, ['日常']), path = tagQueryPath(query)
    assert.deepEqual(parseTagQuery(path.slice(path.indexOf('?')), ['日常']).query, query)
    assert.deepEqual(tagQuery(tagDraftFromQuery(query), ['日常']), query)
    assert.equal(path.includes('includeAdult'), false)
  }
  assert.ok(parseTagQuery('?include=日常&minScore=7&scoreFrom=6.3', ['日常']).error)
  assert.ok(parseTagQuery('?include=日常&minScore=custom&scoreFrom=6.3&scoreTo=7.5&scoreTo=9', ['日常']).error)
})

test('invalid bounds cannot be submitted and removing a score chip clears both endpoints', () => {
  for (const [scoreFrom, scoreTo] of [['', '7'], ['0', '7'], ['7', ''], ['8', '7'], ['-1', '7'], ['1e0', '7'], ['6.33', '7.5'], ['NaN', '7'], ['1', '10.1']]) {
    assert.ok(scoreError({ ...filters, scoreFrom, scoreTo }))
    assert.throws(() => scoreConditions({ ...filters, scoreFrom, scoreTo }))
  }
  assert.deepEqual(clearScore(filters), DEFAULT_BROWSE_FILTERS)
  const current = browseConditions({ category: 'all', style: 'balanced', filters })
  assert.equal(sameConditions(current, { ...current, max_score: '7.6' }), false)
})

test('personal response rejects unknown and out-of-range scores while retaining inclusive boundaries', () => {
  const selection = { category: 'all', style: 'balanced', filters }
  const raw = browsePage(), effective_conditions = browseConditions(selection)
  const page = score => ({ ...raw, effective_conditions, results: raw.results.map(row => ({ ...row, score })) })
  for (const score of [6.3, 7.5]) assert.equal(mapBrowsePage(page(score)).items.length, raw.results.length)
  for (const score of [0, 6.2, 7.6]) assert.throws(() => mapBrowsePage(page(score)))
  const ceilingTen = { ...effective_conditions, min_score: '8.5' }; delete ceilingTen.max_score
  assert.throws(() => mapBrowsePage({ ...page(0), effective_conditions: ceilingTen }))
})

test('credits converts old fractional thresholds to editable ranges without leaking UI fields into cursor conditions', () => {
  const original = { ...CREDIT_DEFAULTS, min_score: 8.5 }
  const draft = creditsFilterDraft(original, true)
  assert.equal(draft.minScore, 'custom'); assert.equal(draft.scoreTo, '10.0')
  assert.deepEqual(creditsFilterOptions(draft), original)
  for (let value = 1; value <= 100; value++) {
    const score = (value / 10).toFixed(1)
    assert.doesNotThrow(() => creditsFilterOptions({ ...creditsFilterDraft(CREDIT_DEFAULTS, false), minScore: 'custom', scoreFrom: score, scoreTo: score }))
  }
})
