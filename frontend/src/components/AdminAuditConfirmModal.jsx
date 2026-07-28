import { useRef } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

gsap.registerPlugin(useGSAP)

function AdminAuditConfirmModal({
  action,
  submission,
  isSubmitting,
  onConfirm,
  onClose,
}) {
  const modalRef = useRef(null)
  const panelRef = useRef(null)
  const closingRef = useRef(false)
  const isApprove = action === 'approve'
  const isRemoval = submission.audit_status === 'removal_requested'

  const { contextSafe } = useGSAP(() => {
    gsap.fromTo(
      panelRef.current,
      { scale: 0.72, y: -45, rotation: -2.5, autoAlpha: 0 },
      {
        scale: 1,
        y: 0,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.48,
        ease: 'back.out(1.7)',
      },
    )
  }, { scope: modalRef })

  const handleClose = contextSafe(() => {
    if (closingRef.current || isSubmitting) return
    closingRef.current = true
    gsap.to(panelRef.current, {
      y: 45,
      scale: 0.86,
      autoAlpha: 0,
      duration: 0.25,
      ease: 'power3.in',
      onComplete: onClose,
    })
  })

  const verb = isRemoval
    ? (isApprove ? '批准撤下' : '驳回撤下')
    : (isApprove ? '通过收录' : '打回投稿')

  return (
    <div
      ref={modalRef}
      className="fixed inset-0 z-[400] flex items-center justify-center bg-black/80 px-5 backdrop-blur-[2px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
      role="presentation"
    >
      <section
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="admin-audit-title"
        className="w-full max-w-lg border-4 border-black bg-[#fff5ee] p-6 text-black shadow-[12px_12px_0_#ff5b68]"
      >
        <div className="mb-5 flex items-start gap-4 border-b-4 border-black pb-5">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center border-4 border-black bg-[#ff5b68] text-2xl shadow-[4px_4px_0_#ffdf3e]">
            <i className="fa-solid fa-triangle-exclamation" />
          </span>
          <div>
            <span className="mb-1 block text-[10px] font-black uppercase tracking-[0.28em]">Audit Confirmation</span>
            <h2 id="admin-audit-title" className="text-2xl font-black">确认{verb}？</h2>
          </div>
        </div>

        <p className="border-2 border-black bg-white p-4 text-sm font-bold leading-7 shadow-[5px_5px_0_#111]">
          你即将对 <strong className="bg-[#ffdf3e] px-1">《{submission.chinese_title}》</strong>
          执行“{verb}”。确认后将立即变更专栏状态，并向投稿用户发送站内通知。
        </p>

        <div className="mt-7 grid grid-cols-2 gap-4">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="border-4 border-black bg-white px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            取消操作
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isSubmitting}
            className={`border-4 border-black px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-60 ${
              isApprove ? 'bg-[#c7ff4a]' : 'bg-[#ff5b68]'
            }`}
          >
            <i className={`fa-solid ${isSubmitting ? 'fa-spinner animate-spin' : 'fa-gavel'} mr-2`} />
            {isSubmitting ? '正在处理...' : `确认${verb}`}
          </button>
        </div>
      </section>
    </div>
  )
}

export default AdminAuditConfirmModal
