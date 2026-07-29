import { useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

gsap.registerPlugin(useGSAP)

const QUICK_REASONS = [
  '包含违规内容',
  '剧透过多',
  '内容过于简略',
  '图片模糊',
]

function AdminRejectReasonModal({
  subject,
  contextLabel = '审核申请',
  isSubmitting,
  onConfirm,
  onClose,
}) {
  const backdropRef = useRef(null)
  const panelRef = useRef(null)
  const closingRef = useRef(false)
  const [reason, setReason] = useState('')
  const [validationError, setValidationError] = useState('')

  const { contextSafe } = useGSAP(() => {
    if (!panelRef.current) return
    gsap.fromTo(
      panelRef.current,
      {
        scale: 0.68,
        y: -55,
        rotation: -3,
        autoAlpha: 0,
      },
      {
        scale: 1,
        y: 0,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.5,
        ease: 'back.out(1.5)',
      },
    )
  }, { scope: backdropRef })

  const handleClose = contextSafe(() => {
    if (closingRef.current || isSubmitting) return
    closingRef.current = true
    gsap.to(panelRef.current, {
      y: 55,
      scale: 0.88,
      rotation: 2,
      autoAlpha: 0,
      duration: 0.24,
      ease: 'power3.in',
      onComplete: onClose,
    })
  })

  function chooseQuickReason(quickReason) {
    setReason(quickReason)
    setValidationError('')
  }

  function handleSubmit() {
    const normalizedReason = reason.trim()
    if (!normalizedReason) {
      setValidationError('请填写具体的退回原因后再发送通知。')
      return
    }
    onConfirm(normalizedReason)
  }

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-[500] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="admin-reject-reason-title"
        className="relative w-full max-w-2xl border-4 border-black bg-[#ffdf3e] p-5 text-black shadow-[12px_12px_0_#ff5b68] sm:p-8"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <span className="absolute -top-5 left-6 -rotate-2 border-2 border-white bg-black px-5 py-2 text-xs font-black uppercase tracking-[0.2em] text-white shadow-[4px_4px_0_#ff5b68]">
          驳回通知单 / Rejection Notice
        </span>

        <button
          type="button"
          onClick={handleClose}
          disabled={isSubmitting}
          className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center border-4 border-black bg-white text-xl shadow-[4px_4px_0_#111] hover:bg-[#ff5b68] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
          aria-label="关闭驳回通知单"
        >
          <i className="fa-solid fa-xmark" />
        </button>

        <header className="mt-5 border-b-4 border-black pb-5 pr-14">
          <p className="text-xs font-black uppercase tracking-[0.18em]">{contextLabel}</p>
          <h2 id="admin-reject-reason-title" className="mt-2 text-3xl font-black sm:text-4xl">
            填写具体退回原因
          </h2>
          <p className="mt-3 border-l-[6px] border-black bg-white px-4 py-3 text-sm font-bold leading-6 shadow-[4px_4px_0_#111]">
            即将打回：
            <strong className="ml-1 break-words bg-[#ff8db3] px-1">{subject}</strong>
            。该原因会作为站内通知原样发送给用户。
          </p>
        </header>

        <div className="mt-6">
          <p className="mb-3 flex items-center gap-2 font-black">
            <i className="fa-solid fa-bolt" />
            快捷驳回标签
          </p>
          <div className="flex flex-wrap gap-3">
            {QUICK_REASONS.map((quickReason) => {
              const isSelected = reason === quickReason
              return (
                <button
                  key={quickReason}
                  type="button"
                  onClick={() => chooseQuickReason(quickReason)}
                  className={`border-2 border-black px-4 py-2 text-sm font-black ${
                    isSelected
                      ? 'translate-x-1 translate-y-1 bg-[#ff5b9a] shadow-none'
                      : 'bg-white shadow-[4px_4px_0_#111] active:translate-x-1 active:translate-y-1 active:shadow-none'
                  }`}
                  aria-pressed={isSelected}
                >
                  {quickReason}
                </button>
              )
            })}
          </div>
        </div>

        <label className="mt-6 block font-black" htmlFor="admin-reject-reason">
          自定义退回说明
        </label>
        <textarea
          id="admin-reject-reason"
          value={reason}
          onChange={(event) => {
            setReason(event.target.value)
            setValidationError('')
          }}
          maxLength={500}
          rows={5}
          placeholder="请明确说明需要修改的内容，帮助用户理解并重新提交……"
          className="mt-2 w-full resize-y border-2 border-black bg-white p-4 font-bold leading-7 outline-none shadow-[5px_5px_0_#111] focus:translate-x-1 focus:translate-y-1 focus:bg-[#d9fff8] focus:shadow-none"
        />
        <div className="mt-2 flex min-h-6 items-start justify-between gap-3 text-xs font-black">
          <span className="text-[#c61f3a]" role={validationError ? 'alert' : undefined}>
            {validationError}
          </span>
          <span>{reason.length}/500</span>
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-[0.8fr_1.4fr]">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="border-4 border-black bg-[#eee] px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="border-4 border-black bg-[#ff5b68] px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-60"
          >
            <i className={`fa-solid ${isSubmitting ? 'fa-spinner animate-spin' : 'fa-paper-plane'} mr-2`} />
            {isSubmitting ? '正在打回并发送...' : '确认打回并发送通知'}
          </button>
        </div>
      </section>
    </div>
  )
}

export default AdminRejectReasonModal
