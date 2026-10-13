import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { createWantController } from '../utils/recommendationWant.js'

export default function useRecommendationWant(client) {
  const controller = useMemo(() => createWantController(client), [client])
  const operations = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  return { operations, add: controller.add, resetFailures: controller.resetFailures, reconcile: controller.reconcile }
}
