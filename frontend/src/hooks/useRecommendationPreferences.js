import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createPreferenceEditor } from '../utils/recommendationPreferences.js'

export default function useRecommendationPreferences(client, onSaved) {
  const callback = useRef(onSaved)
  callback.current = onSaved
  const controller = useMemo(() => createPreferenceEditor(client, data => callback.current(data)), [client])
  const editor = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  return { editor, ...controller }
}
