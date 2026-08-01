import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api/api.js'

gsap.registerPlugin(useGSAP)

const COLORS = {
  '--cream': '#fff5ee',
  '--coral': '#ff6b6b',
  '--teal': '#4ecdc4',
  '--yellow': '#ffe66d',
  '--ink': '#111111',
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

function ResetPasswordPage() {
  const pageRef = useRef(null)
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

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timeline = gsap.timeline()
    timeline
      .from('.reset-card', {
        scale: 0.55,
        y: -70,
        rotation: 4,
        opacity: 0,
        duration: 0.75,
        ease: 'back.out(1.8)',
      })
      .from('.reset-piece', {
        y: 38,
        opacity: 0,
        stagger: 0.07,
        duration: 0.4,
        ease: 'back.out(1.5)',
      }, '-=0.35')
  }, { scope: pageRef })

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

  const inputClass = 'w-full border-4 border-black bg-white py-3.5 pl-12 pr-4 font-black text-black outline-none shadow-[5px_5px_0_0_var(--teal)] placeholder:text-black/40 focus:translate-x-[3px] focus:translate-y-[3px] focus:bg-[var(--yellow)] focus:shadow-[2px_2px_0_0_var(--coral)]'
  const pressableClass = 'border-4 border-black shadow-[7px_7px_0_0_var(--ink)] transition-none hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-[5px_5px_0_0_var(--ink)] active:translate-x-[7px] active:translate-y-[7px] active:shadow-none'

  return (
    <main ref={pageRef} style={COLORS} className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-[var(--cream)] p-5 text-[var(--ink)]">
      <div className="pointer-events-none absolute inset-0 opacity-30" style={{ backgroundImage: 'linear-gradient(#111 1px, transparent 1px), linear-gradient(90deg, #111 1px, transparent 1px)', backgroundSize: '28px 28px' }} />
      <div className="pointer-events-none absolute -left-12 bottom-16 h-36 w-36 rotate-12 border-4 border-black bg-[var(--yellow)] shadow-[9px_9px_0_var(--coral)]" />
      <div className="pointer-events-none absolute -right-10 top-16 h-36 w-36 rounded-full border-[15px] border-black bg-[var(--teal)] shadow-[8px_8px_0_var(--coral)]" />

      <section className="reset-card relative z-10 grid w-full max-w-4xl border-4 border-black bg-white shadow-[12px_12px_0_0_var(--ink)] md:grid-cols-[0.8fr_1.2fr]">
        <aside className="relative overflow-hidden border-b-4 border-black bg-[var(--coral)] p-7 md:border-b-0 md:border-r-4 md:p-10">
          <span className="reset-piece inline-block -rotate-2 border-2 border-black bg-[var(--yellow)] px-3 py-1.5 text-[10px] font-black tracking-[0.2em] shadow-[4px_4px_0_#111]">PASSWORD RESCUE</span>
          <span className="reset-piece mt-9 flex h-24 w-24 rotate-3 items-center justify-center border-4 border-black bg-[var(--teal)] text-4xl shadow-[7px_7px_0_#111]">
            <i className="fa-solid fa-key" />
          </span>
          <h1 className="reset-piece mt-8 text-4xl font-black uppercase leading-none">给手账房<br />换把新锁！</h1>
          <p className="reset-piece mt-6 border-l-8 border-black bg-white px-4 py-3 text-sm font-black leading-6">设置一组安全的新密码，然后重新进入你的动画宇宙。</p>
          <div className="pointer-events-none absolute -bottom-8 -right-6 h-24 w-24 rotate-12 border-4 border-black bg-[var(--yellow)]" />
        </aside>

        <div className="p-7 md:p-10">
          <Link to="/login" replace className={`reset-piece inline-flex items-center gap-2 bg-white px-4 py-2 text-sm font-black ${pressableClass}`}>
            <i className="fa-solid fa-arrow-left" />返回登录
          </Link>

          <div className="reset-piece mt-8 border-b-4 border-black pb-5">
            <span className="inline-block border-2 border-black bg-[var(--teal)] px-2 py-1 text-[10px] font-black tracking-[0.18em]">SECURITY CHECKPOINT</span>
            <h2 className="mt-3 text-3xl font-black">设置新密码</h2>
          </div>

          {linkIsInvalid ? (
            <div className="reset-piece mt-7">
              <p role="alert" className="border-4 border-black bg-[var(--coral)] px-5 py-4 text-sm font-black leading-6 shadow-[6px_6px_0_#111]">
                <i className="fa-solid fa-link-slash mr-2" />重置链接不完整，请重新打开邮件中的完整链接。
              </p>
              <Link to="/login?mode=reset" replace className={`mt-6 flex w-full items-center justify-center gap-2 bg-[var(--yellow)] px-5 py-3.5 font-black ${pressableClass}`}>
                <i className="fa-solid fa-envelope" />重新申请重置邮件
              </Link>
            </div>
          ) : notice ? (
            <div className="reset-piece mt-7 border-4 border-black bg-[var(--teal)] p-6 shadow-[8px_8px_0_0_var(--coral)]">
              <span className="flex h-16 w-16 -rotate-3 items-center justify-center border-4 border-black bg-[var(--yellow)] text-2xl shadow-[4px_4px_0_#111]">
                <i className="fa-solid fa-shield-check" />
              </span>
              <h3 className="mt-5 text-2xl font-black">新密码设置成功！</h3>
              <p role="status" className="mt-3 border-2 border-black bg-white px-4 py-3 text-sm font-black leading-6">{notice}</p>
              <p className="mt-4 text-xs font-black"><i className="fa-solid fa-clock mr-2" />即将自动返回登录页面</p>
            </div>
          ) : (
            <form className="reset-piece mt-7 space-y-5" onSubmit={handleSubmit}>
              <label className="block">
                <span className="mb-2 inline-block -rotate-1 bg-black px-2 py-1 text-xs font-black text-white">新密码 NEW PASSWORD</span>
                <div className="relative">
                  <i className="fa-solid fa-lock absolute left-4 top-1/2 z-10 -translate-y-1/2" />
                  <input required minLength={8} type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className={inputClass} placeholder="请输入新密码" />
                </div>
              </label>

              <label className="block">
                <span className="mb-2 inline-block rotate-1 bg-black px-2 py-1 text-xs font-black text-white">确认密码 REPEAT</span>
                <div className="relative">
                  <i className="fa-solid fa-shield-halved absolute left-4 top-1/2 z-10 -translate-y-1/2" />
                  <input required minLength={8} type="password" autoComplete="new-password" value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} className={inputClass} placeholder="请再次输入新密码" />
                </div>
              </label>

              {error && <p role="alert" className="border-4 border-black bg-[var(--coral)] px-4 py-3 text-sm font-black shadow-[5px_5px_0_#111]"><i className="fa-solid fa-circle-exclamation mr-2" />{error}</p>}

              <button type="submit" disabled={isSubmitting} className={`flex w-full items-center justify-center gap-2 bg-[var(--yellow)] px-5 py-4 font-black disabled:cursor-not-allowed disabled:opacity-60 ${pressableClass}`}>
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
