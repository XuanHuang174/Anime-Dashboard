import test from 'node:test'
import assert from 'node:assert/strict'
import { mapFeaturedSubmission } from '../src/utils/featuredDisplay.js'
import { normalizeBangumiLink } from '../src/utils/journalSourceLinks.js'

test('new featured snapshots expose their own Bangumi link; legacy snapshots stay unset', () => {
  const source = { id: 1, chinese_title: '合成快照', score: '9.0', bangumi_url: 'https://bgm.tv/subject/12345' }
  const item = mapFeaturedSubmission(source)
  assert.equal(item.bangumiUrl, source.bangumi_url)
  assert.equal(normalizeBangumiLink(item.bangumiUrl), source.bangumi_url)
  assert.equal(mapFeaturedSubmission({ id: 2, chinese_title: '旧快照' }).bangumiUrl, '')
  assert.equal(normalizeBangumiLink(mapFeaturedSubmission({ ...source, bangumi_url: 'javascript:bad' }).bangumiUrl), '')
})
