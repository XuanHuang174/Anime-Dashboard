import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/api.js'

function getApiError(error, fallback) {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') {
    const firstMessage = Object.values(data).flat().find((item) => typeof item === 'string')
    if (firstMessage) return firstMessage
  }
  return fallback
}

function ResetPasswordPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const redirectTimer = useRef(null)
  const [newPassword, setNewPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const uid = searchParams.get('uid')
  const token = searchParams.get('token')
  const linkIsInvalid = !uid || !token

  useEffect(() => () => {
    if (redirectTimer.current) window.clearTimeout(redirectTimer.current)
  }, [])

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')
    setNotice('')

    if (linkIsInvalid) {
      setError('重置链接缺少必要参数，请重新打开邮件中的完整链接。')
      return
    }
    if (newPassword !== passwordConfirm) {
      setError('两次输入的密码不一致。')
      return
    }

    setIsSubmitting(true)
    try {
      const response = await api.post('auth/password-reset/confirm/', {
        uid,
        token,
        new_password: newPassword,
      })
      setNotice(response.data?.detail || '密码修改成功，请重新登录。')
      setNewPassword('')
      setPasswordConfirm('')
      redirectTimer.current = window.setTimeout(() => {
        navigate('/login', {
          replace: true,
          state: { message: '密码修改成功，请使用新密码重新登录。' },
        })
      }, 2200)
    } catch (requestError) {
      setError(getApiError(requestError, '密码修改失败，链接可能无效或已经过期。'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const inputClass = 'w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 py-3.5 pl-11 pr-4 text-slate-700 shadow-[inset_0_3px_8px_rgba(15,23,42,0.06)] outline-none transition-all focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:bg-white focus:ring-4 focus:ring-indigo-100'

  return (
    <main className="relative min-h-screen overflow-hidden bg-slate-950 px-4 py-12 text-slate-800">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(236,72,153,0.3),_transparent_35%),radial-gradient(circle_at_bottom_right,_rgba(99,102,241,0.35),_transparent_40%)]" />
      <div className="absolute left-1/2 top-16 h-72 w-72 -translate-x-1/2 rounded-full bg-purple-500/20 blur-3xl" />

      <section className="relative z-10 mx-auto flex min-h-[calc(100vh-6rem)] max-w-md items-center">
        <div className="w-full rounded-3xl border border-white/30 bg-white/90 p-7 shadow-2xl shadow-indigo-950/40 backdrop-blur-xl sm:p-9">
          <Link to="/login" replace className="mb-7 inline-flex items-center gap-2 text-sm font-semibold text-indigo-600 transition-colors hover:text-pink-500">
            <i className="fa-solid fa-arrow-left" />
            返回登录
          </Link>

          <div className="mb-7">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-pink-400 to-indigo-500 text-xl text-white shadow-lg shadow-indigo-300/50">
              <i className="fa-solid fa-key" />
            </span>
            <h1 className="mt-5 text-3xl font-black tracking-tight text-slate-900">设置新密码</h1>
            <p className="mt-2 text-sm leading-6 text-slate-500">请为你的番剧手账设置一个安全且容易记住的新密码。</p>
          </div>

          {linkIsInvalid ? (
            <div>
              <p role="alert" className="rounded-2xl border border-rose-100 bg-rose-50 px-5 py-4 text-sm font-medium leading-6 text-rose-600 shadow-inner">
                <i className="fa-solid fa-link-slash mr-2" />
                重置链接不完整，请重新打开邮件中的完整链接。
              </p>
              <Link to="/login?mode=reset" replace className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 px-5 py-3.5 font-bold text-white shadow-lg shadow-indigo-300/40 transition-all hover:-translate-y-0.5 hover:shadow-xl active:translate-y-[2px] active:border-b">
                <i className="fa-solid fa-envelope" />
                重新申请重置邮件
              </Link>
            </div>
          ) : (
            <form className="space-y-5" onSubmit={handleSubmit}>
              <label className="block">
                <span className="mb-2 block text-sm font-bold text-slate-700">新密码</span>
                <div className="relative">
                  <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input required minLength={8} type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className={inputClass} placeholder="请输入新密码" />
                </div>
              </label>

              <label className="block">
                <span className="mb-2 block text-sm font-bold text-slate-700">确认新密码</span>
                <div className="relative">
                  <i className="fa-solid fa-shield-halved absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input required minLength={8} type="password" autoComplete="new-password" value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} className={inputClass} placeholder="请再次输入新密码" />
                </div>
              </label>

              {error && <p role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-600 shadow-inner"><i className="fa-solid fa-circle-exclamation mr-2" />{error}</p>}
              {notice && <p role="status" className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm font-bold leading-6 text-emerald-600 shadow-inner"><i className="fa-solid fa-circle-check mr-2" />{notice}<span className="mt-1 block text-xs">即将自动返回登录页面</span></p>}

              <button type="submit" disabled={isSubmitting || Boolean(notice)} className="flex w-full items-center justify-center gap-2 rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 px-5 py-3.5 font-bold text-white shadow-lg shadow-indigo-300/40 transition-all hover:-translate-y-0.5 hover:shadow-xl active:translate-y-[2px] active:border-b disabled:cursor-not-allowed disabled:opacity-60">
                {isSubmitting ? <><i className="fa-solid fa-spinner animate-spin" />正在修改...</> : <><i className="fa-solid fa-shield-check" />确认修改密码</>}
              </button>
            </form>
          )}
        </div>
      </section>
    </main>
  )
}

export default ResetPasswordPage
