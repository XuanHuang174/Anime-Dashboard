import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createFeedbackController, createHiddenFeedbackController } from '../utils/recommendationFeedback.js'

export default function useRecommendationFeedback(client, onConfirmed, writeGate, onVersionChanged) {
  const latest = useRef(onConfirmed); latest.current = onConfirmed
  const versionChanged = useRef(onVersionChanged); versionChanged.current = onVersionChanged
  const controller = useMemo(() => createFeedbackController(client, result => latest.current(result), { writeGate }), [client, writeGate])
  const hidden = useMemo(() => createHiddenFeedbackController(client, version => {
    const previous = controller.getSnapshot().version
    controller.observeVersion(version)
    if (previous !== null && version > previous) versionChanged.current?.(version)
  }, () => controller.getSnapshot().version), [client, controller])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const hiddenState = useSyncExternalStore(hidden.subscribe, hidden.getSnapshot, hidden.getSnapshot)
  useEffect(() => { controller.activate(); return () => { controller.dispose(); hidden.dispose() } }, [controller, hidden])
  return { state, controller, hidden, hiddenState }
}
