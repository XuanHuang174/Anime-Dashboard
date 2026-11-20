import test from 'node:test'
import assert from 'node:assert/strict'
import { createVisiblePoller } from '../src/utils/visiblePoller.js'

const settle = () => new Promise(setImmediate)
function fixture(poll) {
  const state = { time: 0, visible: true, calls: 0, errors: 0, timers: new Map(), serial: 0 }
  const poller = createVisiblePoller({
    poll: async () => { state.calls++; return poll?.(state) },
    onError: () => state.errors++, isVisible: () => state.visible, now: () => state.time,
    setTimer: (fn, delay) => { const id = ++state.serial; state.timers.set(id, { fn, delay }); return id },
    clearTimer: id => state.timers.delete(id),
  })
  return { state, poller }
}

test('hidden pages pause notifications and focus/visibility bursts coalesce', async () => {
  const { state, poller } = fixture()
  state.visible = false; poller.wake(); await settle()
  assert.equal(state.calls, 0); assert.equal(state.timers.size, 0)
  state.visible = true; poller.wake(); poller.wake(); await settle()
  assert.equal(state.calls, 1)
  poller.wake(); poller.wake(); await settle()
  assert.equal(state.calls, 1); assert.equal(state.timers.size, 1)
  state.visible = false; poller.wake()
  assert.equal(state.timers.size, 0)
  state.time = 31000; state.visible = true; poller.wake(); await settle()
  assert.equal(state.calls, 2)
  poller.stop(); assert.equal(state.timers.size, 0)
})

test('429 Retry-After survives foreground events; errors only notify once per failure streak', async () => {
  const { state, poller } = fixture(() => { throw { response: { status: 429, data: { retry_after: 180 } } } })
  poller.wake(); await settle()
  assert.equal(state.calls, 1); assert.equal([...state.timers.values()][0].delay, 180000)
  state.time = 30000; poller.wake(); await settle()
  assert.equal(state.calls, 1)
  state.time = 180001; poller.wake(); await settle()
  assert.equal(state.calls, 2); assert.equal(state.errors, 1)
  poller.stop()
})

test('temporary failures back off and successful response resets the interval', async () => {
  const { state, poller } = fixture(state => { if (state.calls < 3) throw Error('offline') })
  poller.wake(); await settle()
  assert.equal([...state.timers.values()][0].delay, 30000)
  state.time = 30001; poller.wake(); await settle()
  assert.equal([...state.timers.values()][0].delay, 60000)
  state.time = 90002; poller.wake(); await settle()
  assert.equal([...state.timers.values()][0].delay, 30000)
  poller.stop()
})

test('explicit network recovery wakes a transport failure but cannot bypass Retry-After', async () => {
  for (const limited of [false, true]) {
    const { state, poller } = fixture(() => {
      throw limited ? { response: { status: 429, data: { retry_after: 180 } } } : { code: 'ERR_NETWORK' }
    })
    poller.wake(); await settle()
    state.time = 1000
    poller.recover(); poller.recover(); await settle()
    assert.equal(state.calls, limited ? 1 : 2)
    poller.stop()
  }
})
