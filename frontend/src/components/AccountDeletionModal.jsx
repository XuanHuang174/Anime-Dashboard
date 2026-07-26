import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'

gsap.registerPlugin(useGSAP)

const CONFIRM_TEXT = '永久注销'

function getApiError(error) {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') {
    const firstMessage = Object.values(data).flat().find((item) => typeof item === 'string')
    if (firstMessage) return firstMessage
  }
  return '账户注销失败，请稍后重试。'
}

function AccountDeletionModal({ isOpen, profile, onClose, onDeleted }) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const [password, setPassword] = useState('')
  const [confirmText, setConfirmText] = useState('')
  const [error, setError] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)

  const { contextSafe } = useGSAP(() => {
    if (!isOpen || !overlayRef.current || !modalRef.current) return undefined

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([overlayRef.current, modalRef.current, '.account-delete-piece'], { clearProps: 'all' })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(overlayRef.current, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.14 })
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, scale: 0.72, y: -45, rotation: 2 },
        {
          autoAlpha: 1,
          scale: 1,
          y: 0,
          rotation: 0,
          duration: 0.68,
          ease: 'elastic.out(1, 0.62)',
          clearProps: 'transform,opacity,visibility',
        },
        0,
      )
      .fromTo(
        '.account-delete-piece',
        { autoAlpha: 0, y: -14, scale: 0.95 },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.32,
          stagger: 0.05,
          ease: 'back.out(1.7)',
          clearProps: 'transform,opacity,visibility',
        },
        0.18,
      )

    return () => timeline.kill()
  }, {
    scope: overlayRef,
    dependencies: [isOpen],
    revertOnUpdate: true,
  })

  const playExit = contextSafe((afterExit = onClose) => {
    if (!modalRef.current || !overlayRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      return
    }
    gsap.killTweensOf([modalRef.current, overlayRef.current])
    gsap.timeline({ onComplete: afterExit })
      .to(modalRef.current, {
        y: 50,
        scale: 0.82,
        rotation: -2,
        autoAlpha: 0,
        duration: 0.24,
        ease: 'power3.in',
      })
      .to(overlayRef.current, { autoAlpha: 0, duration: 0.12 }, '-=0.08')
  })

  const handleClose = () => {
    if (!isDeleting) playExit(onClose)
  }
  closeHandlerRef.current = handleClose

  useEffect(() => {
    if (!isOpen) return undefined
    setPassword('')
    setConfirmText('')
    setError('')
    const previousOverflow = document.body.style.overflow
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') closeHandlerRef.current?.()
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  if (!isOpen) return null

  const canDelete = password.length > 0 && confirmText === CONFIRM_TEXT

  const handleDelete = async (event) => {
    event.preventDefault()
    if (!canDelete) return
    setError('')
    setIsDeleting(true)
    try {
      await api.delete('user/account/', {
        data: { password, confirmation: 'DELETE' },
      })
      playExit(onDeleted)
    } catch (requestError) {
      setError(getApiError(requestError))
      setIsDeleting(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[130] flex items-center justify-center overflow-y-auto bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="accountDeletionTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section
        ref={modalRef}
        className="relative my-auto max-h-[calc(100vh-2rem)] w-full max-w-md overflow-y-auto border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#ff6b6b]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.08]" style={{ backgroundImage: 'linear-gradient(#111 1px, transparent 1px), linear-gradient(90deg, #111 1px, transparent 1px)', backgroundSize: '18px 18px' }} aria-hidden="true" />

        <form onSubmit={handleDelete} className="relative px-5 py-6 sm:px-6">
          <div className="account-delete-piece flex items-start justify-between gap-4">
            <span className="flex h-16 w-16 items-center justify-center border-4 border-black bg-[#ff6b6b] text-2xl shadow-[5px_5px_0_#111]">
              <i className="fa-solid fa-user-slash" />
            </span>
            <button
              type="button"
              onClick={handleClose}
              disabled={isDeleting}
              className="flex h-10 w-10 items-center justify-center border-[3px] border-black bg-white text-lg shadow-[4px_4px_0_#111] transition-none hover:bg-[#ff6b6b] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
              aria-label="关闭"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </div>

          <div className="account-delete-piece mt-5">
            <p className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">Danger Zone</p>
            <h2 id="accountDeletionTitle" className="mt-3 text-2xl font-black">永久注销账户</h2>
            <p className="mt-2 text-sm font-bold leading-6">
              这会彻底删除 <strong className="bg-black px-1.5 py-0.5 text-[#ff8fab]">{profile?.nickname || profile?.username || '当前用户'}</strong> 的头像、追番记录、筛选配置和个性化方案，且无法恢复。
            </p>
          </div>

          <div className="account-delete-piece mt-5 border-[3px] border-black bg-[#ffe66d] p-4 text-sm font-black leading-7 shadow-[5px_5px_0_#ff6b6b]">
            <i className="fa-solid fa-triangle-exclamation mr-2 text-lg" />
            注销后该邮箱可以重新注册，但新账号将从空白手账重新开始。
          </div>

          <label className="account-delete-piece mt-5 block">
            <span className="mb-2 block text-sm font-black">当前密码</span>
            <div className="relative">
              <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-black" />
              <input
                required
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full border-[3px] border-black bg-white py-3 pl-11 pr-4 text-sm font-bold text-black shadow-[4px_4px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none"
                placeholder="请输入当前登录密码"
              />
            </div>
          </label>

          <label className="account-delete-piece mt-5 block">
            <span className="mb-2 block text-sm font-black">输入“{CONFIRM_TEXT}”以确认</span>
            <input
              required
              value={confirmText}
              onChange={(event) => setConfirmText(event.target.value)}
              className="w-full border-[3px] border-black bg-white px-4 py-3 text-sm font-bold text-black shadow-[4px_4px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#ffb0c8] focus:shadow-none"
              placeholder={CONFIRM_TEXT}
            />
          </label>

          {error && (
            <p role="alert" className="account-delete-piece mt-5 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]">
              <i className="fa-solid fa-circle-exclamation mr-2" />{error}
            </p>
          )}

          <div className="account-delete-piece mt-6 grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={handleClose}
              disabled={isDeleting}
              className="border-[3px] border-black bg-white px-4 py-3 text-sm font-black shadow-[5px_5px_0_#111] transition-none hover:bg-[#f4f1ea] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-50"
            >
              保留账户
            </button>
            <button
              type="submit"
              disabled={!canDelete || isDeleting}
              className="border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[5px_5px_0_#111] transition-none hover:bg-[#ff8fab] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:opacity-60"
            >
              <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`} />
              {isDeleting ? '正在注销...' : '永久删除'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default AccountDeletionModal
