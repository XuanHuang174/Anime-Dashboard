import { useLayoutEffect, useRef } from 'react'
import gsap from 'gsap'
import { createRecommendationMotion, createRecommendationEntranceState } from '../utils/recommendationMotion.js'
const entrances = { search: createRecommendationEntranceState(), entity: createRecommendationEntranceState() }
export default function useCreditsMotion(root, kind, ready, epoch) {
  const motion = useRef(null)
  useLayoutEffect(() => {
    const instance = createRecommendationMotion({ root: root.current, gsap, entrance: entrances[kind],
      media: window.matchMedia('(prefers-reduced-motion: reduce)') })
    motion.current = instance; instance.enterPage()
    return () => { instance.dispose(); motion.current = null }
  }, [root, kind])
  useLayoutEffect(() => { motion.current?.updateResults(epoch, ready) }, [ready, epoch])
}
