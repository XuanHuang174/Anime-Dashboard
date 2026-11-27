import { useLayoutEffect, useRef } from 'react'

// Layout initialization and mutation enrollment both run before the next paint.
// Keep state outside React's className so edits cannot restart an existing card.
function useScrollReveal(_dependency, disabled = false) {
  const containerRef = useRef(null)

  useLayoutEffect(() => {
    const root = containerRef.current
    if (!root) return undefined
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const tracked = new Set(), animations = new Map()
    let disposed = false
    const settle = element => {
      element.dataset.revealState = 'done'
      const animation = animations.get(element)
      animations.delete(element)
      animation?.cancel()
    }
    const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
      let index = 0
      for (const { target, isIntersecting } of entries) {
        if (!isIntersecting) continue
        observer.unobserve(target)
        if (disposed || disabled || media.matches || !target.animate) { settle(target); continue }
        target.dataset.revealState = 'entering'
        const animation = target.animate([
          { opacity: 0, transform: 'translateY(20px)' },
          { opacity: 1, transform: 'translateY(0)' },
        ], { duration: 400, delay: Math.min(index++ * 20, 100), easing: 'ease-out', fill: 'both' })
        animations.set(target, animation)
        animation.finished.then(() => { if (!disposed) settle(target) }, () => {})
      }
    }, { threshold: 0, rootMargin: '0px 0px 100px 0px' }) : null
    const enroll = () => {
      for (const element of tracked) {
        if (!root.contains(element)) { observer?.unobserve(element); settle(element); tracked.delete(element) }
      }
      root.querySelectorAll('.animate-item').forEach(element => {
        if (tracked.has(element)) return
        tracked.add(element)
        if (element.dataset.revealState === 'done' || disabled || media.matches || !observer || !element.animate) {
          settle(element)
        } else {
          element.dataset.revealState = 'pending'
          observer.observe(element)
        }
      })
    }
    const reduce = () => {
      if (media.matches) tracked.forEach(element => { observer?.unobserve(element); settle(element) })
    }
    enroll()
    const mutations = new MutationObserver(enroll)
    mutations.observe(root, { childList: true, subtree: true })
    media.addEventListener('change', reduce)
    return () => {
      disposed = true
      observer?.disconnect(); mutations.disconnect(); media.removeEventListener('change', reduce)
      // StrictMode replays layout setup before paint. Unfinished cards remain
      // pending for the next setup rather than being mistaken for completed ones.
      tracked.forEach(element => {
        if (element.dataset.revealState !== 'done') element.dataset.revealState = 'pending'
        animations.get(element)?.cancel()
      })
    }
  }, [disabled])

  return containerRef
}

export default useScrollReveal
