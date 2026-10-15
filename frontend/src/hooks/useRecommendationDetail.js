import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createRecommendationDetailController } from '../utils/recommendationDetail.js'

export default function useRecommendationDetail(client) {
  const controller = useMemo(() => createRecommendationDetailController(client), [client])
  const detail = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  return { detail, controller }
}
