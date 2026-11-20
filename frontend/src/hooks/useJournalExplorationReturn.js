import { createContext, useLayoutEffect, useRef } from 'react'
import useSeriesSession from './useSeriesSession.js'

export const JournalExplorationAwayContext = createContext(false)

// A single original journal, scoped to this mounted router and current account.
// No records, credentials or return locations are written to browser storage.
export default function useJournalExplorationReturn(location) {
  const session = useSeriesSession(), previous = useRef(location), retained = useRef(null)
  const wasAway = useRef(false), returnScroll = useRef(0)
  const exploring = /^\/recommendations(?:\/|$)/.test(location.pathname)
  const entry = /^\/recommendations(?:\/(?:similar|tags))?\/?$/.test(location.pathname)
  // Direct series/credits entries already retain their own original route.
  if (previous.current.pathname === '/dashboard' && entry) {
    retained.current = { location: previous.current, session }
    returnScroll.current = window.scrollY
  }
  if (!exploring || retained.current?.session !== session) retained.current = null
  previous.current = location
  const origin = retained.current?.location || null
  useLayoutEffect(() => {
    const restore = wasAway.current && !origin && location.pathname === '/dashboard'
    wasAway.current = Boolean(origin)
    if (!restore) return
    const frame = requestAnimationFrame(() => window.scrollTo({ top: returnScroll.current, behavior: 'instant' }))
    return () => cancelAnimationFrame(frame)
  }, [origin, location.pathname])
  return { origin, session }
}
