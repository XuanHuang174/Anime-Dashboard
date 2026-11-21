import test from 'node:test'
import assert from 'node:assert/strict'
import { cleanupCounts, cleanupProgress, cleanupResult, defaultCleanupTab, cleanupTagTone, cleanupScope } from '../src/utils/tagCleanupPresentation.js'

test('cleanup preview counts overlap and never infer change totals from visible page', () => {
  const job = { counts: { proposed: 13, unchanged: 6 }, total: 19, change_records: 13, protected_records: 18, needs_review: 1 }
  assert.deepEqual(cleanupCounts(job), { changes: 13, conflicts: 0, protected: 18, review: 1 })
  assert.equal(defaultCleanupTab({ ...job, status: 'ready' }), 'changes')
  assert.equal(defaultCleanupTab({ ...job, status: 'ready', change_records: 0 }), 'all')
  assert.equal(defaultCleanupTab({ ...job, status: 'applied', counts: { applied: 12, conflict: 1 } }), 'conflicts')
})

test('progress uses the actual stage cursor and selected denominator, including partial undo', () => {
  assert.deepEqual(cleanupProgress({ status: 'scanning', total: 120, scan_cursor: 50 }), { phase: 'scan', name: '扫描', done: 50, total: 120, percent: 42 })
  assert.deepEqual(cleanupProgress({ status: 'applying', total: 120, selected_count: 25, apply_cursor: 10 }), { phase: 'apply', name: '应用', done: 10, total: 25, percent: 40 })
  assert.deepEqual(cleanupProgress({ status: 'undoing', total: 120, selected_count: 10, undo_cursor: 5 }), { phase: 'undo', name: '撤销', done: 5, total: 10, percent: 50 })
  assert.equal(cleanupProgress({ status: 'ready', total: 0 }).percent, 0)
})

test('completed result separates untouched works and later edits from successful mutations', () => {
  assert.equal(cleanupResult({ status: 'applied', total: 19, counts: { applied: 12, conflict: 1, proposed: 2, unchanged: 4 } }), '已整理 12 部，跳过冲突 1 部，其余 6 部未修改。')
  assert.equal(cleanupResult({ status: 'undone', counts: { undone: 11, undo_conflict: 1 } }), '已撤销 11 部，1 部因后续变化而保留。')
})

test('tag chips distinguish protected names, aliases, additions and removals without mutating a plan', () => {
  const item = { before: ['2020', '治愈系', 'TV'], after: ['治愈', '音乐', 'TV'], protected: ['TV'], changes: [{ kind: 'normalize', from: '治愈系', to: '治愈' }] }
  const original = JSON.stringify(item)
  assert.equal(cleanupTagTone(item, '2020', 'before'), 'remove')
  assert.equal(cleanupTagTone(item, '治愈系', 'before'), 'normalize')
  assert.equal(cleanupTagTone(item, '治愈', 'after'), 'normalize')
  assert.equal(cleanupTagTone(item, '音乐', 'after'), 'add')
  assert.equal(cleanupTagTone(item, 'TV', 'after'), 'protected')
  assert.equal(JSON.stringify(item), original)
})

test('historical scope always comes from the saved job, with user-facing filter names', () => {
  assert.equal(cleanupScope({ scope: 'filtered', total: 19, filters: { search: '合成', status: 'watching', type_filter: 'moe', quick_filter: 123 } }), '扫描时的筛选作品 · 19 部 · 搜索「合成」 / 在看 / 萌系 / 已保存的快捷筛选')
  assert.equal(cleanupScope({ scope: 'all', total: 99, filters: { search: '不应显示' } }), '全部私人手账 · 99 部')
})
