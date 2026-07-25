import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { saveTokens } from '../auth/tokenStorage.js'
import CustomCursor from '../components/CustomCursor.jsx'

gsap.registerPlugin(useGSAP)

const MEMPHIS_COLORS = {
  '--memphis-cream': '#fff5ee',
  '--memphis-coral': '#ff6b6b',
  '--memphis-teal': '#4ecdc4',
  '--memphis-yellow': '#ffe66d',
  '--memphis-ink': '#111111',
  '--memphis-white': '#ffffff',
}

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
  const pageRef = useRef(null)
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

  useEffect(() => {
    const previousPaddingBottom = document.body.style.paddingBottom
    const previousOverflow = document.body.style.overflow
    document.body.style.paddingBottom = '0px'
    document.body.style.overflow = 'hidden'

    return () => {
      document.body.style.paddingBottom = previousPaddingBottom
      document.body.style.overflow = previousOverflow
    }
  }, [])

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const randomStartRotation = gsap.utils.random(-13, 13)
    const entrance = gsap.timeline({ defaults: { overwrite: 'auto' } })
    entrance
      .fromTo(
        '.memphis-login-card',
        {
          autoAlpha: 0,
          scale: 0.1,
          rotation: randomStartRotation,
          transformOrigin: '50% 50%',
        },
        {
          autoAlpha: 1,
          scale: 1,
          rotation: -0.45,
          duration: 1.15,
          ease: 'elastic.out(1, 0.3)',
        },
      )
      .from(
        '.login-piece',
        {
          autoAlpha: 0,
          y: -85,
          rotation: () => gsap.utils.random(-7, 7),
          duration: 0.55,
          stagger: 0.1,
          ease: 'back.out(1.9)',
        },
        '-=0.72',
      )

    gsap.to('.shape-triangle', {
      rotation: 360,
      duration: 5.5,
      repeat: -1,
      ease: 'none',
      transformOrigin: '50% 50%',
    })
    gsap.to('.shape-ring', {
      y: -42,
      rotation: -18,
      duration: 1.7,
      repeat: -1,
      yoyo: true,
      ease: 'bounce.out',
    })
    gsap.to('.shape-square', {
      x: 32,
      y: -18,
      rotation: 25,
      duration: 2.4,
      repeat: -1,
      yoyo: true,
      ease: 'elastic.inOut(1, 0.45)',
    })
    gsap.to('.shape-zigzag', {
      x: -24,
      rotation: 8,
      duration: 1.3,
      repeat: -1,
      yoyo: true,
      ease: 'steps(4)',
    })
  }, { scope: pageRef })

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.fromTo(
      '.mode-piece',
      { autoAlpha: 0, y: -34, scale: 0.88 },
      {
        autoAlpha: 1,
        y: 0,
        scale: 1,
        duration: 0.42,
        stagger: 0.07,
        ease: 'back.out(1.8)',
        overwrite: 'auto',
      },
    )
  }, { scope: pageRef, dependencies: [mode] })

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
        await api.post('auth/password-reset/', { email: email.trim() })
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
  const inputClass = 'w-full border-4 border-black bg-[var(--memphis-yellow)] px-12 py-3.5 text-base font-black text-black outline-none shadow-[5px_5px_0_0_var(--memphis-ink)] placeholder:font-bold placeholder:text-black/45 focus:translate-x-[3px] focus:translate-y-[3px] focus:bg-white focus:shadow-[2px_2px_0_0_var(--memphis-ink)]'
  const pressableClass = 'border-4 border-black shadow-[6px_6px_0_0_var(--memphis-ink)] transition-none hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-[4px_4px_0_0_var(--memphis-ink)] active:translate-x-[6px] active:translate-y-[6px] active:shadow-none'

  return (
    <main
      ref={pageRef}
      style={MEMPHIS_COLORS}
      className="relative h-[100dvh] overflow-hidden bg-[var(--memphis-cream)] px-3 py-3 text-[var(--memphis-ink)] sm:px-5 sm:py-4 lg:px-8 lg:py-5"
    >
      <CustomCursor />

      <div
        className="pointer-events-none absolute inset-0 opacity-35"
        style={{
          backgroundImage: 'radial-gradient(#111 1.7px, transparent 1.7px)',
          backgroundSize: '20px 20px',
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.08]"
        style={{
          backgroundImage: 'linear-gradient(#111 2px, transparent 2px), linear-gradient(90deg, #111 2px, transparent 2px)',
          backgroundSize: '64px 64px',
        }}
      />

      <svg className="shape-triangle pointer-events-none absolute -left-8 top-[14%] h-28 w-28 sm:left-[3%] sm:h-36 sm:w-36" viewBox="0 0 120 120" aria-hidden="true">
        <path d="M60 8 112 106H8Z" fill="var(--memphis-coral)" stroke="var(--memphis-ink)" strokeWidth="7" strokeLinejoin="round" />
        <path d="m52 42 25 25-33 13Z" fill="var(--memphis-yellow)" stroke="var(--memphis-ink)" strokeWidth="5" />
      </svg>

      <div className="shape-ring pointer-events-none absolute -right-12 top-[8%] h-32 w-32 rounded-full border-[18px] border-black bg-[var(--memphis-teal)] shadow-[7px_7px_0_0_var(--memphis-coral)] sm:right-[4%] sm:h-40 sm:w-40 sm:border-[22px]" />

      <div className="shape-square pointer-events-none absolute -bottom-10 left-[7%] h-28 w-28 rotate-12 border-[7px] border-black bg-[var(--memphis-yellow)] shadow-[8px_8px_0_0_var(--memphis-teal)] sm:h-36 sm:w-36">
        <div className="h-full w-full bg-[repeating-linear-gradient(45deg,transparent_0,transparent_12px,#111_12px,#111_16px)] opacity-25" />
      </div>

      <svg className="shape-zigzag pointer-events-none absolute bottom-[8%] right-[1%] hidden h-24 w-56 sm:block" viewBox="0 0 220 80" aria-hidden="true">
        <path d="m5 58 30-34 30 34 30-34 30 34 30-34 30 34 30-34" fill="none" stroke="var(--memphis-ink)" strokeWidth="18" strokeLinejoin="bevel" />
        <path d="m5 47 30-34 30 34 30-34 30 34 30-34 30 34 30-34" fill="none" stroke="var(--memphis-coral)" strokeWidth="10" strokeLinejoin="bevel" />
      </svg>

      <div className="relative z-10 mx-auto flex h-full max-w-6xl flex-col">
        <div className="mb-3 flex shrink-0 items-center justify-between gap-4">
          <Link
            to="/"
            className={`login-piece inline-flex items-center gap-2 bg-[var(--memphis-white)] px-4 py-2.5 text-sm font-black uppercase tracking-wide ${pressableClass}`}
          >
            <i className="fa-solid fa-arrow-left" />
            返回公共番剧页
          </Link>
          <div className="login-piece hidden -rotate-2 border-4 border-black bg-[var(--memphis-teal)] px-4 py-2 text-xs font-black uppercase tracking-[0.16em] shadow-[5px_5px_0_0_var(--memphis-ink)] sm:block">
            <i className="fa-solid fa-bolt mr-2" />
            PRIVATE ZONE · 私人手账
          </div>
        </div>

        <section className="memphis-login-card mb-3 mr-3 grid min-h-0 flex-1 border-[5px] border-black bg-white shadow-[10px_10px_0_0_var(--memphis-ink)] lg:grid-cols-[0.86fr_1.14fr]">
          <aside className="relative hidden min-h-0 overflow-hidden border-r-[5px] border-black bg-[var(--memphis-coral)] p-7 lg:flex xl:p-9">
            <div className="pointer-events-none absolute -right-9 top-8 h-28 w-28 rounded-full border-[15px] border-black/90 bg-[var(--memphis-yellow)]" />
            <div className="pointer-events-none absolute -bottom-10 -left-8 h-40 w-40 rotate-12 border-[6px] border-black bg-[var(--memphis-teal)]" />

            <div className="relative z-10 flex h-full flex-col">
              <div className="login-piece inline-flex w-fit rotate-2 items-center border-4 border-black bg-white px-3 py-2 text-xs font-black uppercase tracking-[0.18em] shadow-[5px_5px_0_0_var(--memphis-ink)]">
                <span className="mr-2 inline-block h-3 w-3 rounded-full border-2 border-black bg-[var(--memphis-yellow)]" />
                Anime Journal / 追番记录
              </div>

              <div className="login-piece mt-5">
                <div className="inline-flex -rotate-3 border-4 border-black bg-[var(--memphis-yellow)] px-3 py-1.5 text-sm font-black shadow-[5px_5px_0_0_var(--memphis-ink)]">
                  嘿！欢迎回来
                </div>
                <h2
                  className="mt-5 max-w-md text-4xl font-black uppercase leading-[0.95] tracking-[-0.05em] text-black lg:text-5xl xl:text-6xl"
                  style={{ fontFamily: '"Arial Black", Impact, sans-serif' }}
                >
                  把喜欢的
                  <span className="my-2 block w-fit -rotate-2 bg-black px-3 py-2 text-[var(--memphis-yellow)]">动画</span>
                  全部记下来!
                </h2>
                <p className="mt-4 max-w-sm border-l-[7px] border-black pl-4 text-sm font-black leading-6 xl:text-base xl:leading-7">
                  评分、标签、长评、海报墙——这里没有无聊的工作台，只有属于你的动画收藏宇宙。
                </p>
              </div>

              <div className="login-piece mt-auto grid grid-cols-3 gap-2 pt-5">
                {[
                  ['01', '多视图', 'fa-layer-group', 'bg-[var(--memphis-yellow)]'],
                  ['02', '自由筛选', 'fa-tags', 'bg-[var(--memphis-teal)]'],
                  ['03', '私人长评', 'fa-pen-nib', 'bg-white'],
                ].map(([number, label, icon, color], index) => (
                  <div
                    key={number}
                    className={`${color} border-4 border-black p-3 shadow-[4px_4px_0_0_var(--memphis-ink)] ${index === 1 ? 'translate-y-3 -rotate-2' : index === 2 ? 'rotate-2' : '-rotate-1'}`}
                  >
                    <span className="block text-[10px] font-black tracking-widest">{number}</span>
                    <i className={`fa-solid ${icon} my-2 text-xl`} />
                    <span className="block text-xs font-black sm:text-sm">{label}</span>
                  </div>
                ))}
              </div>
            </div>
          </aside>

          <div className="relative flex min-h-0 items-center overflow-hidden bg-white p-4 sm:p-6 lg:p-8 xl:p-10">
            <div className="pointer-events-none absolute right-4 top-4 grid grid-cols-4 gap-2 opacity-30">
              {Array.from({ length: 12 }, (_, index) => (
                <span key={index} className="h-2.5 w-2.5 rounded-full bg-black" />
              ))}
            </div>

            <div className="relative mx-auto w-full max-w-md">
              <header className="login-piece mb-4">
                <span className="inline-flex rotate-1 items-center border-[3px] border-black bg-[var(--memphis-teal)] px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.2em]">
                  {isReset ? 'PASSWORD RESCUE' : isRegister ? 'NEW PLAYER' : 'PLAYER ONE'}
                </span>
                <h1
                  className="mt-4 text-3xl font-black uppercase leading-none tracking-[-0.04em] sm:text-4xl"
                  style={{ fontFamily: '"Arial Black", Impact, sans-serif' }}
                >
                  {isReset ? '找回手账密码' : isRegister ? '创建专属手账' : '登录手账房'}
                </h1>
                <p className="mt-3 border-l-4 border-[var(--memphis-coral)] pl-3 text-sm font-bold leading-6">
                  {isReset
                    ? '留下邮箱，重置入口马上飞进你的收件箱。'
                    : isRegister
                      ? '注册、激活，然后开始疯狂收藏。'
                      : '输入暗号，继续整理你的动画宇宙。'}
                </p>
              </header>

              {!isReset ? (
                <div className="login-piece mb-4 grid grid-cols-2 gap-3" role="tablist" aria-label="账号操作">
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
                      className={`border-4 border-black px-4 py-2.5 text-sm font-black uppercase transition-none ${
                        mode === value
                          ? 'translate-x-[4px] translate-y-[4px] bg-[var(--memphis-coral)] shadow-none'
                          : 'bg-white shadow-[4px_4px_0_0_var(--memphis-ink)] hover:bg-[var(--memphis-yellow)]'
                      }`}
                    >
                      <i className={`fa-solid ${icon} mr-2`} />
                      {label}
                    </button>
                  ))}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => switchMode('login')}
                  className={`login-piece mb-4 bg-[var(--memphis-yellow)] px-4 py-2 text-sm font-black ${pressableClass}`}
                >
                  <i className="fa-solid fa-arrow-left mr-2" />
                  返回登录
                </button>
              )}

              <form className="space-y-4" onSubmit={handleSubmit}>
                {isRegister || isReset ? (
                  <label className="mode-piece block">
                    <span className="mb-2 inline-block -rotate-1 bg-black px-2 py-1 text-xs font-black text-white">邮箱 EMAIL</span>
                    <div className="relative">
                      <i className="fa-solid fa-envelope absolute left-4 top-1/2 z-10 -translate-y-1/2 text-black" />
                      <input
                        required
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        className={inputClass}
                        placeholder={isReset ? '请输入注册时使用的邮箱' : '请输入邮箱地址（支持 QQ 邮箱 / 网易邮箱）'}
                      />
                    </div>
                  </label>
                ) : (
                  <label className="mode-piece block">
                    <span className="mb-2 inline-block -rotate-1 bg-black px-2 py-1 text-xs font-black text-white">账号 ACCOUNT</span>
                    <div className="relative">
                      <i className="fa-solid fa-user absolute left-4 top-1/2 z-10 -translate-y-1/2 text-black" />
                      <input
                        required
                        autoComplete="username"
                        value={username}
                        onChange={(event) => setUsername(event.target.value)}
                        className={inputClass}
                        placeholder="请输入用户名或注册邮箱"
                      />
                    </div>
                  </label>
                )}

                {!isReset && (
                  <label className="mode-piece block">
                    <span className="mb-2 inline-block rotate-1 bg-black px-2 py-1 text-xs font-black text-white">密码 PASSWORD</span>
                    <div className="relative">
                      <i className="fa-solid fa-lock absolute left-4 top-1/2 z-10 -translate-y-1/2 text-black" />
                      <input
                        required
                        type="password"
                        autoComplete={isRegister ? 'new-password' : 'current-password'}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        className={inputClass}
                        placeholder={isRegister ? '请设置安全密码' : '请输入密码'}
                      />
                    </div>
                  </label>
                )}

                {mode === 'login' && (
                  <div className="mode-piece flex justify-end">
                    <button
                      type="button"
                      onClick={() => switchMode('reset')}
                      className="border-b-[3px] border-black text-sm font-black hover:bg-[var(--memphis-yellow)]"
                    >
                      忘记密码？→
                    </button>
                  </div>
                )}

                {isRegister && (
                  <label className="mode-piece block">
                    <span className="mb-2 inline-block -rotate-1 bg-black px-2 py-1 text-xs font-black text-white">再次确认 REPEAT</span>
                    <div className="relative">
                      <i className="fa-solid fa-shield-halved absolute left-4 top-1/2 z-10 -translate-y-1/2 text-black" />
                      <input
                        required
                        type="password"
                        autoComplete="new-password"
                        value={passwordConfirm}
                        onChange={(event) => setPasswordConfirm(event.target.value)}
                        className={inputClass}
                        placeholder="请再次输入密码"
                      />
                    </div>
                  </label>
                )}

                {error && (
                  <p role="alert" className="mode-piece border-4 border-black bg-[var(--memphis-coral)] px-4 py-3 text-sm font-black shadow-[5px_5px_0_0_var(--memphis-ink)]">
                    <i className="fa-solid fa-circle-exclamation mr-2" />
                    {error}
                  </p>
                )}

                {notice && (
                  <p role="status" className="mode-piece border-4 border-black bg-[var(--memphis-teal)] px-4 py-3 text-sm font-black leading-6 shadow-[5px_5px_0_0_var(--memphis-ink)]">
                    <i className="fa-solid fa-circle-check mr-2" />
                    {notice}
                  </p>
                )}

                {isReset && notice && (
                  <button
                    type="button"
                    onClick={() => switchMode('login')}
                    className={`mode-piece flex w-full items-center justify-center bg-white px-5 py-3 font-black ${pressableClass}`}
                  >
                    <i className="fa-solid fa-arrow-left" />
                    返回登录
                  </button>
                )}

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className={`mode-piece flex w-full items-center justify-center gap-2 bg-[var(--memphis-coral)] px-5 py-4 text-base font-black uppercase disabled:cursor-not-allowed disabled:opacity-60 ${pressableClass}`}
                >
                  {isSubmitting ? (
                    <>
                      <i className="fa-solid fa-spinner animate-spin" />
                      {isReset ? '正在发送...' : isRegister ? '正在注册...' : '正在登录...'}
                    </>
                  ) : (
                    <>
                      <i className={`fa-solid ${isReset || isRegister ? 'fa-paper-plane' : 'fa-right-to-bracket'}`} />
                      {isReset ? '发送重置邮件' : isRegister ? '注册并发送激活邮件' : '进入我的手账房'}
                    </>
                  )}
                </button>
              </form>

              <div className="login-piece mt-5 flex items-center justify-between border-t-4 border-black pt-3 text-[10px] font-black uppercase tracking-[0.12em]">
                <span><i className="fa-solid fa-lock mr-1.5" />安全连接</span>
                <span className="bg-[var(--memphis-yellow)] px-2 py-1">NO BORING UI</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}

export default LoginPage
