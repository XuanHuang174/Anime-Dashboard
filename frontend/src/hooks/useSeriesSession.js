import { useEffect, useState } from 'react'
import { getAccessToken } from '../auth/tokenStorage.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'

export function seriesSession() {
  try { return recommendationSessionScope(getAccessToken()) } catch { return 'anonymous' }
}
export default function useSeriesSession() {
  const [session, setSession] = useState(seriesSession)
  useEffect(() => {
    const check = () => setSession(seriesSession())
    window.addEventListener('storage', check); window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', check)
    return () => { window.removeEventListener('storage', check); window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', check) }
  }, [])
  return session
}
