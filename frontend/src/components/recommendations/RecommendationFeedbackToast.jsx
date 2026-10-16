import { useEffect, useRef } from 'react'

export default function RecommendationFeedbackToast({ toast, busy, operation, onUndo, onManage, onDismiss, onReload }) {
  const timer = useRef(null), remaining = useRef(12000), started = useRef(0)
  const hover = useRef(false), focused = useRef(false), busyRef = useRef(busy)
  busyRef.current = busy
  const pause = () => {
    if (timer.current !== null) { clearTimeout(timer.current); timer.current = null; remaining.current -= Date.now() - started.current }
  }
  const resume = () => {
    if (!toast || hover.current || focused.current || busyRef.current || timer.current !== null) return
    started.current = Date.now(); timer.current = setTimeout(() => onDismiss(toast.key), Math.max(0, remaining.current))
  }
  useEffect(() => { remaining.current = 12000; resume(); return pause }, [toast?.key]) // one replaceable toast
  useEffect(() => { if (busy) pause(); else resume() }, [busy])
  if (!toast) return null
  return <aside className="rec-feedback-toast" aria-label="推荐反馈提示" onMouseEnter={() => { hover.current = true; pause() }}
    onMouseLeave={() => { hover.current = false; resume() }} onFocus={() => { focused.current = true; pause() }}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) { focused.current = false; resume() } }}>
    <p role="status">{toast.kind === 'hidden' ? `已在推荐中隐藏《${toast.item.title}》。` : '已撤销隐藏，作品已恢复参与推荐的资格。'}</p>
    <div className="rec-feedback-toast-actions">
      {toast.kind === 'hidden' && <button type="button" className="rec-text-button" disabled={busy}
        onClick={() => onUndo(toast.item, toast.targetVersion)}>撤销</button>}
      <button type="button" className="rec-text-button" onClick={onManage}>查看已隐藏作品</button>
      <button type="button" className="rec-text-button" aria-label="关闭反馈提示" disabled={busy} onClick={() => onDismiss(toast.key)}>关闭</button>
    </div>
    {operation?.message && operation.status !== 'saving' && <p role="alert">{operation.message}
      {operation.status === 'conflict' && <button type="button" className="rec-text-button" onClick={onReload}>重新读取反馈状态</button>}</p>}
  </aside>
}
