// Presentation only. No business callbacks, requests, timers or global GSAP kills.
export const REC_MOTION = Object.freeze({ shell: .32, result: .36, dialog: .22, evidence: .18, stagger: .025 })

export function createRecommendationMotion({ root, gsap, media,
  observe = typeof IntersectionObserver === 'function' ? callback => new IntersectionObserver(callback, { threshold: .08 }) : null }) {
  const active = new Set(), seen = new Map(), pending = new Map()
  let disposed = false, epoch = null, generation = 0, observer = null
  const owns = node => node?.isConnected && (node === root || root.contains(node))
  const stop = entry => {
    if (entry.cancelled) return
    entry.cancelled = true
    entry.context?.revert()
    active.delete(entry)
  }
  const cancel = group => { for (const entry of [...active]) if (!group || entry.group === group) stop(entry) }
  const animate = (nodes, group, duration, distance, stagger = 0) => {
    const targets = [...nodes].filter(owns)
    if (disposed || media?.matches || !targets.length) return
    for (const entry of [...active]) if (entry.targets.some(node => targets.includes(node))) stop(entry)
    const entry = { group, targets, context: null, cancelled: false }
    active.add(entry)
    try {
      entry.context = gsap.context(() => {
        gsap.fromTo(targets, { y: distance, opacity: .55, willChange: 'transform,opacity' }, {
          y: 0, opacity: 1, duration, ease: 'power2.out',
          stagger: Math.min(stagger, .12 / Math.max(1, targets.length - 1)),
          onComplete: () => stop(entry),
        })
      }, root)
    } catch {
      // Optional decoration must never prevent the real page/dialog from working.
      stop(entry)
    }
  }
  const resetResults = () => {
    generation++
    observer?.disconnect(); observer = null
    pending.clear(); seen.clear(); cancel('results'); cancel('evidence')
  }
  const settle = () => {
    generation++
    observer?.disconnect(); observer = null
    pending.clear(); cancel()
  }
  const changed = () => { if (media?.matches) settle() }
  media?.addEventListener('change', changed)
  const toggle = event => {
    const details = event.target
    if (!owns(details) || !details.matches?.('.rec-evidence')) return
    if (details.closest('dialog') && details.closest('dialog') !== root) return
    const body = details.querySelector('.rec-evidence-body')
    for (const entry of [...active]) if (entry.targets.includes(body)) stop(entry)
    if (details.open && body) animate([body], 'evidence', REC_MOTION.evidence, 4)
  }
  root.addEventListener('toggle', toggle, true)
  return {
    enterPage() {
      animate(root.querySelectorAll('.rec-masthead'), 'shell', REC_MOTION.shell, 10)
    },
    enterDialog(kind) {
      // Never delay showModal, inert, focus, close, invalidation or cancellation.
      animate([root], 'dialog', REC_MOTION.dialog, kind === 'filters' || kind === 'preferences' ? 12 : 8)
    },
    updateResults(key, ready) {
      if (disposed) return
      if (!ready || key !== epoch) { resetResults(); epoch = ready ? key : null }
      if (!ready) return
      for (const node of [...seen.keys()]) if (!owns(node)) { seen.delete(node); pending.delete(node); observer?.unobserve(node) }
      const cards = [...root.querySelectorAll('[data-rec-motion-id]')]
        .filter(node => seen.get(node) !== node.dataset.recMotionId)
      for (const node of cards) seen.set(node, node.dataset.recMotionId)
      if (media?.matches) return // CSS default is fully visible; don't replay on opt-in.
      const featured = cards.filter(node => node.classList.contains('rec-card--hero'))
      animate(featured, 'results', REC_MOTION.result, 16)
      if (featured.length) {
        const note = root.querySelector('.rec-taste-note')
        if (note) animate([note], 'results', REC_MOTION.result, 10)
      }
      const grid = cards.filter(node => !node.classList.contains('rec-card--hero'))
      if (!observe) { animate(grid, 'results', REC_MOTION.result, 14, REC_MOTION.stagger); return }
      if (!grid.length) return
      if (!observer) {
        const observedGeneration = generation
        observer = observe(entries => {
          if (disposed || generation !== observedGeneration || media?.matches) return
          const visible = entries.filter(entry => entry.isIntersecting && owns(entry.target)
            && pending.get(entry.target) === entry.target.dataset.recMotionId).map(entry => entry.target)
          for (const node of visible) { pending.delete(node); observer?.unobserve(node) }
          animate([...new Set(visible)], 'results', REC_MOTION.result, 14, REC_MOTION.stagger)
        })
      }
      for (const node of grid) { pending.set(node, node.dataset.recMotionId); observer.observe(node) }
    },
    dispose() {
      if (disposed) return
      disposed = true; generation++
      settle(); seen.clear()
      media?.removeEventListener('change', changed)
      root.removeEventListener('toggle', toggle, true)
    },
  }
}
