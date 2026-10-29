import { useContext, useEffect, useState } from 'react'
import { SeriesOverlayContext } from '../hooks/useSeriesJournalReturn.js'

function BackToTop({ isModalOpen, theme = 'default', scrollRootRef }) {
  const [isVisible, setIsVisible] = useState(false)
  const overlay = useContext(SeriesOverlayContext)
  const blocked = isModalOpen || overlay && !scrollRootRef

  useEffect(() => {
    const target = scrollRootRef?.current?.closest('dialog')?.querySelector('.rec-dialog-body') || window
    const updateVisibility = () => {
      setIsVisible(!blocked && (target === window ? window.scrollY : target.scrollTop) > 300)
    }

    updateVisibility()
    target.addEventListener('scroll', updateVisibility, { passive: true })

    return () => {
      target.removeEventListener('scroll', updateVisibility)
    }
  }, [blocked, scrollRootRef])

  return (
    <button
      type="button"
      aria-label="返回页面顶部"
      aria-hidden={!isVisible}
      tabIndex={isVisible ? 0 : -1}
      onClick={() => (scrollRootRef?.current?.closest('dialog')?.querySelector('.rec-dialog-body') || window).scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })}
      className={`${theme === 'memphis' ? 'memphis-back-to-top' : 'bg-indigo-600 text-white rounded-full shadow-lg shadow-indigo-500/40 hover:bg-indigo-700 hover:shadow-xl hover:-translate-y-1 active:scale-95'} group fixed bottom-8 right-8 z-50 flex h-12 w-12 items-center justify-center p-3 transition-all duration-300 ease-out ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10 pointer-events-none'}`}
    >
      <i className="fa-solid fa-arrow-up text-lg transform group-hover:-translate-y-1 transition-transform duration-300"></i>
    </button>
  )
}

export default BackToTop
