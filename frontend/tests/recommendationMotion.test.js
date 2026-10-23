import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecommendationMotion, createRecommendationEntranceState } from '../src/utils/recommendationMotion.js'

function fixture({ reduced = false, entrance = createRecommendationEntranceState() } = {}) {
  const calls = [], contexts = [], timelines = [], listeners = new Map(), mediaListeners = new Set()
  const node = id => ({ id, isConnected: true })
  const head = node('head'), hero = node('hero'), note = node('note'), more = node('more')
  const root = { isConnected: true, more,
    contains: target => [head, hero, note, root.more, root.body, root.details].includes(target),
    querySelectorAll: selector => selector === '.rec-masthead' ? [head] : [hero, note],
    querySelector: () => root.more,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) } }
  const media = { matches: reduced, addEventListener: (_, fn) => mediaListeners.add(fn), removeEventListener: (_, fn) => mediaListeners.delete(fn) }
  const gsap = {
    context(fn, scope) {
      const context = { scope, reverts: 0, revert() { this.reverts++ } }
      contexts.push(context); fn(); return context
    },
    set() {},
    fromTo(targets, from, to) { calls.push({ targets, from, to }) },
    timeline(options) {
      const timeline = { options, steps: [], plays: 0,
        fromTo(targets, from, to, position) { this.steps.push({ targets, from, to, position }); return this },
        play() { this.plays++; return this } }
      timelines.push(timeline); return timeline
    },
  }
  const motion = createRecommendationMotion({ root, gsap, media, entrance })
  return { root, head, hero, note, more, entrance, calls, contexts, timelines, listeners, mediaListeners, motion,
    finishShell() { calls[0].to.onComplete() },
    startResults() { for (const step of timelines.at(-1).steps) step.to.onStart() },
    finishResults() { this.startResults(); timelines.at(-1).options.onComplete() },
    reduced(value) { media.matches = value; for (const fn of mediaListeners) fn() } }
}

test('menu enters before featured/taste together, then the entire more section', () => {
  const f = fixture(); f.motion.enterPage(); f.motion.updateResults('first', true)
  assert.equal(f.calls.length, 1)
  assert.deepEqual(f.calls[0].targets, [f.head])
  const timeline = f.timelines[0]
  assert.equal(timeline.plays, 0)
  assert.deepEqual(timeline.steps[0].targets, [f.hero, f.note])
  assert.equal(timeline.steps[1].targets, f.more)
  assert.ok(timeline.steps[1].position > timeline.steps[0].to.duration + timeline.steps[0].to.stagger)
  f.finishShell(); assert.equal(timeline.plays, 1)
  f.finishResults(); assert.ok(f.contexts.every(ctx => ctx.scope === f.root && ctx.reverts === 1))
  f.motion.dispose()
})

test('loading does not consume result entrance; delayed data starts after the completed menu', () => {
  const f = fixture(); f.motion.enterPage(); f.motion.updateResults('loading', false)
  f.finishShell(); assert.equal(f.timelines.length, 0)
  assert.equal(f.entrance.featured, false)
  f.motion.updateResults('ready', true)
  assert.equal(f.timelines[0].plays, 1)
  f.finishResults(); f.motion.dispose()
})

test('filters, reloads, batches and route remounts do not replay played modules; document refresh resets', () => {
  const entrance = createRecommendationEntranceState()
  const f = fixture({ entrance }); f.motion.enterPage(); f.motion.updateResults('first', true)
  f.finishShell(); f.finishResults()
  f.motion.updateResults('first', true)
  f.motion.updateResults('loading', false); f.motion.updateResults('different-batch', true)
  assert.equal(f.timelines.length, 1)
  f.motion.dispose()
  const routeReturn = fixture({ entrance }); routeReturn.motion.enterPage(); routeReturn.motion.updateResults('new-route', true)
  assert.equal(routeReturn.calls.length, 0); assert.equal(routeReturn.timelines.length, 0)
  routeReturn.motion.dispose()
  const refresh = fixture(); refresh.motion.enterPage()
  assert.equal(refresh.calls.length, 1)
  refresh.motion.dispose()
})

