import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { RECOMMENDATION_COPY as COPY } from '../src/utils/recommendationCopy.js'
import { publicScorePresentation, personalScoreValue, personalScoreText } from '../src/utils/recommendationScores.js'
import { recommendationEvidence, recommendationReason } from '../src/utils/recommendationExplanation.js'
import { mapRecommendationItem } from '../src/utils/recommendations.js'
import { mapRecordToAnimeItem } from '../src/utils/recordMapping.js'

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { RecommendationPublicScore: PublicScore, RecommendationPersonalScore: PersonalScore } =
  await server.ssrLoadModule('/src/components/recommendations/RecommendationScore.jsx')
const { default: Evidence } = await server.ssrLoadModule('/src/components/recommendations/RecommendationEvidence.jsx')
const { default: Card, RecommendationMeta } = await server.ssrLoadModule('/src/components/recommendations/RecommendationCard.jsx')
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props))
const versions = { algorithm_version: 'content.v1', ranking_policy_version: 'frozen-v1',
  taxonomy_version: 'genres.v4', feature_version: 'genres-idf.v4', feature_rule_version: 'journal-genres.v3',
  selection_rule_version: 'association-evidence.v1', profile_version: 'c'.repeat(64),
  preference_version: 2, catalog_feature_version: 'd'.repeat(64) }
const evidence = { contract_version: 'recommendations.evidence.v1', kind: 'rated_work',
  source: { bangumi_id: 51, title: '来源作品', personal_score: '9.0' }, shared_tags: ['音乐'],
  feature_rule_version: versions.feature_rule_version, versions, interpretation: 'shared_genre_association',
  is_probability: false, is_exhaustive_ranking_explanation: false }
const row = { content_rating: 'non_adult', bangumi_id: 52, title_zh: '推荐作品', title_jp: 'Original',
  score: 7.5, tags: ['音乐', '成长'], evidence, reason: '因为你给《来源作品》打了 9.0 分，这部作品同样包含“音乐”标签。',
  season: '2026-04', status_type: 'TV', evidence_token: 'a'.repeat(64), evidence_context: versions }
const item = mapRecommendationItem(row)

test('public zero is ambiguous after import normalization, not a claimed unrated or actual low score', () => {
  assert.equal(publicScorePresentation(0).state, 'zero_ambiguous')
  const html = render(PublicScore, { score: 0 })
  assert.match(html, /Bangumi 评分/); assert.match(html, /状态待核实/)
  assert.match(html, /缺失、异常数据的归一化/)
  assert.doesNotMatch(html, /<b>0\.0|>未评分<|>暂无评分</)
  assert.equal(mapRecommendationItem({ ...row, score: 0 }).score, 0)
})

test('null and invalid public display values are unavailable without zero coercion or invented corrections', () => {
  for (const score of [null, undefined, '', '0', '暂无评分', NaN, Infinity, -1, 11, false]) {
    assert.equal(publicScorePresentation(score).state, 'unavailable')
    const html = render(PublicScore, { score })
    assert.match(html, /暂无数据/)
    assert.doesNotMatch(html, /<b>0|NaN|Infinity|可信度修正/)
  }
  assert.equal(mapRecommendationItem({ ...row, score: null }).score, null)
  // Malformed transport values still fail closed; only explicit null is additive.
  for (const score of ['', '0', undefined, false, NaN, Infinity, -1, 11]) {
    assert.throws(() => mapRecommendationItem({ ...row, score }), /合同不兼容/)
  }
})

test('personal 0 and string 0 are preserved; only actual missing/invalid values are unavailable', () => {
  for (const score of [0, '0', '0.0']) {
    assert.equal(personalScoreValue(score), 0)
    assert.equal(personalScoreText(score), '0.0 分')
    assert.match(render(PersonalScore, { title: '手账作品', score }), /你对《手账作品》的评分：<strong>0\.0 分/)
    const record = { id: 1, personal_score: score, review: '保留原长评', custom_tags: ['原标签'] }
    const before = JSON.stringify(record)
    const mapped = mapRecordToAnimeItem(record)
    assert.equal(mapped.score, 0)
    assert.equal(mapped.review, record.review)
    assert.deepEqual(mapped.customTags, record.custom_tags)
    assert.equal(JSON.stringify(record), before)
  }
  for (const value of [null, undefined, '', ' ', false, '9分', -1, 11, NaN, Infinity]) {
    assert.equal(personalScoreValue(value), null)
    assert.equal(personalScoreText(value), COPY.personalScoreMissing)
  }
  assert.equal(personalScoreText('10.0'), '10.0 分')
  const zeroSource = { ...item, evidence: { ...evidence, source: { ...evidence.source, personal_score: 0 } } }
  assert.equal(recommendationEvidence(zeroSource), null) // Real 0 does not meet >=8.5 source policy.
})

