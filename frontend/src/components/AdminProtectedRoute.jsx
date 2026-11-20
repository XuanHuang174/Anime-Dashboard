import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens, getAccessToken } from '../auth/tokenStorage.js'

function AdminProtectedRoute({ children }) {
  const location = useLocation()
  const [accessState, setAccessState] = useState(
    getAccessToken() ? 'checking' : 'denied',
  )
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!getAccessToken()) return undefined
    setAccessState('checking')

    let cancelled = false
    api.get('admin/session/')
      .then(() => {
        if (!cancelled) setAccessState('allowed')
      })
      .catch((error) => {
        if (cancelled) return
        if (error.response?.status === 401) clearTokens()
        setAccessState([401, 403].includes(error.response?.status) ? 'denied' : 'unavailable')
      })

    return () => {
      cancelled = true
    }
  }, [attempt])

  if (accessState === 'denied') {
    return <Navigate to="/admin-login" replace state={{ from: location }} />
  }

  if (accessState === 'checking' || accessState === 'unavailable') {
    return (
      <main className="admin-control-grid flex min-h-screen items-center justify-center bg-zinc-900 px-6 text-white">
        <div className="border-4 border-black bg-[#c7ff4a] px-8 py-6 text-center text-black shadow-[10px_10px_0_#ffdf3e]">
          <i className="fa-solid fa-satellite-dish mb-3 block animate-pulse text-3xl" />
          <p className="text-sm font-black uppercase tracking-[0.24em]">{accessState === 'checking' ? '正在验证管理员权限' : '服务暂时不可用，登录状态已保留'}</p>
          {accessState === 'unavailable' && <button type="button" className="mt-4 border-2 border-black bg-white px-4 py-2 font-bold" onClick={() => setAttempt(value => value + 1)}>重新验证</button>}
        </div>
      </main>
    )
  }

  return children
}

export default AdminProtectedRoute
