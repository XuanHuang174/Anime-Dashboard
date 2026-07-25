import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/api.js'
import CustomCursor from '../components/CustomCursor.jsx'

function ActivatePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState('checking')
  const [message, setMessage] = useState('正在验证激活链接，请稍候...')

  useEffect(() => {
    const uid = searchParams.get('uid')
    const token = searchParams.get('token')
    const controller = new AbortController()
    let redirectTimer

    if (!uid || !token) {
      setStatus('error')
      setMessage('激活链接缺少必要参数，请重新打开邮件中的完整链接。')
      return () => controller.abort()
    }

    const verifyEmail = async () => {
      try {
        const response = await api.post(
          'auth/verify-email/',
          { uid, token },
          { signal: controller.signal },
        )
        setStatus('success')
        setMessage(response.data?.detail || '账号激活成功，现在可以登录。')
        redirectTimer = window.setTimeout(() => {
          navigate('/login', {
            replace: true,
            state: { message: '账号激活成功，请使用邮箱和密码登录。' },
          })
        }, 2200)
      } catch (error) {
        if (error.code === 'ERR_CANCELED') return
        setStatus('error')
        setMessage(error.response?.data?.detail || '激活失败，链接可能无效或已经过期。')
      }
    }

    verifyEmail()
    return () => {
      controller.abort()
      if (redirectTimer) window.clearTimeout(redirectTimer)
    }
  }, [navigate, searchParams])

  const appearance = {
    checking: { icon: 'fa-spinner animate-spin', color: 'text-indigo-500', panel: 'from-indigo-50 to-purple-50' },
    success: { icon: 'fa-circle-check', color: 'text-emerald-500', panel: 'from-emerald-50 to-indigo-50' },
    error: { icon: 'fa-circle-xmark', color: 'text-rose-500', panel: 'from-rose-50 to-pink-50' },
  }[status]

  return (
    <main className="relative min-h-screen overflow-hidden bg-slate-950 px-4 py-12 text-slate-800">
      <CustomCursor />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(236,72,153,0.3),_transparent_35%),radial-gradient(circle_at_bottom_right,_rgba(99,102,241,0.35),_transparent_40%)]" />
      <div className="absolute left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-indigo-500/20 blur-3xl" />

      <section className="relative z-10 mx-auto flex min-h-[calc(100vh-6rem)] max-w-md items-center">
        <div className="w-full rounded-3xl border border-white/30 bg-white/90 p-8 text-center shadow-2xl shadow-indigo-950/40 backdrop-blur-xl sm:p-10">
          <div className={`mx-auto flex h-24 w-24 items-center justify-center rounded-3xl border-4 border-white bg-gradient-to-br ${appearance.panel} text-4xl shadow-xl shadow-indigo-200/40 ring-1 ring-slate-100 ${appearance.color}`}>
            <i className={`fa-solid ${appearance.icon}`} />
          </div>

          <h1 className="mt-7 text-3xl font-black tracking-tight text-slate-900">
            {status === 'checking' && '正在激活账号'}
            {status === 'success' && '激活成功！'}
            {status === 'error' && '激活未完成'}
          </h1>
          <p role={status === 'error' ? 'alert' : 'status'} className="mt-3 rounded-2xl border border-slate-100 bg-slate-50/80 px-5 py-4 text-sm font-medium leading-7 text-slate-600 shadow-inner">
            {message}
          </p>

          {status === 'success' && <p className="mt-4 text-xs font-semibold text-emerald-600"><i className="fa-solid fa-clock mr-1.5" />即将自动返回登录页面</p>}
          {status === 'error' && (
            <Link to="/login" replace className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 to-indigo-500 px-5 py-3.5 font-bold text-white shadow-lg transition-all hover:-translate-y-0.5 hover:shadow-xl active:translate-y-[2px] active:border-b">
              <i className="fa-solid fa-arrow-left" />返回登录与注册
            </Link>
          )}
        </div>
      </section>
    </main>
  )
}

export default ActivatePage
