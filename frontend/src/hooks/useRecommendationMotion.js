import { useLayoutEffect, useRef } from 'react'
import gsap from 'gsap'
import { createRecommendationMotion } from '../utils/recommendationMotion.js'

export default function useRecommendationMotion(rootRef, { status, epoch, ids, blocked }) {
  const motion = useRef(null)
  useLayoutEffect(() => {
    if (!rootRef.current) return undefined
    const controller = createRecommendationMotion({ root: rootRef.current, gsap,
      media: window.matchMedia('(prefers-reduced-motion: reduce)') })
    motion.current = controller
    controller.enterPage()
    return () => { controller.dispose(); motion.current = null }
  }, [rootRef])
  useLayoutEffect(() => {
    motion.current?.updateResults(epoch, status === 'ready' && !blocked)
  }, [status, epoch, ids, blocked])
}
