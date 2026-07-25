import { useEffect, useState } from 'react'
import api from '../api/api.js'

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
  const [password, setPassword] = useState('')
  const [confirmText, setConfirmText] = useState('')
  const [error, setError] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)

  useEffect(() => {
    if (!isOpen) return undefined
    setPassword('')
    setConfirmText('')
    setError('')
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
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
      onDeleted()
    } catch (requestError) {
      setError(getApiError(requestError))
      setIsDeleting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-md animate-[viewFadeIn_0.2s_ease-out_both]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="accountDeletionTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isDeleting) onClose()
      }}
    >
      <section className="w-full max-w-md overflow-hidden rounded-3xl border border-white/60 bg-white shadow-2xl shadow-rose-950/30" onMouseDown={(event) => event.stopPropagation()}>
        <div className="h-2 bg-gradient-to-r from-orange-400 via-rose-500 to-pink-600" />
        <form onSubmit={handleDelete} className="px-6 py-7">
          <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-rose-100 bg-rose-50 text-2xl text-rose-500 shadow-inner">
            <i className="fa-solid fa-user-slash" />
          </span>
          <div className="mt-4 text-center">
            <h2 id="accountDeletionTitle" className="text-xl font-black text-slate-800">永久注销账户</h2>
            <p className="mt-2 text-sm font-medium leading-6 text-slate-500">
              这会彻底删除 <strong className="text-rose-500">{profile?.nickname || profile?.username || '当前用户'}</strong> 的头像、追番记录、筛选配置和个性化方案，且无法恢复。
            </p>
          </div>

          <div className="mt-5 rounded-2xl border border-rose-100 bg-rose-50/80 p-4 text-xs font-bold leading-6 text-rose-600 shadow-inner">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            注销后该邮箱可以重新注册，但新账号将从空白手账重新开始。
          </div>

          <label className="mt-5 block">
            <span className="mb-2 block text-sm font-black text-slate-700">当前密码</span>
            <div className="relative">
              <i className="fa-solid fa-lock absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
              <input required type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 py-3 pl-11 pr-4 text-sm font-medium text-slate-700 caret-rose-500 shadow-inner outline-none transition placeholder:text-slate-400 focus:translate-y-[2px] focus:border-rose-400 focus:border-b focus:bg-white focus:text-slate-800 focus:ring-4 focus:ring-rose-100" placeholder="请输入当前登录密码" />
            </div>
          </label>

          <label className="mt-4 block">
            <span className="mb-2 block text-sm font-black text-slate-700">输入“{CONFIRM_TEXT}”以确认</span>
            <input required value={confirmText} onChange={(event) => setConfirmText(event.target.value)} className="w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-slate-50/80 px-4 py-3 text-sm font-medium text-slate-700 caret-rose-500 shadow-inner outline-none transition placeholder:text-slate-400 focus:translate-y-[2px] focus:border-rose-400 focus:border-b focus:bg-white focus:text-slate-800 focus:ring-4 focus:ring-rose-100" placeholder={CONFIRM_TEXT} />
          </label>

          {error && <p role="alert" className="mt-4 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-600 shadow-inner"><i className="fa-solid fa-circle-exclamation mr-2" />{error}</p>}

          <div className="mt-6 grid grid-cols-2 gap-3">
            <button type="button" onClick={onClose} disabled={isDeleting} className="rounded-xl bg-slate-100 px-4 py-3 text-sm font-bold text-slate-600 shadow-inner transition hover:bg-slate-200 active:scale-95 disabled:opacity-50">保留账户</button>
            <button type="submit" disabled={!canDelete || isDeleting} className="rounded-xl border-b-[4px] border-b-rose-700 bg-gradient-to-r from-rose-500 to-pink-600 px-4 py-3 text-sm font-black text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg active:translate-y-[2px] active:border-b disabled:cursor-not-allowed disabled:border-b-0 disabled:opacity-40">
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
