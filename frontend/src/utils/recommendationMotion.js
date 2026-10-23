// Presentation only. No business callbacks, requests, timers or global GSAP kills.
export const REC_MOTION = Object.freeze({ shell: .78, result: .48, dialog: .22, evidence: .18, stagger: .1 })

export const createRecommendationEntranceState = () => ({ shell: false, featured: false, more: false })

export function createRecommendationMotion({ root, gsap, media, entrance = createRecommendationEntranceState() }) {
  const active = new Set()
  let disposed = false, epoch = null, resultsEntry = null, shellFinished = entrance.shell, pageEntered = false
  const owns = node => node?.isConnected && (node === root || root.contains(node))
  const stop = entry => {
    if (entry.cancelled) return
    entry.cancelled = true
    entry.context?.revert()
    active.delete(entry)
    if (resultsEntry === entry) resultsEntry = null
  }
  const cancel = group => { for (const entry of [...active]) if (!group || entry.group === group) stop(entry) }
  const animate = (nodes, group, duration, distance, stagger = 0, options = {}) => {
    const targets = [...nodes].filter(owns)
    if (disposed || media?.matches || !targets.length) return
    for (const entry of [...active]) if (entry.targets.some(node => targets.includes(node))) stop(entry)
    const entry = { group, targets, context: null, cancelled: false }
    active.add(entry)
    try {
      entry.context = gsap.context(() => {
        gsap.fromTo(targets, { y: distance, opacity: .55, willChange: 'transform,opacity', ...options.from }, {
          y: 0, opacity: 1, duration, ease: 'power2.out',
          stagger: Math.min(stagger, .12 / Math.max(1, targets.length - 1)),
          ...options.to,
          onComplete: () => { stop(entry); options.onComplete?.() },
        })
      }, root)
    } catch {
      // Optional decoration must never prevent the real page/dialog from working.
      stop(entry)
      options.onComplete?.()
    }
  }
  const consumeEntrance = () => { entrance.shell = entrance.featured = entrance.more = true }
  const changed = () => { if (media?.matches) { consumeEntrance(); cancel(); shellFinished = true } }
  const enterResults = () => {
    if (disposed || media?.matches || resultsEntry) return
    const featured = entrance.featured ? [] : [...root.querySelectorAll('.rec-featured-main, .rec-taste-note')].filter(owns)
    const more = entrance.more ? null : root.querySelector('.rec-more-section')
    const targets = [...featured, ...(owns(more) ? [more] : [])]
    if (!targets.length) return
    const entry = { group: 'results', targets, context: null, cancelled: false, timeline: null }
    resultsEntry = entry; active.add(entry)
    try {
      entry.context = gsap.context(() => {
        // Prepare ready modules before paint, including the section waiting its turn.
        // Context cleanup restores ordinary visible CSS on completion or cancellation.
        gsap.set(targets, { autoAlpha: 0, willChange: 'transform,opacity' })
        entry.timeline = gsap.timeline({ paused: true, onComplete: () => stop(entry) })
        const from = { y: 42, scale: .96, autoAlpha: 0 }
        const to = { y: 0, scale: 1, autoAlpha: 1, duration: REC_MOTION.result, ease: 'back.out(1.5)' }
        if (featured.length) entry.timeline.fromTo(featured, from, { ...to, stagger: REC_MOTION.stagger,
          onStart: () => { entrance.featured = true } }, 0)
        if (owns(more)) entry.timeline.fromTo(more, from, { ...to,
          onStart: () => { entrance.more = true } }, featured.length ? REC_MOTION.result + (featured.length - 1) * REC_MOTION.stagger + .08 : 0)
      }, root)
      if (shellFinished) entry.timeline.play()
    } catch {
      consumeEntrance(); stop(entry)
    }
  }
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
      if (disposed || pageEntered) return
      pageEntered = true
      if (media?.matches) { consumeEntrance(); shellFinished = true; return }
      const shell = [...root.querySelectorAll('.rec-masthead')].filter(owns)
      if (entrance.shell || !shell.length) { entrance.shell = shellFinished = true; return }
      animate(shell, 'shell', REC_MOTION.shell, -80, 0, {
        from: { autoAlpha: 0, rotation: -1.5 }, to: { autoAlpha: 1, rotation: 0, ease: 'bounce.out',
          onStart: () => { entrance.shell = true } },
        onComplete: () => {
          entrance.shell = shellFinished = true
          if (!disposed) resultsEntry?.timeline.play()
        },
      })
    },
    enterDialog(kind) {
      // Never delay showModal, inert, focus, close, invalidation or cancellation.
      animate([root], 'dialog', REC_MOTION.dialog, kind === 'filters' || kind === 'preferences' ? 12 : 8)
    },
    updateResults(key, ready) {
      if (disposed) return
      if (!ready || key !== epoch) { cancel('results'); cancel('evidence'); epoch = ready ? key : null }
      if (!ready) return
      if (media?.matches) { consumeEntrance(); return }
      enterResults()
    },
    dispose() {
      if (disposed) return
      disposed = true
      cancel()
      media?.removeEventListener('change', changed)
      root.removeEventListener('toggle', toggle, true)
    },
  }
}
