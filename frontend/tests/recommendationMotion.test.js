import test from 'node:test'
import assert from 'node:assert/strict'
import { createRecommendationMotion, REC_MOTION } from '../src/utils/recommendationMotion.js'

function fixture({ reduced = false, observerAvailable = true } = {}) {
  const calls = [], contexts = [], observers = [], listeners = new Map(), mediaListeners = new Set()
  const node = (id, hero = false) => ({ isConnected: true, dataset: { recMotionId: id },
    classList: { contains: name => hero && name === 'rec-card--hero' } })
  const hero = node('101', true), card = node('102'), note = node('note'), masthead = node('head')
  const root = { cards: [hero, card], isConnected: true,
    contains: target => [masthead, note, ...root.cards, root.body, root.details].includes(target),
    querySelectorAll: selector => selector === '.rec-masthead' ? [masthead] : root.cards,
    querySelector: () => note,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) } }
  const media = { matches: reduced, addEventListener: (_, fn) => mediaListeners.add(fn), removeEventListener: (_, fn) => mediaListeners.delete(fn) }
  const gsap = { context: (fn, scope) => {
    const context = { scope, reverts: 0, revert() { this.reverts++ } }; contexts.push(context); fn(); return context
  }, fromTo: (targets, from, to) => calls.push({ targets, from, to }) }
  const observe = observerAvailable ? callback => {
    const observer = { callback, nodes: new Set(), disconnected: false,
      observe(target) { this.nodes.add(target) }, unobserve(target) { this.nodes.delete(target) },
      disconnect() { this.disconnected = true; this.nodes.clear() } }
    observers.push(observer); return observer
  } : null
  const motion = createRecommendationMotion({ root, gsap, media, observe })
  return { root, hero, card, note, calls, contexts, observers, listeners, mediaListeners, motion, node,
    visible(observer = observers.at(-1), targets = [...observer.nodes]) { observer.callback(targets.map(target => ({ target, isIntersecting: true }))) },
    reduced(value) { media.matches = value; for (const fn of mediaListeners) fn() } }
}

test('R3 uses short scoped GSAP entries and once-only viewport card reveal without hiding static content', () => {
  const f = fixture(); f.motion.enterPage(); f.motion.updateResults('a', true)
  assert.equal(f.calls.length, 3) // masthead, hero, taste; offscreen card is untouched
  f.visible(); assert.equal(f.calls.length, 4)
  for (const call of f.calls) {
    assert.ok(call.from.opacity >= .5); assert.ok(call.to.duration <= .4)
    assert.equal(call.to.opacity, 1); assert.equal(call.to.y, 0)
    assert.ok(call.to.stagger * (call.targets.length - 1) <= .12)
  }
  for (const call of f.calls) call.to.onComplete()
  assert.ok(f.contexts.every(ctx => ctx.scope === f.root && ctx.reverts === 1))
  f.motion.dispose()
})

test('R3 append animates only new DOM identities; ordinary button/status renders do not replay old cards', () => {
  const f = fixture(); f.motion.updateResults('a', true); f.visible()
  const before = f.calls.length
  f.motion.updateResults('a', true); assert.equal(f.calls.length, before)
  const added = f.node('103'); f.root.cards.push(added)
  f.motion.updateResults('a', true); f.visible()
  assert.equal(f.calls.length, before + 1)
  assert.deepEqual(f.calls.at(-1).targets, [added])
  f.motion.dispose()
})

test('R3 changed context cancels old scopes and rejects disconnected or late observer callbacks', () => {
  const f = fixture(); f.motion.updateResults('a', true)
  const old = f.observers[0], oldCard = f.card
  const oldComplete = f.calls[0].to.onComplete
  f.root.cards = [f.node('201', true), f.node('202')]
  f.motion.updateResults('b', true)
  const count = f.calls.length
  old.callback([{ target: oldCard, isIntersecting: true }]); oldComplete()
  assert.equal(f.calls.length, count); assert.equal(old.disconnected, true)
  assert.equal(f.contexts[0].reverts, 1)
  f.visible(); assert.deepEqual(f.calls.at(-1).targets, [f.root.cards[1]])
  f.motion.dispose()
})

