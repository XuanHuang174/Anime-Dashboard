import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

gsap.registerPlugin(useGSAP)

function FeaturedActionConfirmModal({ action, onClose, onConfirm }) {
  const overlayRef = useRef(null)
  const panelRef = useRef(null)
  const closingRef = useRef(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const isRemoval = action?.type === 'removal'

  const { contextSafe } = useGSAP(() => {
    if (!action || !overlayRef.current || !panelRef.current) return undefined
    closingRef.current = false
    setIsSubmitting(false)
    const timeline = gsap.timeline()
      .fromTo(
        overlayRef.current,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.15, ease: 'power1.out' },
      )
      .fromTo(
        panelRef.current,
        { y: -45, scale: 0.72, rotation: -3, autoAlpha: 0 },
        {
          y: 0,
          scale: 1,
          rotation: 0,
          autoAlpha: 1,
          duration: 0.45,
          ease: 'back.out(1.7)',
        },
        0.03,
      )
    return () => timeline.kill()
  }, {
    scope: overlayRef,
    dependencies: [action?.type, action?.item?.id],
    revertOnUpdate: true,
  })

  const closeWithAnimation = contextSafe(() => {
    if (closingRef.current || isSubmitting) return
    closingRef.current = true
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      onClose()
      return
    }
    gsap.to(panelRef.current, {
      y: 42,
      scale: 0.86,
      autoAlpha: 0,
      duration: 0.22,
      ease: 'power3.in',
      onComplete: onClose,
    })
    gsap.to(overlayRef.current, {
      autoAlpha: 0,
      duration: 0.16,
      delay: 0.08,
      ease: 'power2.in',
    })
  })

  const confirmAction = async () => {
    if (isSubmitting) return
    setIsSubmitting(true)
    const succeeded = await onConfirm(action)
    setIsSubmitting(false)
    if (succeeded) {
      closingRef.current = false
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        onClose()
      } else {
        gsap.to(panelRef.current, {
          y: 42,
          scale: 0.86,
          autoAlpha: 0,
          duration: 0.22,
          ease: 'power3.in',
          onComplete: onClose,
        })
      }
    }
  }

  useEffect(() => {
    if (!action) return undefined
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') closeWithAnimation()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [action, closeWithAnimation])

  if (!action) return null

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[140] flex items-center justify-center bg-zinc-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="featuredConfirmTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeWithAnimation()
      }}
    >
      <section
        ref={panelRef}
        className={`w-full max-w-lg border-4 border-black bg-[#fff5ee] shadow-[12px_12px_0_#111] ${
          isRemoval ? 'selection:bg-[#ff6b6b]' : 'selection:bg-[#4ecdc4]'
        }`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={`flex items-center justify-between border-b-4 border-black px-5 py-4 ${
          isRemoval ? 'bg-[#ff6b6b]' : 'bg-[#ffe66d]'
        }`}>
          <div>
            <span className="border-2 border-black bg-white px-2 py-0.5 text-[10px] font-black tracking-[0.16em] shadow-[2px_2px_0_#111]">
              {isRemoval ? 'DANGER ACTION' : 'FEATURED ENTRY'}
            </span>
            <h2 id="featuredConfirmTitle" className="mt-3 text-2xl font-black">
              {isRemoval ? '确认申请撤下？' : '确认提交至精选专栏？'}
            </h2>
          </div>
          <i className={`fa-solid ${isRemoval ? 'fa-triangle-exclamation' : 'fa-paper-plane'} text-4xl`} />
        </div>

        <div className="p-6">
          <p className="border-l-[7px] border-black bg-white px-4 py-3 text-sm font-bold leading-7 shadow-[4px_4px_0_#4ecdc4]">
            {isRemoval
              ? `提交后，${action.item.title}会进入撤下审核流程，在处理完成前无法再次提交其他作品。`
              : `将${action.item.title}当前的海报、评分、标签和个人长评制作成独立快照并提交审核。`
            }
          </p>
          <div className="mt-6 grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={closeWithAnimation}
              disabled={isSubmitting}
              className="border-[3px] border-black bg-white px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={confirmAction}
              disabled={isSubmitting}
              className={`border-[3px] border-black px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50 ${
                isRemoval ? 'bg-[#ff6b6b]' : 'bg-[#4ecdc4]'
              }`}
            >
              <i className={`fa-solid ${isSubmitting ? 'fa-spinner animate-spin' : isRemoval ? 'fa-box-archive' : 'fa-paper-plane'} mr-2`} />
              {isSubmitting ? '处理中…' : isRemoval ? '确认申请撤下' : '确认提交'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}

export default FeaturedActionConfirmModal
