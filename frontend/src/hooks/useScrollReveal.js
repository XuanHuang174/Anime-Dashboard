import { useEffect, useRef } from 'react'

function useScrollReveal(dependency, disabled = false) {
  const containerRef = useRef(null)

  useEffect(() => {
    if (disabled) return undefined

    const elements = containerRef.current?.querySelectorAll('.animate-item') ?? []
    if (!('IntersectionObserver' in window)) {
      elements.forEach((element) => element.classList.add('is-revealed'))
      return undefined
    }

    let batchDelay = 0
    let timeoutId
    const cleanupTimers = []
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return

          entry.target.style.animationDelay = `${Math.min(batchDelay * 0.02, 0.1)}s`
          requestAnimationFrame(() => {
            entry.target.classList.add('is-revealed')
            cleanupTimers.push(
              window.setTimeout(() => {
                entry.target.style.willChange = 'auto'
              }, 500),
            )
          })
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

    elements.forEach((element) => {
      element.style.animationDelay = '0s'
      element.style.willChange = 'opacity, transform'
      observer.observe(element)
    })

    return () => {
      observer.disconnect()
      window.clearTimeout(timeoutId)
      cleanupTimers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [dependency, disabled])

  return containerRef
}

export default useScrollReveal
