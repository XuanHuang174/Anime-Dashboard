import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { saveTokens } from '../auth/tokenStorage.js'
import CustomCursor from '../components/CustomCursor.jsx'

function getApiError(error, fallback) {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') {
    const firstMessage = Object.values(data).flat().find((item) => typeof item === 'string')
    if (firstMessage) return firstMessage
  }
  return fallback
}

function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState(
    new URLSearchParams(location.search).get('mode') === 'reset' ? 'reset' : 'login',
  )
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(location.state?.message || '')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const switchMode = (nextMode) => {
    setMode(nextMode)
    setError('')
    setNotice('')
    setPassword('')
    setPasswordConfirm('')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')
    setNotice('')

    if (mode === 'register' && password !== passwordConfirm) {
      setError('两次输入的密码不一致。')
      return
    }

    setIsSubmitting(true)
    try {
      if (mode === 'reset') {
        await api.post('auth/password-reset/', {
          email: email.trim(),
        })
        setNotice('重置邮件已发送，请查收。')
        return
      }

      if (mode === 'register') {
        await api.post('auth/register/', {
          email: email.trim(),
          password,
          password_confirm: passwordConfirm,
        })
        setNotice('注册成功，请前往邮箱点击激活链接（10分钟有效）')
        setPassword('')
        setPasswordConfirm('')
        return
      }

      const response = await api.post('token/', {
        username: username.trim(),
        password,
      })
      saveTokens(response.data)
      navigate(location.state?.from?.pathname || '/dashboard', { replace: true })
    } catch (requestError) {
      const fallback = mode === 'register'
        ? '注册失败，请检查邮箱和密码后重试。'
        : mode === 'reset'
          ? '重置邮件发送失败，请稍后重试。'
          : '用户名、邮箱或密码错误，请重新输入。'
      setError(getApiError(requestError, fallback))
    } finally {
      setIsSubmitting(false)
    }
  }

  const isRegister = mode === 'register'
  const isReset = mode === 'reset'
  const inputClass = 'w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-100/75 py-3.5 pl-11 pr-4 font-medium text-slate-700 shadow-[inset_0_3px_8px_rgba(15,23,42,0.07)] outline-none transition-all placeholder:text-slate-400 hover:bg-slate-50 hover:shadow-[inset_0_2px_5px_rgba(15,23,42,0.05)] focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:bg-white focus:ring-4 focus:ring-indigo-100'

  return (
    <main className="relative min-h-screen overflow-hidden bg-slate-100 px-4 py-5 text-slate-800 sm:px-6 sm:py-8 lg:px-8">
      <CustomCursor />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_8%_12%,_rgba(244,114,182,0.18),_transparent_28%),radial-gradient(circle_at_92%_86%,_rgba(99,102,241,0.16),_transparent_30%)]" />
      <div className="pointer-events-none absolute -left-24 top-1/3 h-64 w-64 rounded-full border-[38px] border-white/55" />
      <div className="pointer-events-none absolute -right-20 top-10 h-56 w-56 rounded-full bg-purple-200/40 blur-3xl" />

      <div className="relative z-10 mx-auto max-w-6xl">
        <div className="mb-4 flex items-center justify-between px-1 sm:mb-6">
          <Link to="/" className="group inline-flex items-center gap-2 rounded-xl border border-white/80 bg-white/75 px-3.5 py-2 text-sm font-bold text-indigo-600 shadow-sm backdrop-blur-md transition-all hover:-translate-y-0.5 hover:text-pink-500 hover:shadow-md">
            <i className="fa-solid fa-arrow-left transition-transform group-hover:-translate-x-1" />
            返回公共番剧页
          </Link>
          <span className="hidden items-center gap-2 text-xs font-bold tracking-wider text-slate-400 sm:inline-flex">
            <i className="fa-solid fa-shield-heart text-pink-400" />
            私密、安全、只属于你的手账
          </span>
        </div>

        <section className="grid overflow-hidden rounded-[32px] border border-white/80 bg-white shadow-[0_24px_70px_rgba(79,70,229,0.16)] ring-1 ring-indigo-100/60 lg:grid-cols-[0.92fr_1.08fr]">
          <div className="relative overflow-hidden bg-gradient-to-br from-pink-400 via-purple-400 to-indigo-500 px-7 py-8 text-white sm:px-10 sm:py-10 lg:flex lg:min-h-[650px] lg:flex-col lg:justify-between lg:px-12 lg:py-12">
            <i className="fa-solid fa-star pointer-events-none absolute -right-12 -top-16 rotate-12 text-[15rem] text-white/10" />
            <div className="pointer-events-none absolute -bottom-28 -left-20 h-72 w-72 rounded-full border-[46px] border-white/10" />
            <div className="pointer-events-none absolute bottom-1/3 right-10 h-24 w-24 rounded-full bg-pink-200/20 blur-2xl" />

            <div className="relative z-10">
              <span className="inline-flex items-center rounded-full bg-white/20 px-3 py-1.5 text-xs font-bold tracking-wider shadow-sm ring-1 ring-white/25 backdrop-blur-sm">
                <i className="fa-solid fa-heart mr-2 animate-pulse text-pink-100" />
                个人手账 / 追番记录
              </span>
              <div className="mt-6 flex items-center gap-4">
                <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border-2 border-white/60 bg-white/20 text-2xl text-yellow-200 shadow-lg ring-2 ring-white/15 backdrop-blur-sm">
                  <i className="fa-solid fa-star" />
                </span>
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.22em] text-pink-100">Anime Journal</p>
                  <h2 className="mt-1 text-2xl font-black tracking-tight sm:text-3xl">你的私人番剧手账房</h2>
                </div>
              </div>
              <p className="mt-6 max-w-md text-sm font-medium leading-7 text-white/90 sm:text-base">
                收藏每一次心动，记录每一部看过的作品。评分、标签、长评与海报墙，都由你亲手布置。
              </p>
            </div>

            <div className="relative z-10 mt-8 grid grid-cols-3 gap-2.5 sm:gap-3 lg:mt-12">
              {[
                ['fa-layer-group', '多视图', '列表 / 海报墙'],
                ['fa-tags', '自由整理', '标签与筛选'],
                ['fa-pen-nib', '认真记录', '评分与长评'],
              ].map(([icon, title, subtitle]) => (
                <div key={title} className="rounded-2xl border border-white/20 bg-white/15 px-3 py-3.5 shadow-sm backdrop-blur-md transition-transform hover:-translate-y-1 sm:px-4 sm:py-4">
                  <i className={`fa-solid ${icon} text-lg text-yellow-200`} />
                  <p className="mt-2 text-xs font-black sm:text-sm">{title}</p>
                  <p className="mt-0.5 hidden text-[10px] font-medium text-white/70 sm:block">{subtitle}</p>
                </div>
              ))}
            </div>

            <p className="relative z-10 mt-7 hidden items-center gap-2 text-xs font-semibold text-white/65 lg:flex">
              <i className="fa-solid fa-sparkles text-yellow-200" />
              从今天开始，设计属于自己的追番档案
            </p>
          </div>

          <div className="relative flex items-center bg-gradient-to-br from-white via-white to-indigo-50/45 px-6 py-8 sm:px-10 sm:py-10 lg:px-14">
            <div className="mx-auto w-full max-w-md">
              <div className="mb-6 flex items-start gap-4">
                <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-pink-400 to-indigo-500 text-xl text-white shadow-lg shadow-indigo-300/50 ring-4 ring-indigo-50">
                  <i className={`fa-solid ${isReset ? 'fa-key' : isRegister ? 'fa-user-plus' : 'fa-book-open'}`} />
                </span>
                <div>
                  <h1 className="text-2xl font-black tracking-tight text-slate-900 sm:text-3xl">
                    {isReset ? '找回你的手账密码' : isRegister ? '创建你的专属手账' : '欢迎回到手账房'}
                  </h1>
                  <p className="mt-1.5 text-sm leading-6 text-slate-500">
                    {isReset ? '输入注册邮箱，我们会发送一封限时有效的重置邮件。' : isRegister ? '使用常用邮箱注册，激活后即可开始记录。' : '登录后继续书写只属于你的追番记录。'}
                  </p>
                </div>
              </div>

              {!isReset ? (
                <div className="mb-6 grid grid-cols-2 rounded-2xl border border-slate-200 bg-slate-100/80 p-1.5 shadow-inner" role="tablist" aria-label="账号操作">
                  {[
                    ['login', 'fa-right-to-bracket', '登录'],
                    ['register', 'fa-user-plus', '注册'],
                  ].map(([value, icon, label]) => (
                    <button
                      key={value}
                      type="button"
                      role="tab"
                      aria-selected={mode === value}
                      onClick={() => switchMode(value)}
                      className={`flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-black transition-all duration-300 ${mode === value ? 'bg-white text-indigo-600 shadow-md ring-1 ring-slate-200' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                    >
                      <i className={`fa-solid ${icon}`} />
                      {label}
                    </button>
                  ))}
                </div>
              ) : (
                <button type="button" onClick={() => switchMode('login')} className="mb-6 inline-flex items-center gap-2 text-sm font-bold text-indigo-600 transition-colors hover:text-pink-500">
                  <i className="fa-solid fa-arrow-left" />
                  返回登录
                </button>
              )}

              <form className="space-y-5" onSubmit={handleSubmit}>
            {isRegister || isReset ? (
              <label className="block">
                <span className="mb-2 block text-sm font-bold text-slate-700">邮箱</span>
                <div className="relative">
                  <i className="fa-solid fa-envelope absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className={inputClass} placeholder={isReset ? '请输入注册时使用的邮箱' : '请输入 QQ 邮箱（其他邮箱暂不支持）'} />
                </div>
              </label>
            ) : (
              <label className="block">
                <span className="mb-2 block text-sm font-bold text-slate-700">用户名或邮箱</span>
                <div className="relative">
                  <i className="fa-solid fa-user absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input required autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} className={inputClass} placeholder="请输入用户名或注册邮箱" />
                </div>
              </label>
            )}

            {!isReset && <label className="block">
              <span className="mb-2 block text-sm font-bold text-slate-700">密码</span>
              <div className="relative">
                <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                <input required type="password" autoComplete={isRegister ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} className={inputClass} placeholder={isRegister ? '请设置安全密码' : '请输入密码'} />
              </div>
            </label>}

            {mode === 'login' && (
              <div className="-mt-2 flex justify-end">
                <button type="button" onClick={() => switchMode('reset')} className="text-sm font-bold text-indigo-600 transition-colors hover:text-pink-500 hover:underline">
                  忘记密码？
                </button>
              </div>
            )}

            {isRegister && (
              <label className="block">
                <span className="mb-2 block text-sm font-bold text-slate-700">确认密码</span>
                <div className="relative">
                  <i className="fa-solid fa-shield-halved absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input required type="password" autoComplete="new-password" value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} className={inputClass} placeholder="请再次输入密码" />
                </div>
              </label>
            )}

            {error && <p role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-600 shadow-inner"><i className="fa-solid fa-circle-exclamation mr-2" />{error}</p>}
            {notice && <p role="status" className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm font-bold leading-6 text-emerald-600 shadow-inner"><i className="fa-solid fa-circle-check mr-2" />{notice}</p>}

            {isReset && notice && (
              <button type="button" onClick={() => switchMode('login')} className="flex w-full items-center justify-center gap-2 rounded-xl border border-indigo-100 bg-indigo-50 px-5 py-3 font-bold text-indigo-600 shadow-inner transition-all hover:bg-indigo-100">
                <i className="fa-solid fa-arrow-left" />
                返回登录
              </button>
            )}

            <button type="submit" disabled={isSubmitting} className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 px-5 py-3.5 font-bold text-white shadow-lg shadow-indigo-300/40 transition-all hover:-translate-y-0.5 hover:shadow-xl active:translate-y-[2px] active:border-b disabled:cursor-not-allowed disabled:opacity-60">
              <span className="absolute -left-1/2 top-0 h-full w-1/3 -skew-x-12 bg-gradient-to-r from-transparent via-white/35 to-transparent transition-transform duration-700 group-hover:translate-x-[500%]" />
              <span className="relative inline-flex items-center gap-2">
              {isSubmitting ? <><i className="fa-solid fa-spinner animate-spin" />{isReset ? '正在发送...' : isRegister ? '正在注册...' : '正在登录...'}</> : <><i className={`fa-solid ${isReset || isRegister ? 'fa-paper-plane' : 'fa-right-to-bracket'}`} />{isReset ? '发送重置邮件' : isRegister ? '注册并发送激活邮件' : '进入我的手账房'}</>}
              </span>
            </button>
              </form>

              <div className="mt-6 flex items-center justify-center gap-2 border-t border-slate-100 pt-5 text-[11px] font-medium text-slate-400">
                <i className="fa-solid fa-lock text-emerald-400" />
                登录凭据将通过安全连接传输
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}

export default LoginPage
