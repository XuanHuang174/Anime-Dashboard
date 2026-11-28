import { useEffect, useId, useRef } from 'react'
import { lockDialogBody } from '../utils/dialogBodyLock.js'

export default function CurationDialog({ title, onClose, busy, children, footer, drawer = false, returnFocus }) {
  const ref = useRef(null), titleId = useId()
  useEffect(() => {
    const previous = returnFocus || document.activeElement, release = lockDialogBody(document), node = ref.current
    node.showModal(); node.querySelector('[data-initial-focus], input, textarea')?.focus()
    return () => { node.close(); release(); if (previous?.isConnected) previous.focus({ preventScroll: true }) }
  }, [])
  return <dialog ref={ref} className={`watch-plans curation-dialog${drawer ? ' curation-drawer' : ''}`} aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose() }}
    onPointerDown={event => { const box = ref.current.getBoundingClientRect(); ref.current.dataset.outside = String(event.target === ref.current && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) }}
    onClick={event => { const box = ref.current.getBoundingClientRect(); if (!busy && ref.current.dataset.outside === 'true' && event.target === ref.current && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) onClose(); delete ref.current.dataset.outside }}
    onKeyDown={event => {
      if (event.key === 'Escape') event.stopPropagation()
      if (event.key !== 'Tab') return
      const nodes = [...ref.current.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter(node => node.getClientRects().length)
      if (!nodes.length) { event.preventDefault(); return }
      if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1).focus() }
      else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0].focus() }
    }}>
    <header><div><span className="plan-sticker">MY COLLECTION</span><h2 id={titleId}>{title}</h2></div><button type="button" aria-label="关闭片单面板" disabled={busy} onClick={onClose}>×</button></header>
    <div className="curation-dialog-body">{children}</div>{footer && <footer>{footer}</footer>}
  </dialog>
}
