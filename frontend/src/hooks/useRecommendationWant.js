import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createWantController } from '../utils/recommendationWant.js'

export default function useRecommendationWant(client, onConfirmed, writeGate) {
  const latest = useRef(onConfirmed)
  latest.current = onConfirmed
  const controller = useMemo(() => createWantController(client, result => latest.current?.(result), { writeGate }), [client, writeGate])
  const operations = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  return { operations, add: controller.add, resetFailures: controller.resetFailures, reconcile: controller.reconcile }
}
