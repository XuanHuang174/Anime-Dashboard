import { useEffect, useState } from 'react'

function BackToTop({ isModalOpen }) {
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    const updateVisibility = () => {
      setIsVisible(!isModalOpen && window.scrollY > 300)
    }

    updateVisibility()
    window.addEventListener('scroll', updateVisibility, { passive: true })

    return () => {
      window.removeEventListener('scroll', updateVisibility)
    }
  }, [isModalOpen])

  return (
    <button
      id="backToTopBtn"
      type="button"
      aria-label="返回页面顶部"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      className={`fixed bottom-8 right-8 z-50 p-3 bg-indigo-600 text-white rounded-full shadow-lg shadow-indigo-500/40 hover:bg-indigo-700 hover:shadow-xl hover:-translate-y-1 active:scale-95 transition-all duration-300 ease-out flex items-center justify-center w-12 h-12 group ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10 pointer-events-none'}`}
    >
      <i className="fa-solid fa-arrow-up transform group-hover:-translate-y-1 transition-transform duration-300"></i>
    </button>
  )
}

export default BackToTop
