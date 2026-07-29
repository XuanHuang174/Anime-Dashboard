import { useEffect, useRef } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'

gsap.registerPlugin(useGSAP)

function MemphisToast({ toast, onDismiss }) {
  const toastRef = useRef(null)
  const closingRef = useRef(false)

  const { contextSafe } = useGSAP(() => {
    if (!toastRef.current) return undefined
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set(toastRef.current, { autoAlpha: 1 })
      return undefined
    }

    const animation = gsap.fromTo(
      toastRef.current,
      { x: 90, scale: 0.72, rotation: 4, autoAlpha: 0 },
      {
        x: 0,
        scale: 1,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.48,
        ease: 'elastic.out(1, 0.58)',
      },
    )
    return () => animation.kill()
  }, { scope: toastRef })

  const dismiss = contextSafe(() => {
    if (closingRef.current) return
    closingRef.current = true
    if (
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
      || !toastRef.current
    ) {
      onDismiss(toast.id)
      return
    }
    gsap.to(toastRef.current, {
      x: 80,
      y: 14,
      scale: 0.7,
      rotation: -5,
      autoAlpha: 0,
      duration: 0.42,
      ease: 'elastic.in(1, 0.65)',
      onComplete: () => onDismiss(toast.id),
    })
  })

  useEffect(() => {
    const messageLength = String(toast.message || '').length
    const displayDuration = Math.min(8000, Math.max(3500, 2800 + messageLength * 35))
    const timer = window.setTimeout(dismiss, displayDuration)
    return () => window.clearTimeout(timer)
  }, [dismiss, toast.message])

  return (
    <div
      ref={toastRef}
      role={toast.type === 'error' ? 'alert' : 'status'}
      className={`pointer-events-auto flex w-full items-start gap-3 border-[3px] border-black px-5 py-4 font-black text-black shadow-[6px_6px_0_#111] ${
        toast.type === 'error' ? 'bg-[#ff6b6b]' : 'bg-[#4ecdc4]'
      }`}
    >
      <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center border-2 border-black bg-white shadow-[2px_2px_0_#111]">
        <i className={`fa-solid ${
          toast.type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-check'
        } text-lg`} />
      </span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm leading-6 [overflow-wrap:anywhere]">
        {toast.message}
      </span>
      <button
        type="button"
        onClick={dismiss}
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center border-2 border-black bg-white text-xs shadow-[2px_2px_0_#111] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none"
        aria-label="关闭提示"
      >
        <i className="fa-solid fa-xmark" />
      </button>
    </div>
  )
}

function MemphisToastStack({ toasts, onDismiss }) {
  if (!toasts.length) return null

  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-[600] flex w-[min(30rem,calc(100vw-3rem))] flex-col items-end gap-3">
      {toasts.map((toast) => (
        <MemphisToast key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

export default MemphisToastStack
