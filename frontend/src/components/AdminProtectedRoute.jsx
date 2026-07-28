import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens, getAccessToken } from '../auth/tokenStorage.js'

function AdminProtectedRoute({ children }) {
  const location = useLocation()
  const [accessState, setAccessState] = useState(
    getAccessToken() ? 'checking' : 'denied',
  )

  useEffect(() => {
    if (!getAccessToken()) return undefined

    let cancelled = false
    api.get('admin/session/')
      .then(() => {
        if (!cancelled) setAccessState('allowed')
      })
      .catch(() => {
        clearTokens()
        if (!cancelled) setAccessState('denied')
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (accessState === 'denied') {
    return <Navigate to="/admin-login" replace state={{ from: location }} />
  }

  if (accessState === 'checking') {
    return (
      <main className="admin-control-grid flex min-h-screen items-center justify-center bg-zinc-900 px-6 text-white">
        <div className="border-4 border-black bg-[#c7ff4a] px-8 py-6 text-center text-black shadow-[10px_10px_0_#ffdf3e]">
          <i className="fa-solid fa-satellite-dish mb-3 block animate-pulse text-3xl" />
          <p className="text-sm font-black uppercase tracking-[0.24em]">正在验证管理员权限</p>
        </div>
      </main>
    )
  }

  return children
}

export default AdminProtectedRoute
