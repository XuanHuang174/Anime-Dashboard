import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'
import { getImageFileError, IMAGE_FILE_ACCEPT } from '../utils/imageUpload.js'

gsap.registerPlugin(useGSAP)

function getApiError(error, fallback = '资料保存失败，请稍后重试。') {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  if (data && typeof data === 'object') {
    const firstMessage = Object.values(data).flat().find((item) => typeof item === 'string')
    if (firstMessage) return firstMessage
  }
  return fallback
}

function ProfileAvatar({ src, label, className = '' }) {
  if (src) {
    return <img src={src} alt={`${label}的头像`} className={`h-full w-full object-cover ${className}`} />
  }
  return <i className={`fa-solid fa-star text-[#ffe66d] ${className}`} aria-hidden="true" />
}

const inputClass = 'w-full border-[3px] border-black bg-white px-4 py-3 text-sm font-bold text-zinc-900 shadow-[4px_4px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none'
const secondaryButtonClass = 'border-[3px] border-black bg-[#f4f1ea] px-4 py-3 text-sm font-black text-black shadow-[5px_5px_0_#111] transition-none hover:bg-white active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-50'
const primaryButtonClass = 'border-[3px] border-black bg-[#ff6b9d] px-4 py-3 text-sm font-black text-black shadow-[5px_5px_0_#111] transition-none hover:bg-[#4ecdc4] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-50'

