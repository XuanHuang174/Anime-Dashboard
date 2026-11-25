import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from './useSeriesSession.js'
import { createPlanController } from '../utils/watchPlans.js'

export default function useWatchPlan({ recordId = null, enabled = true, onCommitted }) {
  const session = useSeriesSession(), callback = useRef(onCommitted); callback.current = onCommitted
  const engine = useMemo(() => createPlanController({ client: api, recordId, getSession: seriesSession, onCommitted: (...args) => callback.current?.(...args) }), [recordId, session])
  const snapshot = useSyncExternalStore(engine.subscribe, engine.getSnapshot)
  useEffect(() => { engine.activate(); return () => engine.dispose() }, [engine])
  useEffect(() => { if (enabled && engine.getSnapshot().status === 'idle') engine.read() }, [engine, enabled])
  useEffect(() => {
    if (!enabled) return
    const refresh = () => { const state = engine.getSnapshot(); if (document.visibilityState === 'visible' && state.status === 'ready' && !state.pending) engine.read() }
    window.addEventListener('focus', refresh); document.addEventListener('visibilitychange', refresh)
    return () => { window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh) }
  }, [enabled, engine])
  return { engine, snapshot, session }
}
