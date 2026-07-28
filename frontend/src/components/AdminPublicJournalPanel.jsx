import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import AdminPublicJournalConfirmModal from './AdminPublicJournalConfirmModal.jsx'
import MemphisToastStack from './MemphisToastStack.jsx'

function normalizeNextUrl(nextUrl) {
  if (!nextUrl) return null
  const parsed = new URL(nextUrl, window.location.origin)
  return `${parsed.pathname.replace(/^\/api\//, '')}${parsed.search}`
}

function getErrorMessage(error, fallback = '操作失败，请稍后重试。') {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  const firstMessage = data && Object.values(data).flat().find(
    (message) => typeof message === 'string',
  )
  return firstMessage || fallback
}

function ProfileAvatar({ profile }) {
  const displayName = profile.nickname || profile.username
  return (
    <span className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden border-4 border-black bg-white text-3xl shadow-[5px_5px_0_#111]">
      {profile.avatar
        ? <img src={profile.avatar} alt={`${displayName}的头像`} className="h-full w-full object-cover" />
        : <i className="fa-solid fa-star text-[#ff5b68]" />}
    </span>
  )
}

function PublicJournalProfileCard({ profile, kind, onReview, onAction }) {
  const isPending = kind === 'pending'
  const displayName = profile.nickname || profile.username
  return (
    <article className="grid gap-5 border-4 border-black bg-white p-5 shadow-[7px_7px_0_#111] lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-center">
      <ProfileAvatar profile={profile} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`border-2 border-black px-3 py-1 text-xs font-black uppercase tracking-[0.12em] shadow-[3px_3px_0_#111] ${isPending ? 'bg-[#ffdf3e]' : 'bg-[#c7ff4a]'}`}>
            <i className={`fa-solid ${isPending ? 'fa-hourglass-half' : 'fa-ticket'} mr-2`} />
            {isPending ? '等待审批' : '已持有通行证'}
          </span>
          <span className="border-2 border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black">
            @{profile.username}
          </span>
        </div>
        <h3 className="mt-4 truncate text-2xl font-black sm:text-3xl">{displayName}</h3>
        <p className="mt-2 line-clamp-2 border-l-[6px] border-black bg-[#fff5ee] px-3 py-2 text-sm font-bold leading-6 text-zinc-600">
          {profile.showcase_subtitle || '这位主理人还没有填写手账副标题。'}
        </p>
        {profile.email && (
          <p className="mt-3 break-all text-xs font-bold text-zinc-500">
            <i className="fa-solid fa-envelope mr-2" />
            {profile.email}
          </p>
        )}
      </div>

      <div className="grid min-w-[215px] gap-3">
        <button
          type="button"
          onClick={() => onReview(profile)}
          className="border-4 border-black bg-[#67b7ff] px-4 py-3 font-black shadow-[5px_5px_0_#111] hover:bg-white active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
        >
          <i className="fa-solid fa-book-open-reader mr-2" />
          查看完整手账
        </button>
        {isPending ? (
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => onAction('approve', profile)}
              className="border-4 border-black bg-[#c7ff4a] px-3 py-3 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
            >
              <i className="fa-solid fa-ticket mr-1.5" />
              发放
            </button>
            <button
              type="button"
              onClick={() => onAction('reject', profile)}
              className="border-4 border-black bg-[#ff5b68] px-3 py-3 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
            >
              <i className="fa-solid fa-xmark mr-1.5" />
              打回
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => onAction('revoke', profile)}
            className="border-4 border-black bg-[#ff5b68] px-4 py-3 font-black shadow-[5px_5px_0_#111] hover:bg-white active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            <i className="fa-solid fa-ban mr-2" />
            强制吊销
          </button>
        )}
      </div>
    </article>
  )
}

function EmptyState({ type }) {
  const isPending = type === 'pending'
  return (
    <div className={`flex min-h-52 flex-col items-center justify-center border-4 border-black p-7 text-center shadow-[7px_7px_0_#111] ${isPending ? 'bg-[#ffdf3e]' : 'bg-[#4ecdc4]'}`}>
      <span className="flex h-16 w-16 items-center justify-center border-4 border-black bg-white text-2xl shadow-[4px_4px_0_#111]">
        <i className={`fa-solid ${isPending ? 'fa-inbox' : 'fa-lock'}`} />
      </span>
      <h3 className="mt-5 text-xl font-black">
        {isPending ? '当前没有待审批申请' : '当前没有已公开用户'}
      </h3>
      <p className="mt-2 text-sm font-bold">
        {isPending ? '新的分享申请会自动出现在这里。' : '发放通行证后，可在此处管理和吊销。'}
      </p>
    </div>
  )
}

