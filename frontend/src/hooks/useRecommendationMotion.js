import { useLayoutEffect, useRef } from 'react'
import gsap from 'gsap'
import { createRecommendationMotion, createRecommendationEntranceState } from '../utils/recommendationMotion.js'

// In-memory document lifetime: survives route remounts, resets on browser refresh.
const pageEntrance = createRecommendationEntranceState()

export default function useRecommendationMotion(rootRef, { status, epoch, ids, blocked }) {
  const motion = useRef(null)
  useLayoutEffect(() => {
    if (!rootRef.current) return undefined
    const controller = createRecommendationMotion({ root: rootRef.current, gsap, entrance: pageEntrance,
      media: window.matchMedia('(prefers-reduced-motion: reduce)') })
    motion.current = controller
    controller.enterPage()
    return () => { controller.dispose(); motion.current = null }
  }, [rootRef])
  useLayoutEffect(() => {
    motion.current?.updateResults(epoch, status === 'ready' && !blocked)
  }, [status, epoch, ids, blocked])
}
