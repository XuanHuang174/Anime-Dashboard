import { useEffect, useRef } from 'react'
import { subscribeRecommendationReload } from '../utils/recommendationReload.js'

export default function useRecommendationReload(onReload, blocked) {
  const latest = useRef({ onReload, blocked }), subscription = useRef(null)
  latest.current = { onReload, blocked }
  useEffect(() => {
    const listener = subscribeRecommendationReload({ onReload: () => latest.current.onReload(),
      canReload: () => !latest.current.blocked })
    subscription.current = listener
    return () => { listener.dispose(); subscription.current = null }
  }, [])
  useEffect(() => { if (!blocked) subscription.current?.flush() }, [blocked])
  return () => subscription.current?.acknowledge()
}
