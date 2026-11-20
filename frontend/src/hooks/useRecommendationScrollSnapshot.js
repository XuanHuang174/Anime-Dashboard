import { useLayoutEffect } from 'react'

export default function useRecommendationScrollSnapshot(cache) {
  useLayoutEffect(() => {
    let restored = false
    const top = cache.scroll ?? 0
    // Track while the old page is still present. Reading on unmount can capture
    // the shorter destination's already-clamped scroll position instead.
    const remember = () => { if (restored) cache.scroll = window.scrollY }
    window.addEventListener('scroll', remember, { passive: true })
    const frame = requestAnimationFrame(() => {
      window.scrollTo({ top, behavior: 'instant' })
      restored = true
    })
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', remember) }
  }, [cache])
}
