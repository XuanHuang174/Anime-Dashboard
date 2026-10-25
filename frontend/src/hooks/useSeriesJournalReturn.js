import { createContext, useContext, useEffect, useRef } from 'react'
export const SeriesOverlayContext = createContext(false)

// Refresh only after returning from a series write; filters, preview and selection survive.
export default function useSeriesJournalReturn(onRefresh) {
  const overlay = useContext(SeriesOverlayContext), dirty = useRef(false), callback = useRef(onRefresh)
  callback.current = onRefresh
  useEffect(() => {
    const changed = () => { if (overlay) dirty.current = true }
    window.addEventListener('recommendation-data-changed', changed)
    return () => window.removeEventListener('recommendation-data-changed', changed)
  }, [overlay])
  useEffect(() => {
    if (!overlay && dirty.current) { dirty.current = false; callback.current() }
  }, [overlay])
}