function AdminPublicJournalPanel() {
  const navigate = useNavigate()
  const [profiles, setProfiles] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmAction, setConfirmAction] = useState(null)
  const [processingId, setProcessingId] = useState(null)
  const [toasts, setToasts] = useState([])
  const pendingProfiles = useMemo(
    () => profiles.filter((profile) => profile.public_status === 'pending'),
    [profiles],
  )
  const approvedProfiles = useMemo(
    () => profiles.filter((profile) => profile.public_status === 'approved' && profile.is_public),
    [profiles],
  )

  const pushToast = useCallback((message, type = 'success') => {
    setToasts((current) => [
      ...current,
      { id: `${Date.now()}-${Math.random()}`, message, type },
    ])
  }, [])

  const loadProfiles = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const allProfiles = []
      let nextUrl = 'admin/public-journals/?page_size=100'
      while (nextUrl) {
        const response = await api.get(nextUrl)
        const payload = response.data
        if (Array.isArray(payload)) {
          allProfiles.push(...payload)
          nextUrl = null
        } else {
          allProfiles.push(...(payload.results ?? []))
          nextUrl = normalizeNextUrl(payload.next)
        }
      }
      setProfiles(allProfiles)
    } catch (requestError) {
      setError(getErrorMessage(requestError, '无法读取通行证审批队列。'))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadProfiles()
  }, [loadProfiles])

  async function handleConfirm() {
    if (!confirmAction) return
    const { action, profile } = confirmAction
    setProcessingId(profile.id)
    try {
      const response = await api.post(
        `admin/public-journals/${profile.id}/${action}/`,
      )
      const updatedProfile = response.data.profile
      setProfiles((current) => {
        if (updatedProfile?.public_status === 'private') {
          return current.filter((item) => item.id !== profile.id)
        }
        return current.map((item) => item.id === profile.id ? updatedProfile : item)
      })
      setConfirmAction(null)
      pushToast(response.data.detail || '通行证状态已更新。')
    } catch (requestError) {
      pushToast(getErrorMessage(requestError), 'error')
    } finally {
      setProcessingId(null)
    }
  }

  function openReview(profile) {
    navigate(`/admin-dashboard/public-journal-review/${encodeURIComponent(profile.username)}`, {
      state: { sharedProfile: profile },
    })
  }

  return (
    <>
      <section className="admin-console-panel mx-auto mt-8 max-w-[1500px] border-4 border-black bg-[#fff5ee] p-4 text-black shadow-[10px_10px_0_#4ecdc4] sm:p-6">
        <div className="mb-7 flex flex-col justify-between gap-4 border-b-4 border-black pb-5 md:flex-row md:items-end">
          <div>
            <span className="inline-flex items-center gap-2 border-2 border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
              <i className="fa-solid fa-ticket" />
              Public Journal Pass
            </span>
            <h2 className="mt-3 text-3xl font-black sm:text-4xl">番剧共创 · 通行证审批</h2>
            <p className="mt-2 font-bold text-zinc-600">审阅实时手账，发放、打回或强制吊销公开权限。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="border-3 border-black bg-[#ffdf3e] px-4 py-2 text-sm font-black shadow-[4px_4px_0_#111]">
              待审批 {pendingProfiles.length}
            </span>
            <span className="border-3 border-black bg-[#c7ff4a] px-4 py-2 text-sm font-black shadow-[4px_4px_0_#111]">
              已公开 {approvedProfiles.length}
            </span>
            <button
              type="button"
              onClick={loadProfiles}
              disabled={isLoading}
              className="border-4 border-black bg-white px-4 py-2 font-black shadow-[4px_4px_0_#ff5b68] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
            >
              <i className={`fa-solid ${isLoading ? 'fa-spinner animate-spin' : 'fa-rotate'} mr-2`} />
              刷新名单
            </button>
          </div>
        </div>

        {error && (
          <div role="alert" className="mb-6 border-4 border-black bg-[#ff5b68] p-4 font-black shadow-[6px_6px_0_#111]">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            {error}
          </div>
        )}

        {isLoading ? (
          <div className="flex min-h-72 flex-col items-center justify-center border-4 border-black bg-[#ffdf3e] text-center shadow-[7px_7px_0_#111]">
            <i className="fa-solid fa-satellite-dish mb-4 animate-pulse text-4xl" />
            <p className="font-black uppercase tracking-[0.16em]">正在读取通行证队列</p>
          </div>
        ) : (
          <div className="space-y-10">
            <section>
              <div className="mb-5 flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center border-4 border-black bg-[#ffdf3e] text-xl shadow-[4px_4px_0_#111]">
                  <i className="fa-solid fa-hourglass-half" />
                </span>
                <div>
                  <h3 className="text-2xl font-black">待审核列表</h3>
                  <p className="text-sm font-bold text-zinc-600">先查看完整手账，再决定是否发放通行证。</p>
                </div>
              </div>
              {pendingProfiles.length ? (
                <div className="space-y-5">
                  {pendingProfiles.map((profile) => (
                    <PublicJournalProfileCard
                      key={profile.id}
                      profile={profile}
                      kind="pending"
                      onReview={openReview}
                      onAction={(action, target) => setConfirmAction({ action, profile: target })}
                    />
                  ))}
                </div>
              ) : <EmptyState type="pending" />}
            </section>

            <div className="h-3 border-2 border-black bg-[repeating-linear-gradient(135deg,#111_0,#111_9px,#ff5b68_9px,#ff5b68_18px)]" />

            <section>
              <div className="mb-5 flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center border-4 border-black bg-[#c7ff4a] text-xl shadow-[4px_4px_0_#111]">
                  <i className="fa-solid fa-earth-asia" />
                </span>
                <div>
                  <h3 className="text-2xl font-black">已公开用户</h3>
                  <p className="text-sm font-bold text-zinc-600">违规或不再适合公开的手账可在此立即吊销。</p>
                </div>
              </div>
              {approvedProfiles.length ? (
                <div className="space-y-5">
                  {approvedProfiles.map((profile) => (
                    <PublicJournalProfileCard
                      key={profile.id}
                      profile={profile}
                      kind="approved"
                      onReview={openReview}
                      onAction={(action, target) => setConfirmAction({ action, profile: target })}
                    />
                  ))}
                </div>
              ) : <EmptyState type="approved" />}
            </section>
          </div>
        )}
      </section>

      {confirmAction && (
        <AdminPublicJournalConfirmModal
          action={confirmAction.action}
          profile={confirmAction.profile}
          isSubmitting={processingId === confirmAction.profile.id}
          onConfirm={handleConfirm}
          onClose={() => setConfirmAction(null)}
        />
      )}
      <MemphisToastStack
        toasts={toasts}
        onDismiss={(id) => setToasts((current) => current.filter((toast) => toast.id !== id))}
      />
    </>
  )
}

export default AdminPublicJournalPanel
