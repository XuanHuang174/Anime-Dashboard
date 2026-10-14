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
