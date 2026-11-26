import { useEffect, useRef } from 'react'

function useScrollReveal(dependency, disabled = false) {
  const containerRef = useRef(null)

  useEffect(() => {
    if (disabled) return undefined

    const root = containerRef.current
    if (!root) return undefined
    if (!('IntersectionObserver' in window) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      root.querySelectorAll('.animate-item').forEach(element => element.classList.add('is-revealed'))
      return undefined
    }

    let batchDelay = 0
    let timeoutId
    const cleanupTimers = []
    const frames = new Set()
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return

          entry.target.style.animationDelay = `${Math.min(batchDelay * 0.02, 0.1)}s`
          const frame = requestAnimationFrame(() => {
            frames.delete(frame)
            entry.target.classList.add('is-revealed')
            cleanupTimers.push(
              window.setTimeout(() => {
                entry.target.style.willChange = 'auto'
              }, 500),
            )
          })
          frames.add(frame)
          observer.unobserve(entry.target)
          batchDelay += 1
          window.clearTimeout(timeoutId)
          timeoutId = window.setTimeout(() => {
            batchDelay = 0
          }, 50)
        })
      },
      { root: null, threshold: 0, rootMargin: '0px 0px 100px 0px' },
    )

    const tracked = new Set()
    const observeNew = () => root.querySelectorAll('.animate-item').forEach((element) => {
      if (tracked.has(element) || element.classList.contains('is-revealed')) return
      tracked.add(element)
      element.classList.add('is-reveal-pending')
      element.style.animationDelay = '0s'
      element.style.willChange = 'opacity, transform'
      observer.observe(element)
    })
    observeNew()
    const mutations = new MutationObserver(observeNew)
    mutations.observe(root, { childList: true, subtree: true })

    return () => {
      observer.disconnect()
      mutations.disconnect()
      frames.forEach(cancelAnimationFrame)
      tracked.forEach(element => { element.classList.remove('is-reveal-pending'); element.style.willChange = 'auto' })
      window.clearTimeout(timeoutId)
      cleanupTimers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [dependency, disabled])

  return containerRef
}

export default useScrollReveal
