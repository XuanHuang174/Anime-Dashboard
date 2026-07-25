import { useEffect, useState } from 'react'
import api from '../api/api.js'

function Header({ onGetStarted }) {
  const [showcaseProfile, setShowcaseProfile] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    api.get('showcase-profile/', { signal: controller.signal })
      .then((response) => setShowcaseProfile(response.data))
      .catch((error) => {
        if (error.code !== 'ERR_CANCELED') setShowcaseProfile(null)
      })
    return () => controller.abort()
  }, [])

  const displayName = showcaseProfile?.nickname?.trim()
    || showcaseProfile?.username
    || 'XuanHuang'

  return (
    <div className="bg-gradient-to-r from-pink-400 via-purple-400 to-indigo-500 text-white shadow-lg relative overflow-hidden">
      <div className="absolute top-0 right-0 -mt-10 -mr-10 text-white/10 text-[15rem] rotate-12 pointer-events-none">
        <i className="fa-solid fa-star"></i>
      </div>
      <div className="max-w-7xl mx-auto px-4 py-8 sm:px-6 lg:px-8 relative z-10">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <span className="bg-white/20 text-white text-xs font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider inline-flex items-center backdrop-blur-sm shadow-sm">
              <i className="fa-solid fa-heart mr-1.5 animate-pulse"></i> 个人手账 / 追番记录
            </span>
            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight mt-3 flex items-center gap-3">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl border-2 border-white/70 bg-white/20 text-xl shadow-lg shadow-indigo-700/20 ring-2 ring-white/20 backdrop-blur-sm sm:h-14 sm:w-14">
                {showcaseProfile?.avatar ? (
                  <img src={showcaseProfile.avatar} alt={`${displayName}的头像`} className="h-full w-full object-cover" />
                ) : (
                  <i className="fa-solid fa-star text-yellow-300"></i>
                )}
              </span>
              <span>{displayName} 的番剧汇总</span>
            </h1>
            <p className="mt-2 text-pink-50 max-w-xl text-sm sm:text-base opacity-90">
              {showcaseProfile?.showcase_subtitle?.trim() || '精心收录 2007 年至今的优质动漫作品，包含详细的题材分类、季度划分与主观评价等。'}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <button
              id="exportCsvBtn"
              type="button"
              onClick={onGetStarted}
              className="bg-white text-indigo-600 hover:bg-indigo-50 hover:shadow-lg active:scale-95 font-semibold px-5 py-3 rounded-xl shadow-md transition-all duration-200 flex items-center gap-2 border border-transparent"
            >
              <i className="fa-solid fa-wand-magic-sparkles text-lg"></i>
              设计自己的个人手账！
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Header