function ProfileModal({ isOpen, profile, onClose, onSaved, showToast }) {
  const navigate = useNavigate()
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const fileInputRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const [activeTab, setActiveTab] = useState('profile')
  const [nickname, setNickname] = useState('')
  const [showcaseSubtitle, setShowcaseSubtitle] = useState('')
  const [avatarFile, setAvatarFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirm, setNewPasswordConfirm] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [isChangingPassword, setIsChangingPassword] = useState(false)

  const { contextSafe } = useGSAP(() => {
    if (!isOpen || !overlayRef.current || !modalRef.current) return undefined

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion) {
      gsap.set([overlayRef.current, modalRef.current, '.profile-modal-piece'], {
        clearProps: 'all',
      })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(
        overlayRef.current,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.14, ease: 'power1.out' },
      )
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, scale: 0.7, y: -50, rotation: -2 },
        {
          autoAlpha: 1,
          scale: 1,
          y: 0,
          rotation: 0,
          duration: 0.72,
          ease: 'elastic.out(1, 0.6)',
          clearProps: 'transform,opacity,visibility',
        },
        0,
      )
      .fromTo(
        '.profile-modal-piece',
        { autoAlpha: 0, y: -18, scale: 0.94 },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.34,
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
    if (!modalRef.current || !overlayRef.current) {
      afterExit()
      return
    }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      return
    }

    gsap.killTweensOf([modalRef.current, overlayRef.current])
    gsap.timeline({ onComplete: afterExit })
      .to(modalRef.current, {
        y: 50,
        scale: 0.82,
        rotation: 2,
        autoAlpha: 0,
        duration: 0.24,
        ease: 'power3.in',
      })
      .to(overlayRef.current, {
        autoAlpha: 0,
        duration: 0.12,
        ease: 'power2.in',
      }, '-=0.08')
  })

  const isBusy = isSaving || isChangingPassword
  const handleClose = () => {
    if (!isBusy) playExit(onClose)
  }
  closeHandlerRef.current = handleClose

  useEffect(() => {
    if (!isOpen) return undefined
    setNickname(profile?.nickname ?? '')
    setShowcaseSubtitle(profile?.showcase_subtitle ?? '')
    setAvatarFile(null)
    setPreviewUrl(profile?.avatar ?? '')
    setError('')
    setActiveTab('profile')
    setOldPassword('')
    setNewPassword('')
    setNewPasswordConfirm('')
    setPasswordError('')
    if (fileInputRef.current) fileInputRef.current.value = ''

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
  }, [isOpen, profile])

  useEffect(() => {
    if (!avatarFile) return undefined
    const objectUrl = URL.createObjectURL(avatarFile)
    setPreviewUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [avatarFile])

  if (!isOpen) return null

  const fallbackName = profile?.username || '手账主理人'

  const handleAvatarChange = (event) => {
    const file = event.target.files?.[0]
    setError('')
    if (!file) return
    const fileError = getImageFileError(file, '头像')
    if (fileError) {
      setError(fileError)
      event.target.value = ''
      return
    }
    setAvatarFile(file)
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')
    setIsSaving(true)
    const formData = new FormData()
    formData.append('nickname', nickname.trim())
    formData.append('showcase_subtitle', showcaseSubtitle.trim())
    if (avatarFile) formData.append('avatar', avatarFile)

    try {
      const response = await api.patch('user/profile/', formData)
      onSaved(response.data)
      showToast?.('个人资料已更新。', 'success')
      playExit(onClose)
    } catch (requestError) {
      setError(getApiError(requestError))
    } finally {
      setIsSaving(false)
    }
  }

  const handlePasswordSubmit = async (event) => {
    event.preventDefault()
    setPasswordError('')

    if (newPassword !== newPasswordConfirm) {
      setPasswordError('新密码和确认新密码不一致。')
      return
    }

    setIsChangingPassword(true)
    try {
      await api.post('user/change-password/', {
        old_password: oldPassword,
        new_password: newPassword,
      })
      clearTokens()
      showToast?.('密码修改成功，请重新登录。', 'success')
      playExit(() => {
        navigate('/login', {
          replace: true,
          state: { message: '密码修改成功，请使用新密码重新登录。' },
        })
      })
    } catch (requestError) {
      setPasswordError(getApiError(requestError, '密码修改失败，请检查当前密码后重试。'))
    } finally {
      setIsChangingPassword(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[120] flex items-center justify-center overflow-y-auto bg-zinc-950/80 p-2 backdrop-blur-sm sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="profileModalTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section
        ref={modalRef}
        className="relative my-auto max-h-[calc(100vh-1rem)] w-full max-w-xl overflow-y-auto border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111] sm:max-h-[calc(100vh-2rem)]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.08]" style={{ backgroundImage: 'radial-gradient(#111 1.5px, transparent 1.5px)', backgroundSize: '16px 16px' }} aria-hidden="true" />

        <header className="profile-modal-piece relative flex items-start justify-between gap-4 border-b-4 border-black bg-[#4ecdc4] px-5 py-3 sm:px-6">
          <div>
            <p className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.18em] shadow-[3px_3px_0_#111]">
              Personal Profile
            </p>
            <h2 id="profileModalTitle" className="mt-2 text-xl font-black sm:text-2xl">设置个人资料</h2>
            <p className="text-xs font-bold">打造专属于你的手账名片</p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={isBusy}
            className="flex h-10 w-10 shrink-0 items-center justify-center border-[3px] border-black bg-white text-lg text-black shadow-[4px_4px_0_#111] transition-none hover:bg-[#ff6b6b] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
            aria-label="关闭"
          >
            <i className="fa-solid fa-xmark" />
          </button>
        </header>

        <div className="profile-modal-piece relative mx-5 mt-3 grid grid-cols-2 border-[3px] border-black bg-white shadow-[5px_5px_0_#111] sm:mx-6" role="tablist" aria-label="个人设置">
          {[
            ['profile', 'fa-user-pen', '基础资料'],
            ['security', 'fa-shield-halved', '安全设置'],
          ].map(([value, icon, label], index) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={activeTab === value}
              onClick={() => {
                setActiveTab(value)
                setError('')
                setPasswordError('')
              }}
              disabled={isBusy}
              className={`flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-black transition-none disabled:opacity-50 ${index === 1 ? 'border-l-[3px] border-black' : ''} ${activeTab === value ? (value === 'profile' ? 'bg-[#ffe66d]' : 'bg-[#ff8fab]') : 'bg-[#f4f1ea] text-zinc-500 hover:bg-white hover:text-black'}`}
            >
              <i className={`fa-solid ${icon}`} />
              {label}
            </button>
          ))}
        </div>

        {activeTab === 'profile' ? (
          <form onSubmit={handleSubmit} className="relative space-y-3 px-5 py-4 sm:px-6">
            <div className="profile-modal-piece flex flex-col items-center gap-3 border-[3px] border-black bg-[#ff8fab] p-3 shadow-[6px_6px_0_#4ecdc4] sm:flex-row">
              <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden border-4 border-black bg-white text-3xl shadow-[5px_5px_0_#ffe66d]">
                <ProfileAvatar src={previewUrl} label={nickname || fallbackName} />
              </div>
              <div className="min-w-0 flex-1 text-center sm:text-left">
                <p className="truncate text-xl font-black">{nickname.trim() || fallbackName}</p>
                <p className="mt-1 truncate text-sm font-bold text-zinc-800">{profile?.email || profile?.username}</p>
                <label className="mt-2 inline-flex cursor-pointer items-center gap-2 border-[3px] border-black bg-white px-4 py-1.5 text-xs font-black shadow-[4px_4px_0_#111] transition-none hover:bg-[#ffe66d] active:translate-x-1 active:translate-y-1 active:shadow-none">
                  <i className="fa-solid fa-camera" />
                  选择本地头像
                  <input ref={fileInputRef} type="file" accept={IMAGE_FILE_ACCEPT} className="sr-only" onChange={handleAvatarChange} />
                </label>
                <p className="mt-1.5 text-[10px] font-bold">支持 JPG、PNG、WebP，最大 5MB</p>
              </div>
            </div>

            <label className="profile-modal-piece block">
              <span className="mb-1 block text-sm font-black"><i className="fa-solid fa-signature mr-2 text-[#ff4f87]" />昵称</span>
              <input
                value={nickname}
                onChange={(event) => setNickname(event.target.value)}
                maxLength={50}
                className={inputClass}
                placeholder={fallbackName}
              />
              <span className="mt-1 block text-right text-[11px] font-black">{nickname.length}/50</span>
            </label>

            <label className="profile-modal-piece block">
              <span className="mb-1 block text-sm font-black"><i className="fa-solid fa-quote-left mr-2 text-[#087f78]" />预览页副标题</span>
              <textarea
                value={showcaseSubtitle}
                onChange={(event) => setShowcaseSubtitle(event.target.value)}
                maxLength={200}
                rows={2}
                className={`${inputClass} resize-none leading-6`}
                placeholder="写一句属于你的追番宣言吧"
              />
              <span className="mt-1 block text-right text-[11px] font-black">{showcaseSubtitle.length}/200</span>
            </label>

            {error && (
              <p role="alert" className="profile-modal-piece border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]">
                <i className="fa-solid fa-circle-exclamation mr-2" />{error}
              </p>
            )}

            <div className="profile-modal-piece grid grid-cols-2 gap-3 pt-1">
              <button type="button" onClick={handleClose} disabled={isSaving} className={secondaryButtonClass}>暂不修改</button>
              <button type="submit" disabled={isSaving} className={primaryButtonClass}>
                <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`} />
                {isSaving ? '保存中...' : '保存资料'}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handlePasswordSubmit} className="relative space-y-3 px-5 py-4 sm:px-6">
            <div className="profile-modal-piece border-[3px] border-black bg-[#ffe66d] p-3 shadow-[6px_6px_0_#ff8fab]">
              <div className="flex items-center gap-4">
                <span className="flex h-14 w-14 shrink-0 items-center justify-center border-[3px] border-black bg-[#4ecdc4] text-xl shadow-[4px_4px_0_#111]">
                  <i className="fa-solid fa-key" />
                </span>
                <div>
                  <h3 className="font-black">修改登录密码</h3>
                  <p className="mt-1 text-xs font-bold leading-5">修改成功后将自动退出登录，请使用新密码重新进入手账房。</p>
                </div>
              </div>
            </div>

            {[
              ['old-password', '旧密码', 'fa-lock', oldPassword, setOldPassword, 'current-password', '请输入当前登录密码'],
              ['new-password', '新密码', 'fa-key', newPassword, setNewPassword, 'new-password', '请输入新的安全密码'],
              ['confirm-password', '确认新密码', 'fa-shield-halved', newPasswordConfirm, setNewPasswordConfirm, 'new-password', '请再次输入新密码'],
            ].map(([id, label, icon, value, setter, autoComplete, placeholder]) => (
              <label key={id} htmlFor={id} className="profile-modal-piece block">
                <span className="mb-1 block text-sm font-black"><i className={`fa-solid ${icon} mr-2 text-[#087f78]`} />{label}</span>
                <input
                  id={id}
                  required
                  type="password"
                  autoComplete={autoComplete}
                  value={value}
                  onChange={(event) => setter(event.target.value)}
                  className={inputClass}
                  placeholder={placeholder}
                />
              </label>
            ))}

            {passwordError && (
              <p role="alert" className="profile-modal-piece border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]">
                <i className="fa-solid fa-circle-exclamation mr-2" />{passwordError}
              </p>
            )}

            <div className="profile-modal-piece grid grid-cols-2 gap-3 pt-1">
              <button type="button" onClick={handleClose} disabled={isChangingPassword} className={secondaryButtonClass}>暂不修改</button>
              <button type="submit" disabled={isChangingPassword} className={primaryButtonClass}>
                <i className={`fa-solid ${isChangingPassword ? 'fa-spinner animate-spin' : 'fa-shield-check'} mr-2`} />
                {isChangingPassword ? '修改中...' : '修改密码'}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  )
}

export default ProfileModal
