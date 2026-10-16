import { useEffect, useId, useRef, useState } from 'react'

export default function RecommendationFeedbackMenu({ item, onHide, disabled, operation, onReload }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null), trigger = useRef(null), action = useRef(null), id = useId()
  useEffect(() => {
    if (!open) return undefined
    action.current?.focus()
    const outside = event => { if (!ref.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('focusin', outside)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside) }
  }, [open])
  return <div className="rec-feedback-menu" ref={ref} onKeyDown={event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus() }
  }}>
    <button ref={trigger} type="button" className="rec-feedback-trigger" aria-label={`${item.title}的更多操作`}
      aria-expanded={open} aria-controls={id} aria-haspopup="menu" disabled={disabled}
      onClick={() => setOpen(value => !value)}>⋯</button>
    {open && <div className="rec-feedback-options" role="menu" id={id}>
      <button ref={action} role="menuitem" type="button" disabled={disabled}
        onClick={() => { setOpen(false); trigger.current?.focus(); onHide(item) }}>不感兴趣</button>
    </div>}
    {operation?.status === 'saving' && <span className="rec-feedback-message" role="status">正在处理…</span>}
    {operation?.message && operation.status !== 'saving' && <div className="rec-feedback-message" role="alert">
      <p>{operation.message}</p>{operation.status === 'conflict' && <button type="button" className="rec-text-button" onClick={onReload}>重新读取反馈状态</button>}</div>}
  </div>
}
