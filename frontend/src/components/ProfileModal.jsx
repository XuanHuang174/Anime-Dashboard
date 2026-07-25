import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'

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
  return <i className={`fa-solid fa-star text-yellow-300 ${className}`} aria-hidden="true" />
}

function ProfileModal({ isOpen, profile, onClose, onSaved, showToast }) {
  const navigate = useNavigate()
  const fileInputRef = useRef(null)
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
      if (event.key === 'Escape') onClose()
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, onClose, profile])

  useEffect(() => {
    if (!avatarFile) return undefined
    const objectUrl = URL.createObjectURL(avatarFile)
    setPreviewUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [avatarFile])

  if (!isOpen) return null

  const fallbackName = profile?.username || '手账主理人'
  const isBusy = isSaving || isChangingPassword

  const handleAvatarChange = (event) => {
    const file = event.target.files?.[0]
    setError('')
    if (!file) return
    if (!file.type.startsWith('image/')) {
      setError('请选择有效的图片文件。')
      event.target.value = ''
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setError('头像图片不能超过 5MB。')
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
      onClose()
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
      navigate('/login', {
        replace: true,
        state: { message: '密码修改成功，请使用新密码重新登录。' },
      })
    } catch (requestError) {
      setPasswordError(getApiError(requestError, '密码修改失败，请检查当前密码后重试。'))
    } finally {
      setIsChangingPassword(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="profileModalTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isBusy) onClose()
      }}
    >
      <section className="w-full max-w-lg overflow-hidden rounded-3xl border border-white/60 bg-white shadow-2xl shadow-indigo-950/30" onMouseDown={(event) => event.stopPropagation()}>
        <div className="h-2 bg-gradient-to-r from-pink-400 via-purple-500 to-indigo-500" />
        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
          <div>
            <p className="text-xs font-black uppercase tracking-[0.18em] text-indigo-400">Personal Profile</p>
            <h2 id="profileModalTitle" className="mt-1 text-xl font-black text-slate-800">设置个人资料</h2>
            <p className="mt-1 text-xs text-slate-500">打造专属于你的手账名片</p>
          </div>
          <button type="button" onClick={onClose} disabled={isBusy} className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 text-slate-500 shadow-inner transition hover:bg-rose-50 hover:text-rose-500 disabled:opacity-50" aria-label="关闭">
            <i className="fa-solid fa-xmark" />
          </button>
        </header>

        <div className="mx-6 mt-5 grid grid-cols-2 rounded-2xl border border-slate-200 bg-slate-100/80 p-1.5 shadow-inner" role="tablist" aria-label="个人设置">
          {[
            ['profile', 'fa-user-pen', '基础资料'],
            ['security', 'fa-shield-halved', '安全设置'],
          ].map(([value, icon, label]) => (
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
              className={`flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-black transition-all disabled:opacity-50 ${activeTab === value ? 'bg-white text-indigo-600 shadow-md ring-1 ring-slate-200' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
            >
              <i className={`fa-solid ${icon}`} />
              {label}
            </button>
          ))}
        </div>

        {activeTab === 'profile' ? (
        <form onSubmit={handleSubmit} className="space-y-6 px-6 py-6">
          <div className="flex flex-col items-center gap-4 rounded-3xl border border-indigo-100 bg-gradient-to-br from-pink-50/70 via-white to-indigo-50/80 p-5 shadow-inner sm:flex-row">
            <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-3xl border-4 border-white bg-gradient-to-br from-pink-400 to-indigo-500 text-4xl shadow-xl shadow-indigo-200/50 ring-1 ring-indigo-100">
              <ProfileAvatar src={previewUrl} label={nickname || fallbackName} />
            </div>
            <div className="min-w-0 flex-1 text-center sm:text-left">
              <p className="truncate text-lg font-black text-slate-800">{nickname.trim() || fallbackName}</p>
              <p className="mt-1 truncate text-xs font-medium text-slate-400">{profile?.email || profile?.username}</p>
              <label className="mt-3 inline-flex cursor-pointer items-center gap-2 rounded-xl border border-indigo-100 border-b-[3px] border-b-indigo-200 bg-white px-4 py-2 text-xs font-black text-indigo-600 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md active:translate-y-[2px] active:border-b">
                <i className="fa-solid fa-camera" />选择本地头像
                <input ref={fileInputRef} type="file" accept="image/*" className="sr-only" onChange={handleAvatarChange} />
              </label>
              <p className="mt-2 text-[10px] text-slate-400">支持 JPG、PNG、WebP，最大 5MB</p>
            </div>
          </div>

          <label className="block">
            <span className="mb-2 block text-sm font-black text-slate-700"><i className="fa-solid fa-signature mr-2 text-pink-400" />昵称</span>
            <input
              value={nickname}
              onChange={(event) => setNickname(event.target.value)}
              maxLength={50}
              className="w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 px-4 py-3 text-sm text-slate-700 shadow-[inset_0_3px_8px_rgba(15,23,42,0.06)] outline-none transition-all focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:bg-white focus:ring-4 focus:ring-indigo-100"
              placeholder={fallbackName}
            />
            <span className="mt-1.5 block text-right text-[10px] font-medium text-slate-400">{nickname.length}/50</span>
          </label>

          <label className="block">
            <span className="mb-2 block text-sm font-black text-slate-700"><i className="fa-solid fa-quote-left mr-2 text-indigo-400" />预览页副标题</span>
            <textarea
              value={showcaseSubtitle}
              onChange={(event) => setShowcaseSubtitle(event.target.value)}
              maxLength={200}
              rows={3}
              className="w-full resize-none rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 px-4 py-3 text-sm leading-6 text-slate-700 shadow-[inset_0_3px_8px_rgba(15,23,42,0.06)] outline-none transition-all focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:bg-white focus:ring-4 focus:ring-indigo-100"
              placeholder="写一句属于你的追番宣言吧"
            />
            <span className="mt-1.5 block text-right text-[10px] font-medium text-slate-400">{showcaseSubtitle.length}/200</span>
          </label>

          {error && <p role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-600 shadow-inner"><i className="fa-solid fa-circle-exclamation mr-2" />{error}</p>}

          <div className="grid grid-cols-2 gap-3">
            <button type="button" onClick={onClose} disabled={isSaving} className="rounded-xl bg-slate-100 px-4 py-3 text-sm font-bold text-slate-600 shadow-inner transition hover:bg-slate-200 active:scale-95 disabled:opacity-50">暂不修改</button>
            <button type="submit" disabled={isSaving} className="rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 to-indigo-500 px-4 py-3 text-sm font-black text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg active:translate-y-[2px] active:border-b disabled:cursor-not-allowed disabled:opacity-60">
              <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`} />
              {isSaving ? '保存中...' : '保存资料'}
            </button>
          </div>
        </form>
        ) : (
          <form onSubmit={handlePasswordSubmit} className="space-y-5 px-6 py-6">
            <div className="rounded-3xl border border-indigo-100 bg-gradient-to-br from-pink-50/70 via-white to-indigo-50/80 p-5 shadow-inner">
              <div className="flex items-center gap-4">
                <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-pink-400 to-indigo-500 text-xl text-white shadow-lg shadow-indigo-200/60">
                  <i className="fa-solid fa-key" />
                </span>
                <div>
                  <h3 className="font-black text-slate-800">修改登录密码</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">修改成功后将自动退出登录，请使用新密码重新进入手账房。</p>
                </div>
              </div>
            </div>

            {[
              ['old-password', '旧密码', 'fa-lock', oldPassword, setOldPassword, 'current-password', '请输入当前登录密码'],
              ['new-password', '新密码', 'fa-key', newPassword, setNewPassword, 'new-password', '请输入新的安全密码'],
              ['confirm-password', '确认新密码', 'fa-shield-halved', newPasswordConfirm, setNewPasswordConfirm, 'new-password', '请再次输入新密码'],
            ].map(([id, label, icon, value, setter, autoComplete, placeholder]) => (
              <label key={id} htmlFor={id} className="block">
                <span className="mb-2 block text-sm font-black text-slate-700"><i className={`fa-solid ${icon} mr-2 text-indigo-400`} />{label}</span>
                <input
                  id={id}
                  required
                  type="password"
                  autoComplete={autoComplete}
                  value={value}
                  onChange={(event) => setter(event.target.value)}
                  className="w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 px-4 py-3 text-sm text-slate-700 shadow-[inset_0_3px_8px_rgba(15,23,42,0.06)] outline-none transition-all focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:bg-white focus:ring-4 focus:ring-indigo-100"
                  placeholder={placeholder}
                />
              </label>
            ))}

            {passwordError && <p role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-600 shadow-inner"><i className="fa-solid fa-circle-exclamation mr-2" />{passwordError}</p>}

            <div className="grid grid-cols-2 gap-3 pt-1">
              <button type="button" onClick={onClose} disabled={isChangingPassword} className="rounded-xl bg-slate-100 px-4 py-3 text-sm font-bold text-slate-600 shadow-inner transition hover:bg-slate-200 active:scale-95 disabled:opacity-50">暂不修改</button>
              <button type="submit" disabled={isChangingPassword} className="rounded-xl border-b-[4px] border-b-indigo-700 bg-gradient-to-r from-pink-500 to-indigo-500 px-4 py-3 text-sm font-black text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg active:translate-y-[2px] active:border-b disabled:cursor-not-allowed disabled:opacity-60">
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
