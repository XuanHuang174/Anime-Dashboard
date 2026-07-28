import { useEffect, useState } from 'react'
import api from '../api/api.js'

function Header({ onGetStarted, onViewFeatured }) {
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
    <header className="relative overflow-hidden border-b-4 border-black bg-[#ff6b6b] text-black">
      <div
        className="pointer-events-none absolute inset-0 opacity-20"
        style={{
          backgroundImage: 'linear-gradient(#111 2px, transparent 2px), linear-gradient(90deg, #111 2px, transparent 2px)',
          backgroundSize: '38px 38px',
        }}
      />
      <div className="memphis-header-piece pointer-events-none absolute -right-10 -top-14 h-52 w-52 rounded-full border-[18px] border-black bg-[#4ecdc4] shadow-[8px_8px_0_0_#ffe66d]">
        <i className="fa-solid fa-star absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rotate-12 text-7xl text-[#ffe66d]"></i>
      </div>

      <div className="relative z-10 mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8 lg:py-12">
        <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div className="max-w-3xl">
            <span className="memphis-header-piece inline-flex -rotate-1 items-center border-[3px] border-black bg-[#ffe66d] px-3 py-1.5 text-xs font-black uppercase tracking-[0.16em] shadow-[4px_4px_0_0_#111]">
              <i className="fa-solid fa-heart mr-2"></i>
              个人手账 / 追番记录
            </span>

            <h1
              className="memphis-header-piece mt-5 flex items-center gap-4 text-3xl font-black tracking-[-0.04em] sm:text-5xl"
              style={{ fontFamily: '"Arial Black", Impact, sans-serif' }}
            >
              <span className="flex h-14 w-14 shrink-0 -rotate-3 items-center justify-center overflow-hidden border-4 border-black bg-white text-xl shadow-[6px_6px_0_0_#4ecdc4] sm:h-16 sm:w-16">
                {showcaseProfile?.avatar ? (
                  <img src={showcaseProfile.avatar} alt={`${displayName}的头像`} className="h-full w-full object-cover" />
                ) : (
                  <i className="fa-solid fa-star text-[#ff6b6b]"></i>
                )}
              </span>
              <span className="inline-block bg-black px-3 py-2 text-white">{displayName} 的番剧汇总</span>
            </h1>

            <p className="memphis-header-piece mt-5 max-w-2xl border-l-[8px] border-black bg-white px-4 py-3 text-sm font-bold leading-6 shadow-[6px_6px_0_0_#111] sm:text-base">
              {showcaseProfile?.showcase_subtitle?.trim()
                || '精心收录 2007 年至今的优质动画作品，包含详细的题材分类、季度划分与主观评价。'}
            </p>
          </div>

          <div className="memphis-header-piece mr-5 flex flex-wrap gap-3">
            {onViewFeatured && (
              <button
                type="button"
                onClick={onViewFeatured}
                className="memphis-pressable flex items-center gap-2 self-center border-[3px] border-black bg-[#ff8fab] px-4 py-3 text-sm font-black text-black shadow-[5px_5px_0_#111]"
              >
                <i className="fa-solid fa-award"></i>
                浏览精选专栏
              </button>
            )}
            <button
              id="exportCsvBtn"
              type="button"
              onClick={onGetStarted}
              className="memphis-primary-cta memphis-pressable flex items-center gap-3 border-4 border-black bg-[#ffe66d] px-6 py-4 text-left font-black text-black shadow-[6px_6px_0_0_#111]"
            >
              <span className="flex h-12 w-12 shrink-0 items-center justify-center border-[3px] border-black bg-[#ff6b6b] text-xl shadow-[3px_3px_0_#111]">
                <i className="cta-spark-icon fa-solid fa-wand-magic-sparkles"></i>
              </span>
              <span className="flex min-w-0 flex-1 flex-col leading-none">
                <span className="flex items-center gap-2 text-[10px] tracking-[0.18em]">
                  START YOUR JOURNAL
                  <span className="border-2 border-black bg-white px-1.5 py-0.5 text-[8px] tracking-normal">FREE</span>
                </span>
                <span className="mt-2 text-base sm:text-lg">设计自己的个人手账！</span>
                <span className="mt-2 text-[10px] font-black tracking-[0.08em] text-zinc-700">
                  注册 · 登录 · 跨设备同步
                </span>
              </span>
              <span className="cta-arrow-box ml-1 flex h-9 w-9 shrink-0 items-center justify-center border-[3px] border-black bg-white shadow-[3px_3px_0_#111]">
                <i className="fa-solid fa-arrow-right"></i>
              </span>
            </button>
          </div>
        </div>
      </div>
    </header>
  )
}

export default Header
