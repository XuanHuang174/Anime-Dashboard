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

  const avatar = (sizeClass, preview = false) => (
    <span
      className={`flex shrink-0 items-center justify-center overflow-hidden border-4 border-black bg-white text-black ${preview ? 'shadow-[6px_6px_0_#4ecdc4]' : 'shadow-[4px_4px_0_#111]'} ${sizeClass}`}
    >
      {profile?.avatar
        ? <img src={profile.avatar} alt={`${displayName}的头像`} className="h-full w-full object-cover" />
        : <i className="fa-solid fa-star text-[#ff6b6b]" />}
    </span>
  )

  const dropdownMenu = isMenuOpen && (
    <div
      role="menu"
      className="absolute left-0 top-[calc(100%+0.75rem)] z-[100] w-72 border-[3px] border-black bg-[#fff5ee] p-2 text-black shadow-[7px_7px_0_#111] animate-[viewFadeIn_0.16s_ease-out_both]"
    >
      <div className="mb-2 border-2 border-black bg-[#ffe66d] px-3 py-3 shadow-[3px_3px_0_#ff6b6b]">
        <p className="truncate text-base font-black">{displayName}</p>
        <p className="mt-1 truncate text-xs font-bold text-zinc-700">
          {profile?.email || '正在读取个人资料...'}
        </p>
      </div>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          setIsMenuOpen(false)
          setIsProfileModalOpen(true)
        }}
        className="memphis-profile-menu-item flex w-full items-center gap-3 border-2 border-transparent px-3 py-2.5 text-left text-sm font-black"
      >
        <span className="flex h-8 w-8 items-center justify-center border-2 border-black bg-[#4ecdc4] shadow-[2px_2px_0_#111]">
          <i className="fa-solid fa-user-pen" />
        </span>
        设置 / 修改资料
      </button>
      <div className="my-1 border-t-2 border-black" />
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          setIsMenuOpen(false)
          onLogout()
        }}
        className="memphis-profile-menu-item flex w-full items-center gap-3 border-2 border-transparent px-3 py-2.5 text-left text-sm font-black"
      >
        <span className="flex h-8 w-8 items-center justify-center border-2 border-black bg-white shadow-[2px_2px_0_#111]">
          <i className="fa-solid fa-arrow-right-from-bracket" />
        </span>
        <span>
          <span className="block">退出登录</span>
          <span className="block text-[9px] font-bold text-zinc-500">保留账户和全部数据</span>
        </span>
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          setIsMenuOpen(false)
          setIsAccountDeletionOpen(true)
        }}
        className="memphis-profile-menu-item mt-1 flex w-full items-center gap-3 border-2 border-transparent px-3 py-2.5 text-left text-sm font-black text-red-700"
      >
        <span className="flex h-8 w-8 items-center justify-center border-2 border-black bg-[#ff6b6b] shadow-[2px_2px_0_#111]">
          <i className="fa-solid fa-user-slash" />
        </span>
        <span>
          <span className="block">永久注销账户</span>
          <span className="block text-[9px] font-bold text-red-700">彻底删除全部私人数据</span>
        </span>
      </button>
    </div>
  )

  const actions = (
    <div className={`flex shrink-0 flex-wrap items-center gap-2 ${isPreviewMode ? 'md:max-w-[48%] md:justify-end' : 'lg:max-w-[54%] lg:justify-end'}`}>
      <JournalDataTransfer
        onImported={onRecordsImported}
        showToast={showToast}
        theme="memphis"
      />
      <button
        type="button"
        onClick={onTogglePreview}
        className={`dashboard-arcade-button inline-flex items-center gap-2 ${isPreviewMode ? 'bg-[#ffe66d]' : 'bg-white'}`}
      >
        <i className={`fa-solid ${isPreviewMode ? 'fa-pen-to-square' : 'fa-eye'}`} />
        {isPreviewMode ? '编辑模式' : '预览模式'}
      </button>
      <button
        type="button"
        onClick={onAddAnime}
        className="dashboard-arcade-button dashboard-add-anime-cta group inline-flex items-center gap-2 bg-[#4ecdc4]"
      >
        <span className="flex h-6 w-6 items-center justify-center border-2 border-black bg-white text-xs group-hover:rotate-6">
          <i className="fa-solid fa-plus" />
        </span>
        添加番剧
        <span className="border-2 border-black bg-[#ffe66d] px-1.5 py-0.5 text-[9px] font-black">ADD</span>
      </button>
    </div>
  )

  return (
    <>
      {isPreviewMode ? (
        <header className={`dashboard-memphis-console dashboard-preview-header relative overflow-visible border-b-4 border-black bg-[#ff6b6b] text-black ${isMenuOpen ? 'z-[80]' : 'z-10'}`}>
          <div
            className="pointer-events-none absolute inset-0 opacity-20"
            style={{
              backgroundImage: 'linear-gradient(#111 2px, transparent 2px), linear-gradient(90deg, #111 2px, transparent 2px)',
              backgroundSize: '38px 38px',
            }}
            aria-hidden="true"
          />
          <div className="pointer-events-none absolute -right-10 -top-14 h-52 w-52 rounded-full border-[18px] border-black bg-[#4ecdc4] shadow-[8px_8px_0_#ffe66d]" aria-hidden="true">
            <i className="fa-solid fa-star absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rotate-12 text-7xl text-[#ffe66d]"></i>
          </div>

          <div className="relative z-10 mx-auto max-w-7xl px-4 pb-12 pt-9 sm:px-6 lg:px-8">
            <div className="flex flex-col gap-7 md:flex-row md:items-center md:justify-between">
              <div className="max-w-3xl">
                <span className="inline-flex -rotate-1 items-center border-[3px] border-black bg-[#ffe66d] px-3 py-1.5 text-xs font-black uppercase tracking-[0.16em] shadow-[4px_4px_0_#111]">
                  <i className="fa-solid fa-heart mr-2"></i>
                  个人手账 / 追番记录
                </span>

                <h1
                  className="mt-5 flex items-center gap-4 text-3xl font-black tracking-[-0.04em] sm:text-5xl"
                  style={{ fontFamily: '"Arial Black", Impact, sans-serif' }}
                >
                  <span ref={menuRef} className="relative shrink-0">
                    <button
                      type="button"
                      onClick={() => setIsMenuOpen((current) => !current)}
                      className="block -rotate-3 outline-none active:translate-x-1 active:translate-y-1"
                      aria-haspopup="menu"
                      aria-expanded={isMenuOpen}
                    >
                      {avatar('h-14 w-14 text-xl sm:h-16 sm:w-16', true)}
                    </button>
                    {dropdownMenu}
                  </span>
                  <span className="inline-block bg-black px-3 py-2 text-white">
                    {displayName} 的番剧汇总
                  </span>
                </h1>

                <p className="mt-5 max-w-2xl border-l-[8px] border-black bg-white px-4 py-3 text-sm font-bold leading-6 shadow-[6px_6px_0_#111] sm:text-base">
                  {subtitle}
                </p>
              </div>

              {actions}
            </div>
          </div>
        </header>
      ) : (
        <header className="dashboard-memphis-console relative z-40 mx-4 mt-4 overflow-visible border-4 border-black bg-[#ff8fab] text-black shadow-[8px_8px_0_#111] sm:mx-6 lg:mx-auto lg:max-w-7xl">
          <div className="dashboard-console-pattern pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true" />
          <div className="pointer-events-none absolute -right-7 -top-7 h-20 w-20 rotate-12 border-4 border-black bg-[#ffe66d] shadow-[5px_5px_0_#4ecdc4]" aria-hidden="true" />

          <div className="relative z-10 flex flex-col justify-between gap-5 px-4 py-5 sm:px-6 lg:flex-row lg:items-center">
            <div className="min-w-0">
              <span className="inline-flex -rotate-1 items-center border-2 border-black bg-[#ffe66d] px-3 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
                <i className="fa-solid fa-bolt mr-2" />
                PRIVATE JOURNAL / 私人手账
              </span>

              <div className="mt-3 flex items-center gap-3">
                <div ref={menuRef} className="relative shrink-0">
                  <button
                    type="button"
                    onClick={() => setIsMenuOpen((current) => !current)}
                    className="group outline-none active:translate-x-1 active:translate-y-1"
                    aria-haspopup="menu"
                    aria-expanded={isMenuOpen}
                  >
                    {avatar('h-14 w-14 text-xl sm:h-16 sm:w-16 text-2xl')}
                  </button>
                  {dropdownMenu}
                </div>

                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-black tracking-tight sm:text-3xl">
                    {displayName}的番剧手账房
                  </h1>
                  <div className="mt-1 flex max-w-2xl items-start gap-2 text-xs font-bold leading-5 sm:text-sm">
                    <p className="line-clamp-2">{subtitle}</p>
                    <button
                      type="button"
                      onClick={() => setIsProfileModalOpen(true)}
                      className="flex h-6 w-6 shrink-0 items-center justify-center border-2 border-black bg-white text-[10px] shadow-[2px_2px_0_#111] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none"
                      aria-label="修改展示副标题"
                      title="修改展示副标题"
                    >
                      <i className="fa-solid fa-pen" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {actions}
          </div>
        </header>
      )}

      <ProfileModal
        isOpen={isProfileModalOpen}
        profile={profile}
        onClose={() => setIsProfileModalOpen(false)}
        onSaved={setProfile}
        showToast={showToast}
      />
      <AccountDeletionModal
        isOpen={isAccountDeletionOpen}
        profile={profile}
        onClose={() => setIsAccountDeletionOpen(false)}
        onDeleted={onAccountDeleted}
      />
    </>
  )
}

export default DashboardHeader
