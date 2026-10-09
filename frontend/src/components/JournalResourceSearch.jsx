import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import AnimeResourceDrawer from './AnimeResourceDrawer.jsx'

// A separate mount per launch isolates global/row searches from detail-modal searches.
export default function JournalResourceSearch({ title, autoSearch, onClose, showToast }) {
  const [visible, setVisible] = useState(false)
  const closingRef = useRef(null)
  const close = useCallback(() => {
    if (closingRef.current) return
    setVisible(false)
    closingRef.current = window.setTimeout(onClose, 320)
  }, [onClose])
  useEffect(() => {
    const previousFocus = document.activeElement
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const frame = requestAnimationFrame(() => setVisible(true))
    const escape = (event) => { if (event.key === 'Escape') close() }
    document.addEventListener('keydown', escape)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(closingRef.current)
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', escape)
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
    }
  }, [close])
  return createPortal(<div className="journal-resource-overlay" role="dialog" aria-modal="true" aria-label="全局番剧资源搜索"
    onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <AnimeResourceDrawer id="journalResourceDrawer" open={visible} title={title} autoSearch={autoSearch}
      onClose={close} showToast={showToast} />
  </div>, document.body)
}
