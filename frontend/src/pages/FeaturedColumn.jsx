import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import BackToTop from '../components/BackToTop.jsx'
import DetailModal from '../components/DetailModal.jsx'
import FilterBar from '../components/FilterBar.jsx'
import {
  comparePeriod,
  getCoverSrc,
  getTagClass,
} from '../utils/animeDisplay.js'
import {
  formatAiringPeriod,
  mapFeaturedSubmission,
} from '../utils/featuredDisplay.js'
import { matchesCustomQuickFilter } from '../utils/quickFilter.js'

gsap.registerPlugin(useGSAP)

let hasPlayedFeaturedEntrance = false

function getScoreTextClass(score) {
  if (score >= 9.9) {
    return 'bg-gradient-to-r from-indigo-700 via-purple-700 to-fuchsia-600 bg-clip-text text-transparent'
  }
  if (score >= 9.5) return 'text-red-600'
  if (score >= 9) return 'text-orange-600'
  return 'text-black'
}

function FeaturedColumn() {
  const navigate = useNavigate()
  const pageRef = useRef(null)
  const cardsRef = useRef(null)
  const hasAnimatedCards = useRef(hasPlayedFeaturedEntrance)
  const [db, setDb] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [selectedItem, setSelectedItem] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('all')
  const [yearFilter, setYearFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('date-desc')
  const [typeFilter, setTypeFilter] = useState('all')
  const [featuredQuickFilters, setFeaturedQuickFilters] = useState([])
  const [activeCustomFilterId, setActiveCustomFilterId] = useState(null)

  useEffect(() => {
    const controller = new AbortController()

    const loadFeaturedColumns = async () => {
      setIsLoading(true)
      setLoadError('')
      try {
        const filtersPromise = api.get('featured-filters/', {
          params: { page_size: 100 },
          signal: controller.signal,
        })
        const submissions = []
        let page = 1
        let hasNext = true

        while (hasNext) {
          const response = await api.get('featured/', {
            params: { page, page_size: 100 },
            signal: controller.signal,
          })
          const payload = response.data
          if (Array.isArray(payload)) {
            submissions.push(...payload)
            hasNext = false
          } else {
            submissions.push(...(payload.results ?? []))
            hasNext = Boolean(payload.next)
            page += 1
          }
        }

        const filtersResponse = await filtersPromise
        const filtersPayload = filtersResponse.data
        setDb(submissions.map(mapFeaturedSubmission))
        setFeaturedQuickFilters(
          Array.isArray(filtersPayload)
            ? filtersPayload
            : filtersPayload.results ?? [],
        )
      } catch (error) {
        if (error.code !== 'ERR_CANCELED') {
          setLoadError('精选专栏暂时未能加载，请稍后再试。')
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadFeaturedColumns()
    return () => controller.abort()
  }, [])

  const availableTags = useMemo(
    () => [...new Set(db.flatMap((item) => item.tags))].sort(),
    [db],
  )
  const availableYears = useMemo(
    () => [...new Set(
      db
        .map((item) => item.rawSubmission.year)
        .filter(Boolean)
        .map(String),
    )].sort((a, b) => Number(b) - Number(a)),
    [db],
  )

  const filteredColumns = useMemo(() => {
    const search = searchQuery.trim().toLowerCase()
    const activeQuickFilter = featuredQuickFilters.find(
      (filter) => filter.id === activeCustomFilterId,
    )
    return db
      .filter((item) => {
        const matchesName = !search
          || item.title.toLowerCase().includes(search)
          || item.japaneseTitle.toLowerCase().includes(search)
        const matchesTag = tagFilter === 'all' || item.tags.includes(tagFilter)
        const matchesYear = yearFilter === 'all'
          || String(item.rawSubmission.year) === yearFilter
        const matchesStatus = statusFilter === 'all' || item.status === statusFilter
        return matchesName
          && matchesTag
          && matchesYear
          && matchesStatus
          && matchesCustomQuickFilter(item, activeQuickFilter)
      })
      .sort((left, right) => {
        if (sortOrder === 'score-desc') return (right.score ?? -1) - (left.score ?? -1)
        if (sortOrder === 'score-asc') return (left.score ?? 11) - (right.score ?? 11)
        if (sortOrder === 'date-asc') return comparePeriod(left.period, right.period)
        return comparePeriod(right.period, left.period)
      })
  }, [
    activeCustomFilterId,
    db,
    featuredQuickFilters,
    searchQuery,
    sortOrder,
    statusFilter,
    tagFilter,
    yearFilter,
  ])

  useGSAP(() => {
    if (isLoading || hasAnimatedCards.current || !filteredColumns.length) return undefined
    hasAnimatedCards.current = true
    hasPlayedFeaturedEntrance = true

    const cards = gsap.utils.toArray('.featured-column-card')
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set(cards, { clearProps: 'all' })
      return undefined
    }

    gsap.set(cards, { willChange: 'transform, opacity' })
    const animation = gsap.fromTo(
      cards,
      { y: 54, scale: 0.92, rotation: -1.5, autoAlpha: 0 },
      {
        y: 0,
        scale: 1,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.52,
        stagger: 0.1,
        ease: 'back.out(1.5)',
        onComplete: () => gsap.set(cards, { clearProps: 'transform,opacity,visibility,willChange' }),
      },
    )
    return () => animation.kill()
  }, {
    scope: cardsRef,
    dependencies: [isLoading],
  })

  return (
    <main
      ref={pageRef}
      className="memphis-public memphis-featured relative min-h-screen overflow-hidden bg-[#fff5ee] pb-10 text-black"
    >
      <div className="memphis-grid-texture pointer-events-none absolute inset-0" aria-hidden="true" />

      <header className="featured-column-header relative z-10 overflow-hidden border-b-4 border-black">
        <div className="featured-header-yellow absolute inset-y-0 left-0 w-[62%] bg-[#ffe66d]" />
        <div className="featured-header-magenta absolute inset-y-0 right-0 w-[48%] -skew-x-12 border-l-4 border-black bg-[#ff2f92]" />
        <div className="absolute -right-10 -top-16 h-56 w-56 rounded-full border-[18px] border-black bg-[#4ecdc4] shadow-[9px_9px_0_#111]" />
        <div className="absolute bottom-3 right-[24%] h-20 w-20 rotate-12 border-4 border-black bg-white shadow-[6px_6px_0_#111]" />

        <div className="relative mx-auto max-w-[1500px] px-4 py-8 sm:px-6 lg:px-8 lg:py-11">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="mb-7 inline-flex items-center gap-2 rounded-full border-4 border-black bg-white px-5 py-2.5 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            <i className="fa-solid fa-arrow-left"></i>
            返回展示主界面
          </button>

          <div className="max-w-4xl">
            <span className="inline-flex -rotate-2 border-[3px] border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black tracking-[0.2em] shadow-[4px_4px_0_#111]">
              EDITOR&apos;S CHOICE / CURATED STORIES
            </span>
            <h1 className="mt-5 flex flex-wrap items-end gap-3 text-5xl font-black tracking-[-0.06em] sm:text-7xl lg:text-8xl">
              <span className="border-4 border-black bg-black px-4 py-2 text-white">精选</span>
              <span className="border-4 border-black bg-[#ff8fab] px-4 py-2 text-black shadow-[8px_8px_0_#111]">专栏</span>
            </h1>
            <p className="mt-6 max-w-2xl border-l-[10px] border-black bg-white px-5 py-3 text-sm font-black leading-7 shadow-[6px_6px_0_#4ecdc4] sm:text-base">
              不追求千篇一律的标准答案。这里收录来自手账主理人的真实观看体验与长篇表达。
            </p>
          </div>
        </div>
      </header>

      <div className="relative z-20 mx-auto max-w-[1500px] px-4 sm:px-6 lg:px-8">
        <div className="-mt-1">
          <FilterBar
            db={db}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            tagFilter={tagFilter}
            setTagFilter={setTagFilter}
            yearFilter={yearFilter}
            setYearFilter={setYearFilter}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            sortOrder={sortOrder}
            setSortOrder={setSortOrder}
            typeFilter={typeFilter}
            setTypeFilter={setTypeFilter}
            view="grid"
            setView={() => {}}
            filteredCount={filteredColumns.length}
            customQuickFilters={featuredQuickFilters}
            activeCustomFilterId={activeCustomFilterId}
            setActiveCustomFilterId={setActiveCustomFilterId}
            availableTags={availableTags}
            availableYears={availableYears}
            enablePosterViewHint={false}
            showViewToggle={false}
            theme="memphis"
          />
        </div>

        {isLoading && (
          <div className="border-4 border-black bg-[#4ecdc4] p-8 text-center text-lg font-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-spinner mr-3 animate-spin"></i>
            正在翻阅精选手账……
          </div>
        )}

        {!isLoading && loadError && (
          <div role="alert" className="border-4 border-black bg-[#ff6b6b] p-8 text-center font-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-triangle-exclamation mr-2"></i>
            {loadError}
          </div>
        )}

        {!isLoading && !loadError && filteredColumns.length === 0 && (
          <div className="border-4 border-black bg-white p-12 text-center shadow-[8px_8px_0_#ff2f92]">
            <span className="mx-auto mb-5 flex h-16 w-16 rotate-3 items-center justify-center border-4 border-black bg-[#ffe66d] text-2xl shadow-[5px_5px_0_#111]">
              <i className="fa-solid fa-book-open"></i>
            </span>
            <h2 className="text-2xl font-black">暂时没有匹配的精选专栏</h2>
            <p className="mt-2 text-sm font-bold text-zinc-600">换个关键词或筛选条件再看看。</p>
          </div>
        )}

        {!isLoading && !loadError && filteredColumns.length > 0 && (
          <div ref={cardsRef} className="grid grid-cols-1 gap-8 xl:grid-cols-2">
            {filteredColumns.map((item, index) => {
              const score = item.score ?? 0
              return (
                <article
                  key={item.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedItem(item)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      setSelectedItem(item)
                    }
                  }}
                  className={`featured-column-card group grid min-h-[430px] cursor-pointer overflow-hidden border-4 border-black bg-white shadow-[10px_10px_0_#111] outline-none active:translate-x-[7px] active:translate-y-[7px] active:shadow-none md:grid-cols-[42%_58%] ${index % 2 ? 'xl:-rotate-[0.35deg]' : 'xl:rotate-[0.35deg]'}`}
                >
                  <div className="relative min-h-[350px] overflow-hidden border-b-4 border-black bg-[#ffe66d] md:min-h-0 md:border-b-0 md:border-r-4">
                    <img
                      src={getCoverSrc(item)}
                      alt={`${item.title}海报`}
                      loading="lazy"
                      decoding="async"
                      className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.025]"
                    />
                    <span className={`memphis-status memphis-status-${item.status} absolute bottom-4 left-4 px-3 py-1 text-xs font-black`}>
                      {item.statusLabel}
                    </span>
                  </div>

                  <div className="flex min-w-0 flex-col p-5 sm:p-6">
                    <div className="mb-4 flex w-fit -rotate-1 items-center gap-2 border-[3px] border-black bg-[#4ecdc4] p-1.5 pr-3 text-xs font-black shadow-[4px_4px_0_#111]">
                      {item.authorAvatar ? (
                        <img
                          src={item.authorAvatar}
                          alt={`${item.authorName}的头像`}
                          className="h-9 w-9 border-2 border-black bg-white object-cover"
                        />
                      ) : (
                        <span className="flex h-9 w-9 items-center justify-center border-2 border-black bg-white">
                          <i className="fa-solid fa-user-pen"></i>
                        </span>
                      )}
                      <span>由 {item.authorName} 撰写</span>
                    </div>

                    <h2 className="text-2xl font-black leading-tight tracking-tight sm:text-3xl">
                      {item.title}
                    </h2>
                    <p className="mt-1 line-clamp-1 text-xs font-bold text-zinc-500">
                      {item.japaneseTitle || ' '}
                    </p>

                    <div className="mt-4 grid grid-cols-[auto_1fr] items-center gap-3">
                      <span className="border-[3px] border-black bg-[#ffe66d] px-3 py-1 text-sm font-black shadow-[3px_3px_0_#111]">
                        {formatAiringPeriod(item.period)}
                      </span>
                      <div className="border-[3px] border-black bg-[#fff5ee] p-2 shadow-[3px_3px_0_#ff2f92]">
                        <div className="mb-1 flex items-center justify-between text-xs font-black">
                          <span>主观评分</span>
                          <span className={`inline-flex items-center gap-1 bg-white px-1.5 text-base ${getScoreTextClass(item.score)}`}>
                            <i className="fa-solid fa-star text-[11px]"></i>
                            {item.score === null ? '待定' : `${item.score.toFixed(1)} / 10`}
                          </span>
                        </div>
                        <div className="h-3 overflow-hidden border-2 border-black bg-white">
                          <div
                            className="h-full bg-[#ff2f92]"
                            style={{ width: `${Math.max(0, Math.min(100, score * 10))}%` }}
                          />
                        </div>
                      </div>
                    </div>

                    <p
                      className="mt-5 max-h-[232px] min-h-[150px] flex-1 overflow-y-auto border-l-[7px] border-black bg-[#fff5ee] px-4 py-3 text-sm font-bold leading-7 text-zinc-700 custom-scrollbar"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {item.review || '这位主理人暂时没有留下长评。'}
                    </p>

                    <div className="mt-5 flex flex-wrap gap-2">
                      {item.tags.map((tag, tagIndex) => (
                        <span
                          key={tag}
                          className={`memphis-sticker ${tagIndex % 2 ? 'rotate-1' : '-rotate-1'} rounded-full px-3 py-1 text-xs font-black ${getTagClass(tag, item.tagColors?.[tag])}`}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </div>

      <DetailModal
        selectedItem={selectedItem}
        closeModal={() => setSelectedItem(null)}
      />
      <BackToTop isModalOpen={Boolean(selectedItem)} theme="memphis" />
    </main>
  )
}

export default FeaturedColumn
