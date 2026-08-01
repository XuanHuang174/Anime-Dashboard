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

const STATUS_STYLE = {
  checking: {
    icon: 'fa-spinner animate-spin',
    label: 'VERIFYING SIGNAL',
    title: '正在激活账号',
    color: 'bg-[var(--yellow)]',
  },
  success: {
    icon: 'fa-circle-check',
    label: 'ACCESS GRANTED',
    title: '激活成功！',
    color: 'bg-[var(--teal)]',
  },
  error: {
    icon: 'fa-circle-xmark',
    label: 'SIGNAL LOST',
    title: '激活未完成',
    color: 'bg-[var(--coral)]',
  },
}

function ActivatePage() {
  const pageRef = useRef(null)
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

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.from('.activation-card', {
      scale: 0.55,
      y: -70,
      rotation: -4,
      opacity: 0,
      duration: 0.75,
      ease: 'back.out(1.8)',
    })
    gsap.from('.activation-piece', {
      y: 35,
      opacity: 0,
      stagger: 0.08,
      duration: 0.42,
      ease: 'back.out(1.5)',
      delay: 0.28,
    })
  }, { scope: pageRef })

  useGSAP(() => {
    gsap.fromTo(
      '.activation-status',
      { scale: 0.75, rotation: -8 },
      { scale: 1, rotation: 2, duration: 0.45, ease: 'back.out(2)' },
    )
  }, { scope: pageRef, dependencies: [status] })

  const appearance = STATUS_STYLE[status]

  return (
    <main ref={pageRef} style={COLORS} className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-[var(--cream)] p-5 text-[var(--ink)]">
      <div className="pointer-events-none absolute inset-0 opacity-30" style={{ backgroundImage: 'radial-gradient(#111 1.5px, transparent 1.5px)', backgroundSize: '20px 20px' }} />
      <div className="pointer-events-none absolute -left-16 top-20 h-40 w-40 rotate-12 border-4 border-black bg-[var(--coral)] shadow-[10px_10px_0_#111]" />
      <div className="pointer-events-none absolute -right-10 bottom-16 h-36 w-36 rounded-full border-[16px] border-black bg-[var(--teal)] shadow-[8px_8px_0_var(--coral)]" />

      <section className="activation-card relative z-10 w-full max-w-xl border-4 border-black bg-white p-6 shadow-[12px_12px_0_0_var(--ink)] sm:p-9">
        <div className="absolute left-0 top-0 flex h-3 w-full">
          <span className="w-1/3 bg-[var(--coral)]" />
          <span className="w-1/3 bg-[var(--yellow)]" />
          <span className="w-1/3 bg-[var(--teal)]" />
        </div>

        <div className="activation-piece mt-3 flex items-center justify-between gap-4 border-b-4 border-black pb-5">
          <span className="-rotate-2 border-2 border-black bg-black px-3 py-1.5 text-[10px] font-black tracking-[0.2em] text-white">ACCOUNT ACTIVATION</span>
          <span className="font-black">ANIME JOURNAL / 账户通行证</span>
        </div>

        <div className="activation-piece mt-8 text-center">
          <span className={`activation-status mx-auto flex h-24 w-24 items-center justify-center border-4 border-black text-4xl shadow-[7px_7px_0_0_var(--ink)] ${appearance.color}`}>
            <i className={`fa-solid ${appearance.icon}`} />
          </span>
          <span className="mt-7 inline-block border-2 border-black bg-[var(--yellow)] px-3 py-1 text-[10px] font-black tracking-[0.2em]">{appearance.label}</span>
          <h1 className="mt-4 text-4xl font-black uppercase leading-none">{appearance.title}</h1>
        </div>

        <p role={status === 'error' ? 'alert' : 'status'} className="activation-piece mt-6 border-4 border-black bg-[var(--cream)] px-5 py-4 text-sm font-black leading-7 shadow-[5px_5px_0_0_var(--teal)]">
          {message}
        </p>

        {status === 'success' && (
          <p className="activation-piece mt-5 border-l-8 border-[var(--coral)] bg-[var(--yellow)] px-4 py-3 text-sm font-black">
            <i className="fa-solid fa-clock mr-2" />即将自动返回登录页面
          </p>
        )}
        {status === 'error' && (
          <Link to="/login" replace className="activation-piece mt-6 flex w-full items-center justify-center gap-2 border-4 border-black bg-[var(--coral)] px-5 py-3.5 font-black shadow-[6px_6px_0_#111] active:translate-x-[6px] active:translate-y-[6px] active:shadow-none">
            <i className="fa-solid fa-arrow-left" />返回登录与注册
          </Link>
        )}
      </section>
    </main>
  )
}

export default ActivatePage
