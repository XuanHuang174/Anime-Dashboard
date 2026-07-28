import { useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens, saveTokens } from '../auth/tokenStorage.js'

gsap.registerPlugin(useGSAP)

function getErrorMessage(error) {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  const firstMessage = data && Object.values(data).flat().find(
    (message) => typeof message === 'string',
  )
  return firstMessage || '控制台认证失败，请检查账号与密码。'
}

function AdminLogin() {
  const pageRef = useRef(null)
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(
    new URLSearchParams(location.search).get('session') === 'expired'
      ? '管理员会话已过期，请重新验证。'
      : '',
  )
  const [isSubmitting, setIsSubmitting] = useState(false)

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.timeline()
      .from('.admin-login-panel', {
        scale: 0.72,
        y: -55,
        rotation: -2,
        autoAlpha: 0,
        duration: 0.75,
        ease: 'back.out(1.7)',
      })
      .from('.admin-login-piece', {
        y: 28,
        autoAlpha: 0,
        stagger: 0.07,
        duration: 0.36,
        ease: 'back.out(1.5)',
      }, '-=0.36')
  }, { scope: pageRef })

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setIsSubmitting(true)
    clearTokens()

    try {
      const tokenResponse = await api.post('token/', { username, password })
      saveTokens({
        access: tokenResponse.data.access,
        refresh: tokenResponse.data.refresh,
      })
      await api.get('admin/session/')
      navigate('/admin-dashboard', { replace: true })
    } catch (requestError) {
      clearTokens()
      if (requestError.response?.status === 403) {
        setError('该账号没有超级管理员权限，无法进入控制室。')
      } else {
        setError(getErrorMessage(requestError))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main
      ref={pageRef}
      className="admin-control-grid relative flex min-h-screen items-center justify-center overflow-hidden bg-zinc-900 px-5 py-8 text-white"
    >
      <div className="pointer-events-none absolute left-[7%] top-[12%] h-24 w-24 rotate-12 border-[12px] border-[#ffdf3e]" />
      <div className="pointer-events-none absolute bottom-[8%] right-[8%] h-28 w-28 rounded-full border-[14px] border-[#c7ff4a]" />

      <section className="admin-login-panel relative z-10 w-full max-w-xl border-4 border-black bg-zinc-800 p-5 shadow-[14px_14px_0_#c7ff4a] sm:p-8">
        <div className="admin-login-piece mb-6 flex items-center justify-between border-b-4 border-[#c7ff4a] pb-4">
          <div className="flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center border-4 border-black bg-[#ffdf3e] text-xl text-black shadow-[4px_4px_0_#c7ff4a]">
              <i className="fa-solid fa-shield-halved" />
            </span>
            <div>
              <span className="block text-[10px] font-black uppercase tracking-[0.32em] text-[#c7ff4a]">
                Restricted Area
              </span>
              <h1 className="text-2xl font-black tracking-tight sm:text-3xl">超级管理员认证</h1>
            </div>
          </div>
          <span className="hidden border-2 border-black bg-[#ff5b68] px-2 py-1 text-[10px] font-black text-black sm:block">
            STAFF ONLY
          </span>
        </div>

        <p className="admin-login-piece mb-7 border-l-8 border-[#ffdf3e] pl-4 text-sm font-bold leading-6 text-zinc-200">
          此入口仅用于精选专栏审核与系统管理，普通手账账号无法进入。
        </p>

        {error && (
          <div role="alert" className="admin-login-piece mb-5 border-4 border-black bg-[#ff5b68] px-4 py-3 font-black text-black shadow-[5px_5px_0_#ffdf3e]">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <label className="admin-login-piece block">
            <span className="mb-2 block text-xs font-black uppercase tracking-[0.18em] text-[#c7ff4a]">
              管理员账号
            </span>
            <div className="flex border-4 border-black bg-white text-black shadow-[6px_6px_0_#ffdf3e] focus-within:translate-x-1 focus-within:translate-y-1 focus-within:bg-[#efffc9] focus-within:shadow-none">
              <span className="flex w-14 items-center justify-center border-r-4 border-black bg-[#c7ff4a]">
                <i className="fa-solid fa-user-lock" />
              </span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                required
                className="min-w-0 flex-1 bg-transparent px-4 py-3 font-black outline-none"
                placeholder="输入超级管理员账号"
              />
            </div>
          </label>

          <label className="admin-login-piece block">
            <span className="mb-2 block text-xs font-black uppercase tracking-[0.18em] text-[#c7ff4a]">
              安全口令
            </span>
            <div className="flex border-4 border-black bg-white text-black shadow-[6px_6px_0_#ff5b68] focus-within:translate-x-1 focus-within:translate-y-1 focus-within:bg-[#fff4ad] focus-within:shadow-none">
              <span className="flex w-14 items-center justify-center border-r-4 border-black bg-[#ffdf3e]">
                <i className="fa-solid fa-key" />
              </span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
                className="min-w-0 flex-1 bg-transparent px-4 py-3 font-black outline-none"
                placeholder="输入管理员密码"
              />
            </div>
          </label>

          <button
            type="submit"
            disabled={isSubmitting}
            className="admin-login-piece flex w-full items-center justify-center gap-2 border-4 border-black bg-[#c7ff4a] px-5 py-4 text-lg font-black text-black shadow-[7px_7px_0_#ffdf3e] hover:bg-[#ffdf3e] active:translate-x-[7px] active:translate-y-[7px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-60"
          >
            <i className={`fa-solid ${isSubmitting ? 'fa-spinner animate-spin' : 'fa-terminal'}`} />
            {isSubmitting ? '正在核验权限...' : '进入管理控制室'}
          </button>
        </form>

        <Link
          to="/login"
          className="admin-login-piece mt-7 inline-flex items-center gap-2 border-2 border-black bg-white px-4 py-2 text-xs font-black text-black shadow-[4px_4px_0_#ff5b68] active:translate-x-1 active:translate-y-1 active:shadow-none"
        >
          <i className="fa-solid fa-arrow-left" />
          返回普通用户登录
        </Link>
      </section>
    </main>
  )
}

export default AdminLogin
