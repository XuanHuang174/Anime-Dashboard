import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { validateRecommendationPreferences } from '../src/utils/recommendations.js'
import { createPreferenceEditor } from '../src/utils/recommendationPreferences.js'

const oldTags = ['日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
  '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
  '青春', '魔法', '异世界', '偶像', '美食', '旅行']
const newTags = ['热血', '后宫', '玄幻', '穿越', '魔法少女', '恐怖', '武侠']
const available = [...oldTags, ...newTags]
const preferences = (tags = [], version = 0) => ({ contract_version: 'recommendations.3a.v1',
  taxonomy_version: 'genres.v4', tags, version, available_tags: available })

const server = await createServer({ configFile: false, plugins: [react()],
  server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Panel } = await server.ssrLoadModule('/src/components/recommendations/RecommendationPreferencesPanel.jsx')

test('existing JSX renders all 33 API-provided choices without auto-selecting new topics', () => {
  const prefs = validateRecommendationPreferences(preferences(['日常'], 3))
  const editor = { status: 'editing', draftTags: ['日常'], preferences: prefs }
  const html = renderToStaticMarkup(React.createElement(Panel, { editor,
    onTag() {}, onClear() {}, onSave() {}, onClose() {}, onReloadLatest() {}, onResolveConflict() {} }))
  assert.equal((html.match(/type="checkbox"/g) || []).length, 33)
  assert.equal((html.match(/checked=""/g) || []).length, 1)
  for (const tag of newTags) assert.ok(html.includes(tag))
  assert.deepEqual(prefs.tags, ['日常'])
})

test('new exact topics remain drafts until acknowledged save, with no parent inference', async () => {
  let writes = 0, acknowledgement
  const editor = createPreferenceEditor({ savePreferences: async changes => {
    writes++
    assert.deepEqual(changes, { tags: ['魔法少女'], expected_version: 0 })
    return preferences(['魔法少女'], 1)
  } }, data => { acknowledgement = data })
  const initial = preferences()
  editor.open(initial)
  editor.setTag('魔法少女', true)
  editor.setTag('修仙', true)
  assert.deepEqual(editor.getSnapshot().draftTags, ['魔法少女'])
  assert.deepEqual(initial.tags, [])
  assert.equal(writes, 0)
  await editor.save()
  assert.equal(writes, 1)
  assert.deepEqual(acknowledgement.tags, ['魔法少女'])
  assert.ok(!acknowledgement.tags.includes('魔法'))
})

test('expanded options do not increase the 20-topic selection limit', () => {
  const editor = createPreferenceEditor({})
  editor.open(preferences(available.slice(0, 20), 1))
  editor.setTag('热血', true)
  assert.equal(editor.getSnapshot().draftTags.length, 20)
  assert.match(editor.getSnapshot().error, /最多选择 20/)
  editor.close()
})

const secondBatch = ['萌系', '战争', '动作', '励志', '超能力', '童话', '犯罪', '赛博朋克']
const expandedTags = [...available, ...secondBatch]
const expandedPreferences = (tags = [], version = 0) => {
  const counts = Object.fromEntries(expandedTags.map(tag => [tag, ({ 喜剧: 100, 战争: 20, 萌系: 10 })[tag] || 0]))
  return { ...preferences(tags, version), taxonomy_version: 'genres.v6',
    available_tags: [...expandedTags].sort((a, b) => counts[b] - counts[a]),
    available_tag_counts: counts, option_order_status: 'ready', option_order_version: 'safe-catalog-topic-frequency.v1' }
}

test('41 live choices follow server public-coverage order without selecting or moving saved preferences', () => {
  const prefs = validateRecommendationPreferences(expandedPreferences(['百合'], 7))
  const original = JSON.stringify(prefs)
  const html = renderToStaticMarkup(React.createElement(Panel, { editor: {
    status: 'editing', draftTags: ['百合'], preferences: prefs }, onTag() {}, onClose() {} }))
  assert.equal((html.match(/type="checkbox"/g) || []).length, 41)
  assert.equal((html.match(/checked=""/g) || []).length, 1)
  assert.ok(html.indexOf('喜剧</label>') < html.indexOf('战争</label>'))
  assert.ok(html.indexOf('战争</label>') < html.indexOf('萌系</label>'))
  for (const tag of secondBatch) assert.ok(html.includes(`${tag}</label>`))
  assert.match(html, /按安全推荐库中题材覆盖的作品数排序/)
  assert.equal(JSON.stringify(prefs), original)
})

test('new topic choices remain explicit drafts and keep the same save/version/20-choice contract', async () => {
  let writes = 0
  const editor = createPreferenceEditor({ savePreferences: async changes => {
    writes++
    assert.deepEqual(changes, { tags: ['萌系'], expected_version: 0 })
    return expandedPreferences(['萌系'], 1)
  } })
  editor.open(expandedPreferences())
  editor.setTag('萌系', true)
  editor.setTag('萌豚', true)
  assert.equal(writes, 0)
  assert.deepEqual(editor.getSnapshot().draftTags, ['萌系'])
  await editor.save()
  assert.equal(writes, 1)
  editor.open(expandedPreferences(expandedTags.slice(0, 20), 1))
  editor.setTag('犯罪', true)
  assert.equal(editor.getSnapshot().draftTags.length, 20)
  assert.match(editor.getSnapshot().error, /最多选择 20/)
})

test('missing frequency metadata rolls back only option order, not preference editing or success state', () => {
  const prefs = { ...expandedPreferences(), available_tags: expandedTags, option_order_status: 'unavailable' }
  delete prefs.available_tag_counts
  const editor = createPreferenceEditor({})
  assert.equal(editor.open(prefs), true)
  editor.setTag('赛博朋克', true)
  const html = renderToStaticMarkup(React.createElement(Panel, { editor: editor.getSnapshot(), onTag() {}, onClose() {} }))
  assert.match(html, /题材数量暂不可用/)
  assert.ok(html.includes('保存喜好'))
  assert.deepEqual(editor.getSnapshot().draftTags, ['赛博朋克'])
})

const thirdBatch = ['竞技', '动物', '惊悚', '讽刺', '科普', '吸血鬼', '妖怪', '家庭', '末世', '公路片']
const thirdPreferences = (tags = [], version = 0) => {
  const all = [...expandedTags, ...thirdBatch]
  const counts = Object.fromEntries(all.map(tag => [tag, ({ 动物: 9, 竞技: 8, 日常: 7 })[tag] || 0]))
  return { ...expandedPreferences(tags, version), taxonomy_version: 'genres.v8',
    available_tags: all.sort((a, b) => counts[b] - counts[a]), available_tag_counts: counts }
}

test('51 API options retain frequency order, saved choices and no automatic parent selection', () => {
  const prefs = validateRecommendationPreferences(thirdPreferences(['公路片'], 2))
  const editor = createPreferenceEditor({})
  editor.open(prefs)
  const html = renderToStaticMarkup(React.createElement(Panel, { editor: editor.getSnapshot(), onTag() {}, onClose() {} }))
  assert.equal((html.match(/type="checkbox"/g) || []).length, 51)
  assert.equal((html.match(/checked=""/g) || []).length, 1)
  assert.ok(html.indexOf('动物</label>') < html.indexOf('竞技</label>'))
  for (const tag of thirdBatch) assert.ok(html.includes(`${tag}</label>`))
  editor.setTag('吸血鬼', true)
  editor.setTag('末日', true)
  assert.deepEqual(editor.getSnapshot().draftTags, ['公路片', '吸血鬼'])
  assert.deepEqual(prefs.tags, ['公路片'])
})

test('third-batch choices save only after acknowledgement and keep the 20-choice limit', async () => {
  let writes = 0
  const editor = createPreferenceEditor({ savePreferences: async changes => {
    writes++
    assert.deepEqual(changes, { tags: ['科普', '家庭'], expected_version: 0 })
    return thirdPreferences(['科普', '家庭'], 1)
  } })
  editor.open(thirdPreferences())
  editor.setTag('科普', true)
  editor.setTag('家庭', true)
  assert.equal(writes, 0)
  await editor.save()
  assert.equal(writes, 1)
  editor.open(thirdPreferences(expandedTags.slice(0, 20), 1))
  editor.setTag('动物', true)
  assert.equal(editor.getSnapshot().draftTags.length, 20)
  assert.match(editor.getSnapshot().error, /最多选择 20/)
})
