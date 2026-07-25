import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import AccountDeletionModal from './AccountDeletionModal.jsx'
import JournalDataTransfer from './JournalDataTransfer.jsx'
import ProfileModal from './ProfileModal.jsx'

const DEFAULT_SUBTITLE = '记录每一次与动画相遇，也珍藏属于自己的追番时光。'

function DashboardHeader({
  isPreviewMode,
  onTogglePreview,
  onAddAnime,
  onRecordsImported,
  onLogout,
  onAccountDeleted,
  onUnauthorized,
  showToast,
}) {
  const menuRef = useRef(null)
  const [profile, setProfile] = useState(null)
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [isProfileModalOpen, setIsProfileModalOpen] = useState(false)
  const [isAccountDeletionOpen, setIsAccountDeletionOpen] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    api.get('user/profile/', { signal: controller.signal })
      .then((response) => setProfile(response.data))
      .catch((error) => {
        if (error.code === 'ERR_CANCELED') return
        if (error.response?.status === 401) {
          onUnauthorized()
          return
        }
        showToast?.('个人资料加载失败，暂时使用默认头像。', 'error')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!isMenuOpen) return undefined
    const closeOnOutsideClick = (event) => {
      if (!menuRef.current?.contains(event.target)) setIsMenuOpen(false)
    }
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setIsMenuOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [isMenuOpen])

  const displayName = profile?.nickname?.trim() || profile?.username || '手账主理人'
  const subtitle = profile?.showcase_subtitle?.trim() || DEFAULT_SUBTITLE

  const avatar = (sizeClass) => (
    <span className={`flex shrink-0 items-center justify-center overflow-hidden border border-white/40 bg-white/20 shadow-lg backdrop-blur-sm ${sizeClass}`}>
      {profile?.avatar
        ? <img src={profile.avatar} alt={`${displayName}的头像`} className="h-full w-full object-cover" />
        : <i className="fa-solid fa-star text-yellow-300" />}
    </span>
  )

  const dropdownMenu = isMenuOpen && (
    <div role="menu" className="absolute left-0 top-[calc(100%+0.65rem)] z-50 w-64 overflow-hidden rounded-2xl border border-white/60 bg-white/90 p-2 text-slate-700 shadow-2xl shadow-indigo-950/25 ring-1 ring-slate-900/5 backdrop-blur-xl animate-[viewFadeIn_0.18s_ease-out_both]">
      <div className="mb-2 rounded-xl bg-gradient-to-br from-pink-50 to-indigo-50 px-3 py-3 shadow-inner">
        <p className="truncate text-sm font-black text-slate-800">{displayName}</p>
        <p className="mt-0.5 truncate text-[10px] font-medium text-slate-400">{profile?.email || '正在读取个人资料...'}</p>
      </div>
      <button type="button" role="menuitem" onClick={() => { setIsMenuOpen(false); setIsProfileModalOpen(true) }} className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-bold transition hover:bg-indigo-50 hover:text-indigo-600">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-50 text-indigo-500 shadow-inner"><i className="fa-solid fa-user-pen" /></span>
        设置/修改资料
      </button>
      <div className="my-1 border-t border-slate-100" />
      <button type="button" role="menuitem" onClick={() => { setIsMenuOpen(false); onLogout() }} className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-bold text-slate-600 transition hover:bg-indigo-50 hover:text-indigo-600">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-50 text-indigo-500 shadow-inner"><i className="fa-solid fa-arrow-right-from-bracket" /></span>
        <span><span className="block">退出登录</span><span className="block text-[9px] font-medium text-slate-400">保留账户和全部数据</span></span>
      </button>
      <button type="button" role="menuitem" onClick={() => { setIsMenuOpen(false); setIsAccountDeletionOpen(true) }} className="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-bold text-rose-500 transition hover:bg-rose-50">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-rose-50 text-rose-500 shadow-inner"><i className="fa-solid fa-user-slash" /></span>
        <span><span className="block">永久注销账户</span><span className="block text-[9px] font-medium text-rose-300">彻底删除全部私人数据</span></span>
      </button>
    </div>
  )

  const actions = (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
      <JournalDataTransfer
        onImported={onRecordsImported}
        showToast={showToast}
      />
      <button
        type="button"
        onClick={onTogglePreview}
        className={`inline-flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-black shadow-md backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:shadow-lg active:translate-y-0 active:scale-95 sm:px-4 ${isPreviewMode ? 'border-white/70 bg-white text-indigo-600' : 'border-white/35 bg-white/15 text-white hover:bg-white/25'}`}
      >
        <i className={`fa-solid ${isPreviewMode ? 'fa-pen-to-square' : 'fa-eye'}`} />
        {isPreviewMode ? '编辑模式' : '预览模式'}
      </button>
      <button type="button" onClick={onAddAnime} className="group relative overflow-visible rounded-xl border border-white/80 bg-white px-3.5 py-2.5 text-sm font-black text-indigo-600 shadow-xl shadow-indigo-950/20 ring-4 ring-white/15 transition-all hover:-translate-y-1 hover:scale-[1.03] hover:shadow-2xl active:translate-y-0 active:scale-95 sm:px-5">
        <span className="absolute -right-1.5 -top-1.5 flex h-3 w-3"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-yellow-300 opacity-75" /><span className="relative inline-flex h-3 w-3 rounded-full border-2 border-white bg-yellow-400" /></span>
        <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-pink-500 to-indigo-500 text-white shadow-sm transition-transform group-hover:rotate-90 sm:mr-2"><i className="fa-solid fa-plus" /></span>
        <span className="hidden sm:inline">添加番剧</span>
        <span className="ml-2 hidden rounded-full bg-indigo-50 px-1.5 py-0.5 text-[9px] font-black tracking-wider text-indigo-400 lg:inline">ADD</span>
      </button>
    </div>
  )

  return (
    <>
      {isPreviewMode ? (
        <header className="relative overflow-visible border-b border-white/30 bg-gradient-to-r from-pink-400 via-purple-400 to-indigo-500 text-white shadow-lg">
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="absolute right-0 top-0 -mr-10 -mt-12 text-[15rem] text-white/10"><i className="fa-solid fa-star rotate-12" /></div>
          </div>
          <div className="relative z-30 mx-auto flex max-w-7xl flex-col justify-between gap-6 px-4 py-8 sm:px-6 md:flex-row md:items-center lg:px-8">
            <div className="min-w-0">
              <span className="inline-flex items-center rounded-full bg-white/20 px-2.5 py-1 text-xs font-semibold uppercase tracking-wider text-white shadow-sm backdrop-blur-sm">
                <i className="fa-solid fa-heart mr-1.5 animate-pulse" />个人手账 / 追番记录
              </span>
              <div className="mt-3 flex items-center gap-3">
                <div ref={menuRef} className="relative shrink-0">
                  <button type="button" onClick={() => setIsMenuOpen((current) => !current)} className="rounded-2xl outline-none transition hover:scale-105 focus:ring-4 focus:ring-white/25" aria-haspopup="menu" aria-expanded={isMenuOpen}>
                    {avatar('h-14 w-14 rounded-2xl text-xl sm:h-16 sm:w-16 sm:text-2xl')}
                  </button>
                  {dropdownMenu}
                </div>
                <h1 className="min-w-0 text-3xl font-extrabold tracking-tight sm:text-4xl">{displayName} 的番剧汇总</h1>
              </div>
              <div className="mt-3 flex max-w-2xl items-start gap-2 text-sm leading-6 text-pink-50/95 sm:text-base">
                <p>{subtitle}</p>
                <button type="button" onClick={() => setIsProfileModalOpen(true)} className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-white/15 text-[10px] text-white shadow-sm transition hover:scale-110 hover:bg-white/30" aria-label="修改展示副标题" title="修改展示副标题">
                  <i className="fa-solid fa-pen" />
                </button>
              </div>
            </div>
            {actions}
          </div>
        </header>
      ) : (
        <header className="relative z-40 border-b border-white/30 bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-600 text-white shadow-lg">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-6 sm:px-6 lg:px-8">
            <div ref={menuRef} className="relative min-w-0">
              <button type="button" onClick={() => setIsMenuOpen((current) => !current)} className="group flex max-w-full items-center gap-3 rounded-2xl p-1.5 pr-3 text-left font-black tracking-tight outline-none transition hover:bg-white/10 focus:ring-4 focus:ring-white/20" aria-haspopup="menu" aria-expanded={isMenuOpen}>
                {avatar('h-12 w-12 rounded-xl text-lg')}
                <span className="min-w-0">
                  <span className="flex items-center gap-2"><span className="truncate text-lg">{displayName}的番剧手账房</span><i className={`fa-solid fa-chevron-down shrink-0 text-[10px] text-indigo-100 transition-transform ${isMenuOpen ? 'rotate-180' : ''}`} /></span>
                  <span className="block truncate text-xs font-medium text-indigo-100">只属于我的私人追番空间</span>
                </span>
              </button>
              {dropdownMenu}
            </div>
            {actions}
          </div>
        </header>
      )}

      <ProfileModal isOpen={isProfileModalOpen} profile={profile} onClose={() => setIsProfileModalOpen(false)} onSaved={setProfile} showToast={showToast} />
      <AccountDeletionModal isOpen={isAccountDeletionOpen} profile={profile} onClose={() => setIsAccountDeletionOpen(false)} onDeleted={onAccountDeleted} />
    </>
  )
}

export default DashboardHeader
