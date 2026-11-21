import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { matchesCustomQuickFilter, quickFilterSummary, emptyQuickFilter, quickFilterPayload, quickFilterDraftError } from '../src/utils/quickFilter.js'

test('frontend condition groups follow the shared full-collection contract', () => {
  const contract = JSON.parse(readFileSync(new URL('./fixtures/quickConditionsContract.json', import.meta.url)))
  for (const { filter, ids } of contract.cases) {
    assert.deepEqual(contract.records.filter(row => matchesCustomQuickFilter(row, filter)).map(row => row.id), ids, JSON.stringify(filter))
  }
})

test('condition-only forms accept zero, single score bounds and states without requiring tags', () => {
  for (const values of [{ score_min: '0', score_max: '0', score_state: 'rated' }, { score_max: '9.9' }, { score_state: 'unrated' }, { watch_statuses: ['completed'] }, { review_state: 'without' }]) {
    const form = { ...emptyQuickFilter(), name: '合成组合', ...values }
    assert.equal(quickFilterDraftError(form), '')
    const payload = quickFilterPayload(form)
    assert.equal(payload.year_from, null)
    assert.equal(payload.tags.length, 0)
    assert.ok(quickFilterSummary(payload).length)
  }
  assert.equal(quickFilterPayload({ ...emptyQuickFilter(), score_min: '0' }).score_min, 0)
})

test('invalid range and contradictory drafts cannot be saved or previewed', () => {
  for (const values of [{}, { score_min: '9', score_max: '8' }, { score_min: '-1' }, { score_max: '10.1' }, { score_min: '9.99' }, { score_min: 'NaN' }, { score_min: '0', score_state: 'unrated' }, { year_from: 2022, year_to: 2020 }, { tags: ['日常'], exclude_tags: ['日常'] }, { title_keywords: 'OVA', exclude_title_keywords: 'ova' }]) {
    const form = { ...emptyQuickFilter(), name: '合成', ...values }
    assert.ok(quickFilterDraftError(form), JSON.stringify(values))
  }
  const unnamed = { ...emptyQuickFilter(), review_state: 'with' }
  assert.ok(quickFilterDraftError(unnamed)); assert.equal(quickFilterDraftError(unnamed, false), '')
})

test('summaries expose every effective condition and old filters remain unrestricted', () => {
  const summary = quickFilterSummary({ tags: ['日常'], score_min: 0, score_max: 9.9, score_state: 'rated', watch_statuses: ['completed','watching'], review_state: 'without', year_from: 2020, match_mode: 'all' })
  for (const text of ['全部满足', '0—9.9', '看过、在看', '无长评', '2020']) assert.ok(summary.includes(text), text)
  assert.equal(matchesCustomQuickFilter({ score: null, status: 'plan', review: '', tags: ['日常'] }, { tags: ['日常'], match_mode: 'any' }), true)
  assert.equal(matchesCustomQuickFilter({ score: false }, { score_state: 'rated' }), false)
  assert.equal(matchesCustomQuickFilter({ period: '2020-全年', filterPeriod: '2020' }, { year_from: 2020 }), true)
})