test('an initially absent section can enter when first available, without replaying featured', () => {
  const f = fixture(); f.root.more = null
  f.motion.enterPage(); f.finishShell(); f.motion.updateResults('single', true); f.finishResults()
  assert.equal(f.entrance.more, false)
  f.root.more = f.more; f.motion.updateResults('appended', true)
  assert.equal(f.timelines[1].steps.length, 1)
  assert.equal(f.timelines[1].steps[0].targets, f.more)
  f.finishResults(); f.motion.dispose()
})

test('invalidation cancels hidden waiting stages; late shell completion cannot restart cancelled work', () => {
  const f = fixture(); f.motion.enterPage(); f.motion.updateResults('first', true)
  const cancelled = f.timelines[0]
  f.motion.updateResults('first', false)
  assert.equal(f.contexts[1].reverts, 1)
  f.finishShell(); assert.equal(cancelled.plays, 0)
  f.motion.updateResults('retry', true); assert.equal(f.timelines[1].plays, 1)
  f.motion.dispose(); f.motion.dispose()
  assert.ok(f.contexts.every(ctx => ctx.reverts === 1))
  assert.equal(f.listeners.size, 0); assert.equal(f.mediaListeners.size, 0)
})

test('reduced motion restores visible CSS mid-flight and never replays after preference changes', () => {
  const f = fixture({ reduced: true }); f.motion.enterPage(); f.motion.updateResults('first', true)
  f.reduced(false); f.motion.updateResults('second', true)
  assert.equal(f.calls.length, 0); assert.equal(f.timelines.length, 0)
  f.motion.dispose()
  const active = fixture(); active.motion.enterPage(); active.motion.updateResults('first', true)
  active.reduced(true)
  assert.ok(active.contexts.every(ctx => ctx.reverts === 1))
  active.finishShell(); assert.equal(active.timelines[0].plays, 0)
  active.reduced(false); active.motion.updateResults('second', true)
  assert.equal(active.timelines.length, 1)
  active.motion.dispose()
})

test('StrictMode cleanup before first completion leaves the real mount able to enter', () => {
  const entrance = createRecommendationEntranceState()
  const probe = fixture({ entrance }); probe.motion.enterPage(); probe.motion.dispose()
  assert.equal(entrance.shell, false)
  const real = fixture({ entrance }); real.motion.enterPage(); real.finishShell()
  real.motion.updateResults('ready', true); real.finishResults(); real.motion.dispose()
  assert.equal(entrance.shell, true)
})

test('leaving after the menu starts consumes its entrance even before completion', () => {
  const entrance = createRecommendationEntranceState()
  const first = fixture({ entrance }); first.motion.enterPage()
  first.calls[0].to.onStart(); first.motion.dispose()
  const returned = fixture({ entrance }); returned.motion.enterPage()
  assert.equal(returned.calls.length, 0)
  returned.motion.dispose()
})

test('dialog and evidence entrances stay local and independent of once-only page state', () => {
  const f = fixture({ entrance: { shell: true, featured: true, more: true } })
  f.motion.enterDialog('preferences'); assert.equal(f.calls.length, 1)
  const body = { isConnected: true }
  const details = { isConnected: true, open: true, matches: () => true, closest: () => null, querySelector: () => body }
  f.root.body = body; f.root.details = details
  f.listeners.get('toggle')({ target: details }); assert.equal(f.calls.length, 2)
  details.open = false; f.listeners.get('toggle')({ target: details }); assert.equal(f.contexts[1].reverts, 1)
  details.open = true; details.closest = () => ({})
  f.listeners.get('toggle')({ target: details }); assert.equal(f.calls.length, 2)
  f.motion.dispose()
})
