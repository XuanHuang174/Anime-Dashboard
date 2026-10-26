import test from 'node:test'
import assert from 'node:assert/strict'
import { createSeriesReminderController, PRESENTATION_VERSION } from '../src/utils/seriesReminders.js'
import { reminderRecordChanged, connectSeriesReminderUpdates } from '../src/utils/seriesReminderUpdates.js'
import { reminders } from './fixtures/seriesRemindersContract.js'
import { scope } from './fixtures/explorationContract.js'

function fixture(collapsed = true) {
  let version = 0, revision = 1, clock = Date.now(), reads = 0, preferences = 0, writes = 0
  const raw = () => ({ contract_version: PRESENTATION_VERSION, version, collapsed, series_revision: revision,
    dismissal_revision: 'a'.repeat(64), content_scope_context: scope })
  const client = {
    reminderPresentation: async () => { preferences++; return raw() },
    saveReminderPresentation: async body => { writes++; assert.equal(body.expected_version, version); version++; collapsed = body.collapsed; return raw() },
    seriesReminders: async () => { reads++; return { ...reminders({ collapsed, version }), expires_at: Math.floor(clock / 1000) + 900 } },
  }
  const controller = createSeriesReminderController(client, () => true, () => clock)
  controller.setVisible(true)
  return { client, controller, counts: () => ({ reads, preferences, writes }), advance: ms => { clock += ms }, revise: () => revision++,
    remoteCollapse: value => { collapsed = value; version++ } }
}

test('first visit is collapsed and lazy; toggles save presentation without recalculating a fresh list', async () => {
  const env = fixture(), c = env.controller
  await c.initialize()
  assert.equal(c.getState().collapsed, true)
  assert.equal(c.getState().data, null)
  assert.deepEqual(env.counts(), { reads: 0, preferences: 1, writes: 0 })
  await c.setCollapsed(false)
  assert.equal(c.getState().data.count, 2)
  await c.setCollapsed(true); await c.setCollapsed(false)
  assert.deepEqual(env.counts(), { reads: 1, preferences: 1, writes: 3 })
  c.setVisible(false); env.advance(40000); await c.foreground()
  c.setVisible(true); await c.initialize()
  assert.equal(env.counts().reads, 1)
})

test('existing expanded preference is respected; foreground and periodic probes only fetch tiny metadata', async () => {
  const env = fixture(false), c = env.controller
  await c.initialize(); assert.equal(env.counts().reads, 1)
  await c.foreground(); await c.foreground(); assert.equal(env.counts().preferences, 1)
  env.advance(31000); await c.foreground()
  assert.equal(env.counts().preferences, 2); assert.equal(env.counts().reads, 1)
  env.revise(); env.advance(31000); await c.foreground()
  assert.equal(env.counts().reads, 2)
})

test('cross-tab presentation changes update folding and write version without recomputing unchanged content', async () => {
  const env = fixture(false), c = env.controller
  await c.initialize()
  env.remoteCollapse(true); await c.checkPresentation(true)
  assert.equal(c.getState().collapsed, true); assert.equal(c.getState().dirty, false)
  env.remoteCollapse(false); await c.checkPresentation(true); await c.maybeRefresh()
  assert.equal(env.counts().reads, 1); assert.equal(c.getState().data.version, 2)
})

test('dirty collapsed panel waits for expansion; 15-minute expiry removes stale data without a hidden request', async () => {
  const env = fixture(), c = env.controller
  await c.initialize(); await c.setCollapsed(false); await c.setCollapsed(true)
  c.invalidate(); await c.maybeRefresh(); assert.equal(env.counts().reads, 1)
  await c.setCollapsed(false); assert.equal(env.counts().reads, 2)
  await c.setCollapsed(true); env.advance(901000); c.expire(); await c.maybeRefresh()
  assert.equal(c.getState().data, null); assert.equal(env.counts().reads, 2)
  await c.setCollapsed(false); assert.equal(env.counts().reads, 3)
})

test('a series revision change acknowledged during a fold save invalidates retained content', async () => {
  const env = fixture(false), c = env.controller
  await c.initialize()
  env.revise(); await c.setCollapsed(true)
  assert.equal(c.getState().dirty, true)
  assert.equal(env.counts().reads, 1)
  await c.setCollapsed(false)
  assert.equal(env.counts().reads, 2)
  assert.equal(c.getState().dirty, false)
})

