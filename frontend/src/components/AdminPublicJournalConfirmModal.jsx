import { useRef } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

gsap.registerPlugin(useGSAP)

const ACTION_COPY = {
  approve: {
    eyebrow: 'Issue Pass',
    title: '确认发放通行证？',
    detail: '通过后，该用户的手账将立即出现在番剧共创大厅中。',
    button: '确认发放',
    icon: 'fa-ticket',
    accent: 'bg-[#c7ff4a]',
  },
  reject: {
    eyebrow: 'Reject Request',
    title: '确认打回申请？',
    detail: '该手账将继续保持私密，用户可以修改内容后重新申请。',
    button: '确认打回',
    icon: 'fa-xmark',
    accent: 'bg-[#ff5b68]',
  },
  revoke: {
    eyebrow: 'Revoke Access',
    title: '确认强制吊销？',
    detail: '该用户的手账会立即从共创大厅下线，并恢复为私密状态。',
    button: '确认吊销',
    icon: 'fa-ban',
    accent: 'bg-[#ff5b68]',
  },
}

function AdminPublicJournalConfirmModal({
  action,
  profile,
  isSubmitting,
  onConfirm,
  onClose,
}) {
  const dialogRef = useRef(null)
  const copy = ACTION_COPY[action] ?? ACTION_COPY.reject
  const displayName = profile?.nickname || profile?.username || '该用户'
  const { contextSafe } = useGSAP(() => {
    gsap.from(dialogRef.current, {
      y: -50,
      scale: 0.72,
      autoAlpha: 0,
      duration: 0.48,
      ease: 'back.out(1.7)',
    })
  }, { scope: dialogRef })

  const handleClose = contextSafe(() => {
    if (isSubmitting) return
    gsap.to(dialogRef.current, {
      y: 45,
      scale: 0.9,
      autoAlpha: 0,
      duration: 0.22,
      ease: 'power3.in',
      onComplete: onClose,
    })
  })

  return (
    <div
      className="fixed inset-0 z-[180] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="publicJournalConfirmTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section
        ref={dialogRef}
        className="relative w-full max-w-lg border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={`h-5 border-b-4 border-black ${copy.accent}`} />
        <button
          type="button"
          onClick={handleClose}
          disabled={isSubmitting}
          className="absolute right-4 top-9 flex h-11 w-11 items-center justify-center border-4 border-black bg-white text-xl shadow-[4px_4px_0_#111] hover:bg-[#ff5b68] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
          aria-label="关闭确认弹窗"
        >
          <i className="fa-solid fa-xmark" />
        </button>

        <div className="p-7 sm:p-9">
          <span className={`flex h-20 w-20 -rotate-3 items-center justify-center border-4 border-black text-3xl shadow-[6px_6px_0_#111] ${copy.accent}`}>
            <i className={`fa-solid ${copy.icon}`} />
          </span>
          <span className="mt-7 inline-block border-2 border-black bg-[#ffdf3e] px-3 py-1 text-[10px] font-black uppercase tracking-[0.2em] shadow-[3px_3px_0_#111]">
            {copy.eyebrow}
          </span>
          <h2 id="publicJournalConfirmTitle" className="mt-4 pr-12 text-3xl font-black">
            {copy.title}
          </h2>
          <div className="mt-5 border-4 border-black bg-white p-4 shadow-[5px_5px_0_#4ecdc4]">
            <p className="text-lg font-black">{displayName}</p>
            <p className="mt-1 break-all text-sm font-bold text-zinc-600">@{profile?.username}</p>
          </div>
          <p className="mt-5 border-l-[7px] border-black bg-[#ffe9a8] px-4 py-3 text-sm font-bold leading-6">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            {copy.detail}
          </p>

          <div className="mt-7 grid grid-cols-2 gap-4">
            <button
              type="button"
              onClick={handleClose}
              disabled={isSubmitting}
              className="border-4 border-black bg-white px-4 py-3 font-black shadow-[5px_5px_0_#4ecdc4] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-50"
            >
              暂不处理
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={isSubmitting}
              className={`border-4 border-black px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-50 ${copy.accent}`}
            >
              <i className={`fa-solid ${isSubmitting ? 'fa-spinner animate-spin' : copy.icon} mr-2`} />
              {isSubmitting ? '处理中...' : copy.button}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}

export default AdminPublicJournalConfirmModal
