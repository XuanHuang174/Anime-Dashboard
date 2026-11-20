import { useCallback, useEffect, useRef, useState } from 'react'
import { connectionRecovery, isConnectionFailure } from '../api/connectionRecovery.js'
import { getAccessToken } from '../auth/tokenStorage.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'

// Only a mounted resource that actually failed is re-read; successful resources
// and unsaved editing state do not get remounted on focus/online/manual recovery.
export default function useReadRecovery() {
  const failed = useRef(false)
  const owner = useRef(recommendationSessionScope(getAccessToken()))
  const [readVersion, setReadVersion] = useState(0)
  useEffect(() => {
    let version = connectionRecovery.getSnapshot().version
    return connectionRecovery.subscribe(() => {
      const next = connectionRecovery.getSnapshot().version
      if (next === version) return
      version = next
      if (failed.current) {
        failed.current = false
        if (owner.current === recommendationSessionScope(getAccessToken())) setReadVersion(value => value + 1)
      }
    })
  }, [])
  const markFailed = useCallback(error => {
    if (!['ERR_CANCELED', 'SESSION_CHANGED'].includes(error?.code)) failed.current = isConnectionFailure(error)
  }, [])
  const markReady = useCallback(() => { failed.current = false }, [])
  return { readVersion, markFailed, markReady }
}
