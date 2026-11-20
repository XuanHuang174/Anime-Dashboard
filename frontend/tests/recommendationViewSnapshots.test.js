import test from 'node:test'
import assert from 'node:assert/strict'
import { createViewSnapshots } from '../src/utils/recommendationViewSnapshots.js'

test('returning to an earlier reference retains its controller, pages, filters and scroll without sharing scopes', () => {
  const views = createViewSnapshots()
  const first = views.entry('similar', '/recommendations/similar?seed=1', 'safe')
  Object.assign(first, { controller: {}, filters: { type: 'OVA,TV' }, scroll: 1560, page: 2 })
  views.entry('similar', '/recommendations/similar?seed=2', 'safe').scroll = 400
  const again = views.entry('similar', '/recommendations/similar?seed=1', 'safe')
  assert.equal(again, first); assert.equal(again.scroll, 1560); assert.equal(again.page, 2)
  assert.equal(again.filters.type, 'OVA,TV')
  assert.notEqual(views.entry('similar', '/recommendations/similar?seed=1', 'include_adult'), first)
  assert.deepEqual(createViewSnapshots().entry('similar', '/recommendations/similar?seed=1', 'safe'), {})
})

test('bounded least-recently-used snapshots dispose evicted controllers and stop advertising evicted query paths', () => {
  const views = createViewSnapshots(2), disposed = []
  views.entry('personal', '/recommendations', 'safe').controller = { dispose: () => disposed.push('personal') }
  const tags = views.entry('tags', '/recommendations/tags?include=日常', 'safe')
  views.entry('personal', '/recommendations', 'safe') // Return: keep personal, evict tags next.
  views.entry('similar', '/recommendations/similar?seed=2', 'safe')
  assert.equal(views.last('tags'), undefined); assert.deepEqual(disposed, [])
  assert.notEqual(views.entry('tags', '/recommendations/tags?include=日常', 'safe'), tags)
  assert.deepEqual(disposed, ['personal'])
})
