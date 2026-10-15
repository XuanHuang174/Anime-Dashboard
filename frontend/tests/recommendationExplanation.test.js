import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { recommendationEvidence, recommendationReason, recommendationAlgorithmLabel } from '../src/utils/recommendationExplanation.js'
import { mapRecommendationItem, mapRecommendationPage, REC_CATEGORIES } from '../src/utils/recommendations.js'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Evidence } = await server.ssrLoadModule('/src/components/recommendations/RecommendationEvidence.jsx')
const { default: Card } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCard.jsx')
const { default: Guide } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCategoryGuide.jsx')
const evidence = { kind: 'rated_work', source: { bangumi_id: 51, title: '来源作品', personal_score: '9.0' },
  shared_tags: ['音乐', '日常'], feature_rule_version: 'journal-genres.v3' }
const row = { content_rating: 'non_adult', bangumi_id: 52, title_zh: '推荐作品', title_jp: 'Original',
  score: 7.5, tags: ['音乐', '日常', '成长'], evidence, reason: '来自后端的真实证据文案。', season: '2026-04' }
const item = mapRecommendationItem(row)
const render = props => renderToStaticMarkup(React.createElement(Evidence, { item, ...props }))

test('rated-work evidence uses the actual source personal score, not the public score', () => {
  const html = render()
  assert.match(html, /来源作品/); assert.match(html, /你的个人评分/); assert.match(html, /9\.0 \/ 10/)
  assert.match(html, /实际共有题材/); assert.match(html, /音乐/); assert.match(html, /日常/)
  assert.doesNotMatch(html, /7\.5 \/ 10|成长|匹配率|匹配百分比/)
  assert.match(html, /公共评价/); assert.match(html, /不是喜欢概率/)
})

test('explicit evidence never pretends to be a rated-work profile or fabricates a source', () => {
  const html = render({ item: { ...item, evidence: { kind: 'explicit_preferences', source: null, shared_tags: ['音乐'] } } })
  assert.match(html, /来自明确偏好/); assert.match(html, /根据你选择的题材推荐/)
  assert.match(html, /而非高分作品画像/)
  assert.doesNotMatch(html, /<dt>来源作品|<dt>你的个人评分|9\.0/)
})

test('missing or invalid structured evidence never uses raw reasons, tags or synopsis to infer taste', () => {
  for (const bad of [null, undefined, {}, { ...evidence, kind: 'unknown' },
    { ...evidence, shared_tags: [] }, { ...evidence, shared_tags: ['悬疑'] },
    { ...evidence, shared_tags: ['音乐', '音乐'] }, { ...evidence, source: null },
    { ...evidence, source: { ...evidence.source, personal_score: '' } },
    { ...evidence, source: { ...evidence.source, personal_score: '9分' } },
    { ...evidence, source: { ...evidence.source, personal_score: 0 } },
    { ...evidence, source: { ...evidence.source, personal_score: 8.4 } },
    { ...evidence, source: { ...evidence.source, personal_score: 10.1 } },
    { ...evidence, source: { ...evidence.source, bangumi_id: '51' } },
    { kind: 'explicit_preferences', source: evidence.source, shared_tags: ['音乐'] }]) {
    const broken = { ...item, evidence: bad, reason: '不能展示的虚构理由', synopsis: '不能推断的简介' }
    assert.equal(recommendationEvidence(broken), null)
    assert.match(recommendationReason(broken), /依据暂不可用/)
    const html = render({ item: broken })
    assert.match(html, /当前没有可展示的完整证据/)
    assert.doesNotMatch(html, /不能展示的虚构理由|不能推断的简介|来源作品|你的个人评分/)
  }
})

test('native disclosure defaults closed on cards and opens in detail without any write handler', () => {
  assert.doesNotMatch(render(), /<details[^>]*\sopen(?:=|>)/)
  assert.match(render({ expanded: true }), /<details[^>]* open=""/)
  assert.match(render(), /<summary aria-label="为什么推荐《推荐作品》？"/)
  assert.doesNotMatch(render(), /<form|type="submit"|href=|onclick=/i)
})

test('hero and grid keep the existing detail/want loop and safe missing-cover fallback', () => {
  for (const hero of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(Card, { item, hero, onDetail() {}, onWant() {} }))
    assert.match(html, /为什么推荐？/); assert.match(html, /查看详情/); assert.match(html, /加入想看/)
    assert.match(html, /暂无托管封面/)
    assert.equal((html.match(/class="rec-card-reason"/g) || []).length, 1)
  }
})

test('untrusted source text is escaped and explanation does not mutate source data', () => {
  const before = JSON.stringify(item)
  render(); recommendationEvidence(item); recommendationReason(item)
  assert.equal(JSON.stringify(item), before)
  const html = render({ item: { ...item, evidence: { ...evidence,
    source: { ...evidence.source, title: '<img src=x onerror=alert(1)>' } } } })
  assert.doesNotMatch(html, /<img src=x/); assert.match(html, /&lt;img/)
})

test('all three categories have distinct modest descriptions and honest explicit fallback', () => {
  assert.equal(new Set(REC_CATEGORIES.map(category => category.description)).size, 3)
  for (const category of REC_CATEGORIES) {
    const html = renderToStaticMarkup(React.createElement(Guide, { category: category.id, mode: 'preference_based' }))
    assert.ok(html.includes(category.description)); assert.match(html, /当前不是基于完整高分画像的推荐/)
    assert.match(html, /aria-live="polite"/); assert.doesNotMatch(html, /百分比|更准确|保证|100%/)
  }
  const ready = renderToStaticMarkup(React.createElement(Guide, { category: 'all', mode: 'personalized' }))
  assert.doesNotMatch(ready, /当前不是基于完整高分画像/)
})

test('V1/V2 transport versions are supported but missing or unknown versions never claim V1', () => {
  const page = { contract_version: 'recommendations.3b.v1', mode: 'personalized', page: 1, page_size: 12,
    count: 1, next_page: null, state_token: 'a'.repeat(64), results: [row] }
  for (const [version, label] of [['content.v1', '推荐 V1'], ['content.v2', '推荐 V2']]) {
    const data = mapRecommendationPage({ ...page, algorithm_version: version })
    assert.equal(recommendationAlgorithmLabel(data.algorithm_version), label)
    assert.deepEqual(data.items[0].evidence, evidence)
  }
  assert.equal(recommendationAlgorithmLabel(undefined), '推荐')
  assert.equal(recommendationAlgorithmLabel('content.v99'), '推荐')
  assert.throws(() => mapRecommendationPage({ ...page, algorithm_version: 'content.v99' }))
  assert.equal(mapRecommendationPage(page).items.length, 1) // Rolling deployment compatibility.
})
