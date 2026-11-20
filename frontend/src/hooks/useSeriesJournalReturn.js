import { createContext, useContext, useEffect, useRef } from 'react'
export const SeriesOverlayContext = createContext(false)
// A credits workspace stays mounted beneath this layer, including its detail.
export const SeriesForegroundContext = createContext(false)

// Refresh after returning from an exploration/series write; keep the journal's UI state.
export default function useSeriesJournalReturn(onRefresh) {
  const overlay = useContext(SeriesOverlayContext), dirty = useRef(false), callback = useRef(onRefresh)
  callback.current = onRefresh
  useEffect(() => {
    const changed = () => { if (overlay) dirty.current = true }
    window.addEventListener('recommendation-data-changed', changed)
    // Exploration confirms its own write locally, so local:false only emits
    // this companion invalidation rather than restarting its browse session.
    window.addEventListener('series-reminders-data-changed', changed)
    return () => {
      window.removeEventListener('recommendation-data-changed', changed)
      window.removeEventListener('series-reminders-data-changed', changed)
    }
  }, [overlay])
  useEffect(() => {
    if (!overlay && dirty.current) { dirty.current = false; callback.current() }
  }, [overlay])
}
