import { useEffect, useId, useRef } from 'react'

// Native modal dialog supplies top-layer stacking, inert background and focus trap.
export default function RecommendationDialog({ title, children, onClose, kind = 'detail' }) {
  const ref = useRef(null)
  const titleId = useId()
  useEffect(() => {
    const node = ref.current
    const previousFocus = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    node.showModal()
    node.querySelector('[data-initial-focus]')?.focus()
    return () => {
      node.close()
      document.body.style.overflow = previousOverflow
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])
  return <dialog ref={ref} className={`rec-dialog rec-dialog--${kind}`} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose() }}
    onClick={(event) => {
      const rect = ref.current.getBoundingClientRect()
      if (event.target === ref.current && (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom)) onClose()
    }}>
    <header className="rec-dialog-header"><h2 id={titleId}>{title}</h2>
      <button type="button" className="rec-button rec-close" data-initial-focus aria-label="关闭面板" onClick={onClose}>×</button></header>
    <div className="rec-dialog-body">{children}</div>
  </dialog>
}
