import test from 'node:test'
import assert from 'node:assert/strict'
import { bangumiSubjectUrl, normalizeBangumiLink, safeExternalUrl, moegirlTitleUrl,
  journalIdentityMessage, canReviewJournalIdentity } from '../src/utils/journalSourceLinks.js'
import { mapRecordToAnimeItem } from '../src/utils/recordMapping.js'

test('Bangumi references use explicit IDs and normalize trusted subject links', () => {
  assert.equal(bangumiSubjectUrl(12345), 'https://bgm.tv/subject/12345')
  assert.equal(normalizeBangumiLink('http://bangumi.tv/subject/12345/?from=search#x'), 'https://bgm.tv/subject/12345')
  for (const value of ['https://bgm.tv.evil/subject/1', 'javascript:alert(1)', 'https://user@bgm.tv/subject/1', 'https://bgm.tv/subject/0']) assert.equal(normalizeBangumiLink(value), '')
  assert.equal(bangumiSubjectUrl('My title'), '')
})

test('external links reject executable URLs and encode generated encyclopedia titles', () => {
  assert.equal(safeExternalUrl('javascript:alert(1)'), '')
  assert.equal(safeExternalUrl('https://user:password@example.com/'), '')
  assert.equal(moegirlTitleUrl('《合成作品 / A》'), 'https://zh.moegirl.org.cn/%E5%90%88%E6%88%90%E4%BD%9C%E5%93%81%20%2F%20A')
  assert.equal(moegirlTitleUrl(''), '')
})

test('unlinked identity, missing catalog and restricted scope have distinct actionable messages', () => {
  assert.equal(canReviewJournalIdentity('unlinked'), true)
  assert.equal(canReviewJournalIdentity('stale_link'), true)
  assert.equal(canReviewJournalIdentity('not_in_catalog'), false)
  assert.equal(canReviewJournalIdentity('unsafe_or_unknown'), false)
  assert.match(journalIdentityMessage('not_in_catalog'), /标准库暂未收录/)
  assert.match(journalIdentityMessage('unsafe_or_unknown'), /内容范围/)
})

test('record mapping preserves independent user links and confirmed identity without guessing from title', () => {
  const item = mapRecordToAnimeItem({ id: 1, personal_score: null, anime: {
    chinese_title: 'User name', baike_url: 'https://zh.moegirl.org.cn/Custom', bangumi_url: 'https://bgm.tv/subject/123', confirmed_bangumi_id: 123,
  } })
  assert.equal(item.confirmedBangumiId, 123)
  assert.equal(item.bangumiUrl, 'https://bgm.tv/subject/123')
  assert.equal(item.baikeUrl, 'https://zh.moegirl.org.cn/Custom')
  assert.equal(mapRecordToAnimeItem({ anime: { chinese_title: '123' } }).confirmedBangumiId, null)
})
