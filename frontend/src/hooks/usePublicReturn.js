import { useEffect, useState } from 'react'
import api from '../api/api.js'
import { getAccessToken } from '../auth/tokenStorage.js'

// Public pages remain public if a saved login has expired. A successful profile
// read (including normal token refresh) confirms where the return action leads.
export default function usePublicReturn() {
  const [authenticated, setAuthenticated] = useState(false)
  useEffect(() => {
    let request
    const check = () => {
      request?.abort()
      if (!getAccessToken()) { setAuthenticated(false); return }
      request = new AbortController()
      const current = request
      api.get('user/profile/', { signal: current.signal, optionalAuth: true }).then(() => {
        if (!current.signal.aborted) setAuthenticated(true)
      }).catch(() => { if (!current.signal.aborted) setAuthenticated(false) })
    }
    check()
    window.addEventListener('storage', check)
    window.addEventListener('focus', check)
    return () => { request?.abort(); window.removeEventListener('storage', check); window.removeEventListener('focus', check) }
  }, [])
  return authenticated ? { path: '/dashboard', label: '返回我的手账' } : { path: '/', label: '返回展示主界面' }
}