test('R3 reduced motion works on first render and cancels mid-flight scopes without replay on preference change', () => {
  const f = fixture({ reduced: true }); f.motion.enterPage(); f.motion.updateResults('a', true)
  assert.equal(f.calls.length, 0); assert.equal(f.observers.length, 0)
  f.reduced(false); f.motion.updateResults('a', true); assert.equal(f.calls.length, 0)
  f.root.cards.push(f.node('103')); f.motion.updateResults('a', true); f.visible()
  const old = f.observers.at(-1); assert.equal(f.calls.length, 1)
  f.reduced(true); assert.equal(f.contexts[0].reverts, 1); assert.equal(old.disconnected, true)
  old.callback([{ target: f.root.cards[2], isIntersecting: true }]); assert.equal(f.calls.length, 1)
  f.reduced(false)
  f.root.cards.push(f.node('104')); f.motion.updateResults('a', true)
  old.callback([{ target: f.root.cards[3], isIntersecting: true }]); assert.equal(f.calls.length, 1)
  f.visible(); assert.equal(f.calls.length, 2)
  f.motion.dispose()
})

test('R3 loading/blocked transitions and repeated disposal release local contexts, listeners and observers', () => {
  const f = fixture(); f.motion.updateResults('a', true)
  f.motion.updateResults('a', false)
  assert.ok(f.contexts.every(ctx => ctx.reverts === 1)); assert.ok(f.observers.every(observer => observer.disconnected))
  f.motion.dispose(); f.motion.dispose()
  assert.equal(f.listeners.size, 0); assert.equal(f.mediaListeners.size, 0)
  const count = f.calls.length; f.motion.updateResults('b', true); assert.equal(f.calls.length, count)
})

test('R3 missing IntersectionObserver still reveals cards, and total stagger is bounded for a full batch', () => {
  const f = fixture({ observerAvailable: false })
  f.root.cards.push(...Array.from({ length: 22 }, (_, index) => f.node(String(index + 103))))
  f.motion.updateResults('a', true)
  assert.equal(f.calls.at(-1).targets.length, 23)
  assert.ok(f.calls.at(-1).to.stagger * 22 <= .12)
  assert.equal(REC_MOTION.result, .36)
  f.motion.dispose()
})

test('R3 evidence reveal has one owner; closing details cancels local motion without business callbacks', () => {
  const f = fixture(); const body = f.node('body')
  const details = { ...f.node('details'), open: true, matches: () => true, closest: () => null, querySelector: () => body }
  f.root.body = body; f.root.details = details
  f.listeners.get('toggle')({ target: details }); assert.equal(f.calls.length, 1)
  details.open = false; f.listeners.get('toggle')({ target: details }); assert.equal(f.contexts[0].reverts, 1)
  details.open = true; details.closest = () => ({}) // page must not also own native-dialog evidence
  f.listeners.get('toggle')({ target: details }); assert.equal(f.calls.length, 1)
  f.motion.dispose()
})

test('R3 reusing a DOM node for a new identity cancels its old tween before starting another', () => {
  const f = fixture(); f.motion.updateResults('a', true)
  const old = f.calls[0], oldContext = f.contexts[0]
  f.hero.dataset.recMotionId = '201'
  f.motion.updateResults('a', true)
  assert.equal(oldContext.reverts, 1)
  old.to.onComplete(); assert.equal(oldContext.reverts, 1)
  const current = f.calls.findLast(call => call.targets.includes(f.hero))
  assert.notEqual(current, old)
  current.to.onComplete(); f.motion.dispose()
  assert.ok(f.contexts.every(context => context.reverts === 1))
})
