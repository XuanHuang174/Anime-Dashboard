import { useLayoutEffect, useMemo, useSyncExternalStore } from 'react'
import recommendationApi from '../api/recommendations.js'
import { createBrowseController } from '../utils/recommendationBrowseSession.js'

export default function useRecommendations(category, filters, client = recommendationApi, style = 'balanced', cache) {
  const controller = useMemo(() => {
    if (!cache) return createBrowseController(client)
    if (cache.client !== client || !cache.controller) { cache.client = client; cache.controller = createBrowseController(client) }
    return cache.controller
  }, [client, cache])
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const key = JSON.stringify([category, style, filters.type, filters.minScore, filters.scoreFrom, filters.scoreTo, filters.hideRecorded, filters.yearFrom, filters.yearTo, !!filters.includeAdult])
  useLayoutEffect(() => {
    controller.activate()
    if (cache) controller.resume({ key, category, style, filters })
    else controller.start({ key, category, style, filters })
    return () => controller.dispose()
    // Only applied primitives trigger reads; filter draft edits do not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, key])
  return { ...(snapshot.key === key ? snapshot : { status: 'loading', items: [] }),
    retry: controller.retry, recordChanged: controller.recordChanged,
    loadMore: controller.loadMore, nextBatch: controller.nextBatch, cancelBrowse: controller.cancel,
    contextChanged: controller.contextChanged }
}
