import { useEffect, useRef } from 'react'
import { subscribeRecommendationReload } from '../utils/recommendationReload.js'

export default function useRecommendationReload(onReload, blocked, client, context) {
  const latest = useRef({ onReload, blocked, client, context }), subscription = useRef(null)
  latest.current = { onReload, blocked, client, context }
  useEffect(() => {
    const listener = subscribeRecommendationReload({ onReload: reason => latest.current.onReload(reason),
      probe: client?.context ? signal => latest.current.client.context(signal) : undefined,
      getContext: () => latest.current.context,
      canReload: () => !latest.current.blocked })
    subscription.current = listener
    return () => { listener.dispose(); subscription.current = null }
  }, [])
  useEffect(() => { if (!blocked) subscription.current?.flush() }, [blocked])
  return () => subscription.current?.acknowledge()
}
