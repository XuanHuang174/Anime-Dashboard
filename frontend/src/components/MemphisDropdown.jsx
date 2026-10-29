import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './JournalToolbar.css'

export default function MemphisDropdown({ label, children, open, onOpenChange, className = '', owner, inline = false }) {
  const triggerRef = useRef(null)
  const panelRef = useRef(null)
  const onOpenChangeRef = useRef(onOpenChange)
  onOpenChangeRef.current = onOpenChange
  const id = useId()
  const [position, setPosition] = useState({ top: 0, left: 0, maxHeight: 400 })
  useLayoutEffect(() => {
    if (!open || inline) return undefined
    const place = () => {
      const box = triggerRef.current.getBoundingClientRect()
      const width = Math.min(300, window.innerWidth - 24)
      const below = window.innerHeight - box.bottom - 20
      const above = box.top - 20
      const upwards = below < 200 && above > below
      setPosition({
        width, left: Math.max(12, Math.min(box.right - width, window.innerWidth - width - 12)),
        top: upwards ? undefined : box.bottom + 8,
        bottom: upwards ? window.innerHeight - box.top + 8 : undefined,
        maxHeight: Math.max(80, upwards ? above : below),
      })
    }
    place()
    // Inline submenu expansion changes the parent panel size, not the viewport.
    const observer = new ResizeObserver(place)
    observer.observe(panelRef.current)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      observer.disconnect()
    }
  }, [open, inline])
  useEffect(() => {
    if (!open) return undefined
    const outside = (event) => {
      if (!triggerRef.current?.contains(event.target) && !panelRef.current?.contains(event.target)
        && !(owner && event.target.closest?.('[data-menu-owner]')?.dataset.menuOwner === owner)) onOpenChangeRef.current(false)
    }
    const keyboard = (event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        // Close the deepest menu first, regardless of effect registration order.
        if (panelRef.current?.querySelector('.journal-dropdown-panel--inline:not([hidden])')) return
        event.preventDefault()
        onOpenChangeRef.current(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keyboard)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keyboard)
    }
  }, [open, owner])
  const focusFirst = () => requestAnimationFrame(() => panelRef.current?.querySelector('button:not(:disabled), a[href]')?.focus())
  const panel = <div ref={panelRef} id={id}
    className={`journal-dropdown-panel${inline ? ' journal-dropdown-panel--inline' : ''}`}
    style={inline ? undefined : position}
    hidden={!open} inert={!open} data-menu-owner={owner} aria-label={typeof label === 'string' ? label : undefined}
    onKeyDown={(event) => {
      if (event.defaultPrevented || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...panelRef.current.querySelectorAll('button:not(:disabled), a[href]')]
        .filter((button) => button.getClientRects().length > 0)
      if (!buttons.length) return
      const current = buttons.indexOf(document.activeElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      event.preventDefault(); buttons[next]?.focus()
    }}>{children}</div>
  return <>
    <button ref={triggerRef} type="button" className={`dashboard-arcade-button journal-dropdown-trigger ${className}`}
      aria-expanded={open} aria-controls={id} aria-haspopup="true"
      onClick={() => onOpenChange(!open)} onKeyDown={(event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); onOpenChange(true); focusFirst() }
      }}>{label}<span aria-hidden="true">{open ? '▴' : '▾'}</span></button>
    {inline ? panel : createPortal(panel, document.body)}
  </>
}