test('actual hero/grid/detail metadata uses public label and never substitutes source personal score', () => {
  for (const score of [7.5, 10, 0, null]) {
    const current = { ...item, score }
    for (const hero of [false, true]) {
      const html = render(Card, { item: current, hero, onDetail() {}, onWant() {} })
      assert.match(html, /<strong>Bangumi 评分<\/strong>/)
      assert.match(html, /你对《来源作品》的评分：<strong>9\.0 分/)
      assert.match(html, /为什么推荐？/); assert.match(html, /加入想看/)
    }
    const detailMeta = render(RecommendationMeta, { item: current })
    assert.match(detailMeta, /Bangumi 评分/)
    assert.doesNotMatch(detailMeta, /9\.0 分/)
  }
  assert.match(render(PublicScore, { score: 7.5 }), /7\.5 \/ 10/)
})

test('explicit preferences use exact semantic label without any made-up personal source', () => {
  const explicit = { ...item, reason: '根据你明确选择的音乐题材推荐。',
    evidence: { ...evidence, kind: 'explicit_preferences', source: null } }
  const html = render(Evidence, { item: explicit, expanded: true })
  assert.match(html, /来自你选择的题材/)
  assert.doesNotMatch(html, /你对《|9\.0 分|<dt>来源作品/)
})

test('missing, partial or inconsistent evidence fails neutral and exposes recovery instead of a guessed reason', () => {
  const cases = [undefined, { ...item, evidence: null }, { ...item, reason: '' },
    { ...item, evidenceToken: undefined }, { ...item, evidenceContext: undefined },
    { ...item, evidenceContext: { ...versions, preference_version: 3 } },
    { ...item, evidence: { ...evidence, contract_version: 'unknown' } },
    { ...item, evidence: { ...evidence, is_probability: true } },
    { ...item, evidence: { ...evidence, is_exhaustive_ranking_explanation: true } },
    { ...item, evidence: { ...evidence, feature_rule_version: 'different' } },
    { ...item, evidence: { ...evidence, shared_tags: ['科幻'] } },
    { ...item, tags: null }, { ...item, tags: '音乐' }]
  for (const bad of cases) {
    assert.equal(recommendationEvidence(bad), null)
    assert.equal(recommendationReason(bad), COPY.evidenceUnavailable)
    if (!bad) continue
    const html = render(Evidence, { item: bad, onReload() {} })
    assert.match(html, /当前没有可展示的完整证据/)
    assert.match(html, /<button type="button"[^>]*>重新加载推荐<\/button>/)
    assert.doesNotMatch(html, /因为你给|你对《|来自高分作品|9\.0 分/)
  }
  assert.match(render(Evidence, { item: { ...item, evidence: null } }), /href="\/recommendations"/)
})

test('recovery callback is explicit, read-only and does not fire during render', () => {
  let calls = 0
  const onReload = () => { calls += 1 }
  const tree = Evidence({ item: { ...item, evidence: null }, onReload })
  function find(node) {
    if (!node || typeof node !== 'object') return null
    if (node.type === 'button') return node
    return React.Children.toArray(node.props?.children).map(find).find(Boolean)
  }
  const button = find(tree)
  assert.equal(calls, 0)
  assert.equal(button.props.type, 'button')
  button.props.onClick()
  assert.equal(calls, 1)
})

test('internal scores/probabilities are never mapped or displayed and source inputs remain unchanged', () => {
  const raw = { ...row, rank_score: 987.654, match_rate: '99.9%', probability: 0.999, adjusted_score: 9.876 }
  const original = JSON.stringify(raw)
  const mapped = mapRecommendationItem(raw)
  for (const key of ['rank_score', 'match_rate', 'probability', 'adjusted_score']) assert.equal(mapped[key], undefined)
  const html = render(Card, { item: mapped, onDetail() {}, onWant() {} })
  assert.doesNotMatch(html, /987\.654|99\.9%|0\.999|9\.876|可信度修正/)
  assert.match(html, /不代表唯一排序原因/)
  assert.equal(JSON.stringify(raw), original)
  assert.ok(Object.isFrozen(COPY))
})
