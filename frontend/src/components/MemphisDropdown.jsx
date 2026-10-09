import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './JournalToolbar.css'

export default function MemphisDropdown({ label, children, open, onOpenChange, className = '', owner }) {
  const triggerRef = useRef(null)
  const panelRef = useRef(null)
  const id = useId()
  const [position, setPosition] = useState({ top: 0, left: 0, maxHeight: 400 })
  useLayoutEffect(() => {
    if (!open) return undefined
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
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open])
  useEffect(() => {
    if (!open) return undefined
    const outside = (event) => {
      if (!triggerRef.current?.contains(event.target) && !panelRef.current?.contains(event.target)
        && !(owner && event.target.closest?.('[data-menu-owner]')?.dataset.menuOwner === owner)) onOpenChange(false)
    }
    const keyboard = (event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault()
        onOpenChange(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', keyboard)
    return () => {
      document.removeEventListener('mousedown', outside)
      document.removeEventListener('keydown', keyboard)
    }
  }, [open, onOpenChange, owner])
  const focusFirst = () => requestAnimationFrame(() => panelRef.current?.querySelector('button:not(:disabled)')?.focus())
  return <>
    <button ref={triggerRef} type="button" className={`dashboard-arcade-button journal-dropdown-trigger ${className}`}
      aria-expanded={open} aria-controls={id} aria-haspopup="true"
      onClick={() => onOpenChange(!open)} onKeyDown={(event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); onOpenChange(true); focusFirst() }
      }}>{label}<span aria-hidden="true">{open ? '▴' : '▾'}</span></button>
    {createPortal(<div ref={panelRef} id={id} className="journal-dropdown-panel" style={position}
      hidden={!open} inert={!open} data-menu-owner={owner} aria-label={typeof label === 'string' ? label : undefined}
      onKeyDown={(event) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
        const buttons = [...panelRef.current.querySelectorAll('button:not(:disabled)')]
        const current = buttons.indexOf(document.activeElement)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
          : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
        event.preventDefault(); buttons[next]?.focus()
      }}>{children}</div>, document.body)}
  </>
}
