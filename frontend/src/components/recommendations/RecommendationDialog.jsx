import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

// Native modal dialog supplies top-layer stacking, inert background and focus trap.
export default function RecommendationDialog({ title, children, onClose, kind = 'detail', busy = false,
  resourceOpen = false, resourcePanel, onResourceClose, resourceTriggerRef, headerActions }) {
  const ref = useRef(null)
  const wasResourceOpen = useRef(false)
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 1023px)').matches)
  const titleId = useId()
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1023px)')
    const changed = () => setCompact(media.matches)
    changed(); media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [])
  useLayoutEffect(() => {
    if (wasResourceOpen.current && !resourceOpen && resourceTriggerRef?.current?.isConnected) {
      resourceTriggerRef.current.focus({ preventScroll: true })
    }
    wasResourceOpen.current = resourceOpen
  }, [resourceOpen, resourceTriggerRef])
  useLayoutEffect(() => {
    if (compact && resourceOpen && ref.current?.querySelector('.rec-dialog-main')?.contains(document.activeElement)) {
      ref.current.querySelector('.rec-dialog-resource input')?.focus({ preventScroll: true })
    }
  }, [compact, resourceOpen])
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
      else document.querySelector('.memphis-rec-container .rec-nav-tools button')?.focus()
    }
  }, [])
  function keepResourceFocus(event) {
    if (!resourcePanel || event.key !== 'Tab') return
    const targets = [...ref.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex]')]
      .filter(node => node.tabIndex >= 0 && !node.closest('[inert]') && node.getClientRects().length > 0
        && getComputedStyle(node).visibility !== 'hidden')
    const first = targets[0], last = targets.at(-1)
    if (!first) { event.preventDefault(); ref.current.focus(); return }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  return <dialog ref={ref} className={`rec-dialog rec-dialog--${kind}${resourceOpen ? ' rec-dialog--resource-open' : ''}`} aria-labelledby={titleId}
    onKeyDown={keepResourceFocus}
    onCancel={(event) => { event.preventDefault(); if (!busy) { if (resourceOpen) onResourceClose?.(); else onClose() } }}
    onClick={(event) => {
      const rect = ref.current.getBoundingClientRect()
      if (event.target === ref.current && (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom) && !busy) onClose()
    }}>
    <header className="rec-dialog-header"><h2 id={titleId}>{title}</h2>
      {headerActions}
      <button type="button" className="rec-button rec-close" disabled={busy} data-initial-focus aria-label="关闭面板" onClick={onClose}>×</button></header>
    {resourcePanel ? <div className="rec-dialog-workspace">
      <div className="rec-dialog-main" inert={resourceOpen && compact} aria-hidden={resourceOpen && compact}>
        <div className="rec-dialog-body">{children}</div>
      </div>
      <div className="rec-dialog-resource" inert={!resourceOpen} aria-hidden={!resourceOpen}>{resourcePanel}</div>
    </div> : <div className="rec-dialog-body">{children}</div>}
  </dialog>
}
