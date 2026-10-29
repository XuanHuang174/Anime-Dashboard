import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import gsap from 'gsap'
import { createRecommendationMotion } from '../../utils/recommendationMotion.js'
import { lockDialogBody } from '../../utils/dialogBodyLock.js'

// Native modal dialog supplies top-layer stacking, inert background and focus trap.
export default function RecommendationDialog({ title, children, onClose, kind = 'detail', busy = false,
  resourceOpen = false, resourcePanel, onResourceClose, resourceTriggerRef, headerActions, fallbackFocusRef }) {
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
    const releaseBody = lockDialogBody(document)
    node.showModal()
    node.querySelector('[data-initial-focus]')?.focus()
    const motion = createRecommendationMotion({ root: node, gsap, media: window.matchMedia('(prefers-reduced-motion: reduce)') })
    motion.enterDialog(kind)
    return () => {
      motion.dispose()
      node.close()
      releaseBody()
      if (previousFocus?.isConnected) previousFocus.focus()
      else if (document.activeElement === document.body || !document.activeElement?.isConnected) {
        (fallbackFocusRef?.current || document.querySelector('.memphis-rec-container .rec-nav-tools button'))?.focus()
      }
    }
  }, [])
  function keepResourceFocus(event) {
    if (event.key === 'Escape') event.stopPropagation() // Do not close an underlying journal preview.
    // Apply the boundary to every modal, including the hidden-works drawer.
    // Some browser top-layer transitions briefly hand Tab back to browser chrome.
    if (event.key !== 'Tab') return
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
    onCancel={(event) => { event.preventDefault(); event.stopPropagation(); if (!busy) { if (resourceOpen) onResourceClose?.(); else onClose() } }}
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
