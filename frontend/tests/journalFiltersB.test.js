import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeTypeSelection, isTypeSelection, matchesTypeSelection } from '../src/utils/animeTypeSelection.js'
import { matchesCustomQuickFilter, quickFilterSummary } from '../src/utils/quickFilter.js'
import { similarQuery, validateExplorationQuery } from '../src/utils/explorationResults.js'
import { tagQuery, emptyTagDraft } from '../src/utils/explorationTagQuery.js'
import { browseConditions } from '../src/utils/recommendationBrowse.js'
import { creditsFilterDraft, creditsFilterOptions } from '../src/utils/creditsBrowseFilters.js'
import { CREDIT_DEFAULTS } from '../src/utils/recommendationCredits.js'

test('multi-type selection stays canonical across personal, similar, tags and credits', () => {
  const type = normalizeTypeSelection('TV,OVA')
  assert.equal(type, 'OVA,TV')
  assert.equal(isTypeSelection(type), true)
  assert.equal(matchesTypeSelection('TV', type), true)
  assert.equal(matchesTypeSelection('剧场版', type), false)
  const filters = { type, minScore: '0', hideRecorded: true, yearFrom: '', yearTo: '' }
  assert.equal(browseConditions({ category: 'all', style: 'balanced', filters }).type, type)
  const similar = similarQuery(123, filters)
  assert.doesNotThrow(() => validateExplorationQuery(similar))
  assert.equal(similar.type, type)
  const draft = emptyTagDraft(); draft.include = ['日常']; draft.filters = filters
  assert.equal(tagQuery(draft, ['日常']).type, type)
  assert.equal(creditsFilterOptions(creditsFilterDraft({ ...CREDIT_DEFAULTS, type }, false)).type, type)
  for (const invalid of ['TV,', 'all,TV', 'TV,TV', 'TV,unknown']) assert.throws(() => normalizeTypeSelection(invalid))
})

test('year range composes with any/all tags and exclusions without accepting unknown dates', () => {
  const filter = { tags: ['日常', '治愈'], title_keywords: [], exclude_tags: ['剧场版'], match_mode: 'any', year_from: 2020, year_to: 2021 }
  assert.equal(matchesCustomQuickFilter({ title: '作品', period: '2020年1月', tags: ['日常'] }, filter), true)
  for (const period of ['', '2019-10', '2022-01', '2020-13']) assert.equal(matchesCustomQuickFilter({ period, tags: ['日常'] }, filter), false)
  assert.equal(matchesCustomQuickFilter({ period: '2020', tags: ['日常', '剧场版'] }, filter), false)
  assert.equal(matchesCustomQuickFilter({ period: '2021-01', tags: [] }, { ...filter, tags: [] }), true)
  assert.match(quickFilterSummary(filter), /2020—2021/)
  assert.equal(matchesCustomQuickFilter({ period: '', tags: ['日常'] }, { ...filter, year_from: null, year_to: null }), true)
})
