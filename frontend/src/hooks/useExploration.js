import { useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from 'react'
import { createExplorationBrowseController } from '../utils/explorationSession.js'

export default function useExploration(client, query, scope, cache) {
  const controller = useMemo(() => {
    if (cache.client !== client || !cache.controller) { cache.client = client; cache.controller = createExplorationBrowseController(client, scope) }
    return cache.controller
  }, [client, scope, cache])
  const key = JSON.stringify([query, scope])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useLayoutEffect(() => {
    controller.activate(); controller.resume({ key, query })
    return () => controller.dispose()
  }, [controller, key])
  useEffect(() => {
    const verify = () => { if (document.visibilityState !== 'hidden') controller.verify() }
    const changed = () => controller.contextChanged()
    const storage = event => { if (event.key === 'anime-dashboard:recommendation-invalidate:v1') verify() }
    window.addEventListener('focus', verify); window.addEventListener('pageshow', verify)
    window.addEventListener('storage', storage); window.addEventListener('recommendation-data-changed', changed)
    document.addEventListener('visibilitychange', verify)
    const remaining = state.data?.snapshot_expires_at * 1000 - Date.now()
    const timer = Number.isFinite(remaining) ? window.setTimeout(verify, Math.max(0, remaining) + 20) : null
    const interval = state.data ? window.setInterval(verify, 60000) : null
    return () => {
      window.removeEventListener('focus', verify); window.removeEventListener('pageshow', verify)
      window.removeEventListener('storage', storage); window.removeEventListener('recommendation-data-changed', changed)
      document.removeEventListener('visibilitychange', verify); window.clearTimeout(timer); window.clearInterval(interval)
    }
  }, [controller, state.data?.snapshot_expires_at])
  return { ...(state.key === key ? state : { key, status: query ? 'loading' : 'idle', items: [], transport: 'explore' }),
    controller, retry: controller.retry, loadMore: controller.loadMore, nextBatch: controller.nextBatch }
}
