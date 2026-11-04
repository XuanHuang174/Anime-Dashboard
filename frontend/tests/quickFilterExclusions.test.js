import test from 'node:test'
import assert from 'node:assert/strict'
import { matchesCustomQuickFilter, quickFilterSummary } from '../src/utils/quickFilter.js'

test('exclusion-only filters keep other works and search both titles case-insensitively', () => {
  const filter = { tags: [], match_mode: 'any', exclude_tags: ['剧场版'], exclude_title_keywords: ['OVA'] }
  assert.equal(matchesCustomQuickFilter({ title: '日常', tags: [] }, filter), true)
  assert.equal(matchesCustomQuickFilter({ title: '日常', tags: ['剧场版'] }, filter), false)
  assert.equal(matchesCustomQuickFilter({ title: '日常', japaneseTitle: 'Everyday ova', tags: [] }, filter), false)
  assert.match(quickFilterSummary(filter), /排除标签：剧场版/)
})

test('exclusions veto both positive match modes while old filters retain their results', () => {
  for (const mode of ['any', 'all']) {
    const filter = { tags: ['日常'], match_mode: mode, exclude_tags: ['后宫'] }
    assert.equal(matchesCustomQuickFilter({ tags: ['日常', '后宫'] }, filter), false)
    assert.equal(matchesCustomQuickFilter({ tags: ['日常'] }, filter), true)
    assert.equal(matchesCustomQuickFilter({ tags: [] }, filter), false)
  }
  assert.equal(matchesCustomQuickFilter({ title: '番外', tags: [] }, { tags: ['日常'], title_keywords: ['番外'], match_mode: 'any' }), true)
})