test('simultaneous reloads coalesce and a change during an in-flight read cannot publish its stale result', async () => {
  const env = fixture(false), c = env.controller
  let finish, reads = 0
  env.client.seriesReminders = () => { reads++; return new Promise(resolve => { finish = resolve }) }
  const initial = c.initialize(); await Promise.resolve(); await Promise.resolve(); await new Promise(resolve => setImmediate(resolve))
  const other = c.maybeRefresh()
  assert.equal(reads, 1)
  c.invalidate(); finish(reminders())
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(c.getState().data, null)
  assert.equal(reads, 2)
  finish(reminders()); await Promise.all([initial, other])
  assert.equal(c.getState().dirty, false)
})

test('background refresh preserves existing display on failure and presentation uncertainty never repeats a write', async () => {
  const env = fixture(false), c = env.controller
  await c.initialize(); const old = c.getState().data
  env.client.seriesReminders = async () => { throw Error('offline') }
  await c.refresh(); assert.equal(c.getState().data, old); assert.match(c.getState().message, /暂时/)
  let writes = 0
  env.client.saveReminderPresentation = async () => { writes++; throw Error('offline') }
  await c.setCollapsed(true)
  assert.equal(c.getState().collapsed, true)
  assert.match(c.getState().preferenceMessage, /尚未确认/)
  await c.checkPresentation(true); assert.equal(writes, 1)
})

test('account disposal rejects delayed metadata and content; explicit scope revocation clears retained rows', async () => {
  const env = fixture(false), c = env.controller
  await c.initialize()
  env.client.reminderPresentation = async () => { throw { response: { status: 403 } } }
  await c.checkPresentation(true); assert.equal(c.getState().data, null)
  let finish
  env.client.reminderPresentation = () => new Promise(resolve => { finish = resolve })
  const pending = c.checkPresentation(true); c.dispose()
  finish({ contract_version: PRESENTATION_VERSION, version: 0, collapsed: false, series_revision: 1, dismissal_revision: 'a'.repeat(64), content_scope_context: scope })
  await pending; assert.equal(c.getState().presentation, null)
})

test('rating/review/private tags/progress edits do not invalidate reminder rules; watch status and identity changes do', () => {
  const before = { watch_status: 'watching', anime: { id: 1, bgm_id: 2, chinese_title: '合成作品' }, personal_score: 8, review: 'a', custom_tags: [] }
  assert.equal(reminderRecordChanged(before, { ...before, personal_score: 10, review: 'b', custom_tags: ['题材'], watched_eps: 3 }), false)
  assert.equal(reminderRecordChanged(before, { ...before, watch_status: 'completed' }), true)
  assert.equal(reminderRecordChanged(before, { ...before, anime: { ...before.anime, bgm_id: 3 } }), true)
  assert.equal(reminderRecordChanged(undefined, before), true)
})

test('event bursts debounce once, score-only changes skip refresh and disposing removes timers/listeners', async () => {
  const target = new EventTarget(), document = new EventTarget(), timers = new Map()
  let serial = 0, invalidations = 0, refreshes = 0
  document.visibilityState = 'visible'
  target.setTimeout = (fn, ms) => { timers.set(++serial, { fn, ms }); return serial }
  target.clearTimeout = id => timers.delete(id)
  const controller = { invalidate() { invalidations++ }, maybeRefresh() { refreshes++ }, foreground() {},
    checkPresentation: async () => {}, deadline: () => Infinity, subscribe: () => () => {} }
  const disconnect = connectSeriesReminderUpdates(controller, target, document)
  const event = new Event('recommendation-data-changed'); Object.defineProperty(event, 'reminders', { value: false }); target.dispatchEvent(event)
  assert.equal(invalidations, 0)
  target.dispatchEvent(new Event('recommendation-data-changed')); target.dispatchEvent(new Event('recommendation-data-changed'))
  assert.equal([...timers.values()].filter(row => row.ms === 250).length, 1)
  for (const row of [...timers.values()].filter(row => row.ms === 250)) row.fn()
  assert.equal(refreshes, 1)
  disconnect(); assert.equal(timers.size, 0)
  target.dispatchEvent(new Event('recommendation-data-changed')); assert.equal(invalidations, 2)
})
