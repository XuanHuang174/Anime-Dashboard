import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useLocation, useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'
import AnimeGrid from '../components/AnimeGrid.jsx'
import AnimeList from '../components/AnimeList.jsx'
import AddAnimeModal from '../components/AddAnimeModal.jsx'
import BackToTop from '../components/BackToTop.jsx'
import DashboardHeader from '../components/DashboardHeader.jsx'
import DetailModal from '../components/DetailModal.jsx'
import EditableDetailModal from '../components/EditableDetailModal.jsx'
import FilterBar from '../components/FilterBar.jsx'
import QuickFilterEditorModal from '../components/QuickFilterEditorModal.jsx'
import {
  PageNavigation,
  PageSizeSelector,
  PriorityNotice,
} from '../components/RecordPaginationControls.jsx'
import StatsSummary from '../components/StatsSummary.jsx'
import { comparePeriod } from '../utils/animeDisplay.js'
import { mapRecordToAnimeItem } from '../utils/recordMapping.js'

gsap.registerPlugin(useGSAP)

let hasPlayedDashboardHeaderEntrance = false
let hasPlayedDashboardContentEntrance = false

const quickFilterTags = {
  yuri: ['真百', '轻百', '真百合', '轻百合'],
  moe: ['萌系', '日常'],
  comedy: ['搞笑', '校园'],
  og: ['原创', '治愈'],
}

function matchesQuickFilter(item, typeFilter) {
  if (typeFilter === 'all') return true

  if (typeFilter === 'special') {
    return item.tags.includes('剧场版')
      || item.tags.includes('泡面番')
      || item.title.toUpperCase().includes('OVA')
  }

  return (quickFilterTags[typeFilter] ?? []).some((tag) => item.tags.includes(tag))
}

function matchesCustomQuickFilter(item, filter) {
  if (!filter) return true
  const titleKeywords = filter.title_keywords ?? []
  if (filter.match_mode === 'all') {
    return filter.tags.every((tag) => item.tags.includes(tag))
      && titleKeywords.every((keyword) => item.title.toUpperCase().includes(keyword.toUpperCase()))
  }
  return filter.tags.some((tag) => item.tags.includes(tag))
    || titleKeywords.some((keyword) => item.title.toUpperCase().includes(keyword.toUpperCase()))
}

function compareScores(a, b, direction) {
  if (a.score === null && b.score === null) return 0
  if (a.score === null) return 1
  if (b.score === null) return -1
  return direction === 'asc' ? a.score - b.score : b.score - a.score
}

function compareJournalPriority(a, b) {
  const aIsRated = a.score !== null
  const bIsRated = b.score !== null
  if (aIsRated !== bIsRated) return aIsRated ? -1 : 1

  const aIsCompleted = a.status === 'completed'
  const bIsCompleted = b.status === 'completed'
  if (aIsCompleted !== bIsCompleted) return aIsCompleted ? -1 : 1

  return 0
}

function Dashboard() {
  const dashboardRef = useRef(null)
  const navigate = useNavigate()
  const location = useLocation()
  const [records, setRecords] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedItem, setSelectedItem] = useState(null)
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [toast, setToast] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('all')
  const [yearFilter, setYearFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('date-desc')
  const [typeFilter, setTypeFilter] = useState('all')
  const [view, setView] = useState('list')
  const [customQuickFilters, setCustomQuickFilters] = useState([])
  const [activeCustomFilterId, setActiveCustomFilterId] = useState(null)
  const [isQuickFilterEditorOpen, setIsQuickFilterEditorOpen] = useState(false)
  const [isLogoutConfirmOpen, setIsLogoutConfirmOpen] = useState(false)
  const [isPreviewMode, setIsPreviewMode] = useState(false)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState('all')
  const [recordsTotal, setRecordsTotal] = useState(null)
  const [hasNextPage, setHasNextPage] = useState(false)
  const [hasPreviousPage, setHasPreviousPage] = useState(false)
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0)
  const [statsData, setStatsData] = useState(null)
  const [filterOptions, setFilterOptions] = useState({ tags: [], years: [] })
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [lastLoadedPage, setLastLoadedPage] = useState(0)
  const [isHeaderEntranceComplete, setIsHeaderEntranceComplete] = useState(
    hasPlayedDashboardHeaderEntrance,
  )
  const [isContentEntranceComplete, setIsContentEntranceComplete] = useState(
    hasPlayedDashboardContentEntrance,
  )
  const shouldScrollAfterPageChange = useRef(false)
  const paginatedFilterKey = pageSize === 'all'
    ? 'all'
    : JSON.stringify([
        debouncedSearchQuery, tagFilter, yearFilter, statusFilter,
        sortOrder, typeFilter, activeCustomFilterId,
      ])

  useEffect(() => {
    if (window.innerWidth < 768) setView('grid')
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearchQuery(searchQuery.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [searchQuery])

  useEffect(() => {
    if (pageSize !== 'all') setCurrentPage(1)
  }, [activeCustomFilterId, debouncedSearchQuery, pageSize, sortOrder, statusFilter, tagFilter, typeFilter, yearFilter])

  useEffect(() => {
    if (!toast) return undefined
    const timer = window.setTimeout(() => setToast(null), 2600)
    return () => window.clearTimeout(timer)
  }, [toast])

  useEffect(() => {
    if (!isLogoutConfirmOpen) return undefined
    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const currentPaddingRight = Number.parseFloat(
      window.getComputedStyle(document.body).paddingRight,
    ) || 0
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${currentPaddingRight + scrollbarWidth}px`
    }
    document.body.style.overflow = 'hidden'
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setIsLogoutConfirmOpen(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isLogoutConfirmOpen])

  useEffect(() => {
    const controller = new AbortController()

    async function loadRecords() {
      setIsLoading(true)
      setError('')

      try {
        const requestParams = {
          page: currentPage,
          page_size: pageSize,
        }
        if (pageSize !== 'all') {
          Object.assign(requestParams, {
            search: debouncedSearchQuery || undefined,
            tag: tagFilter !== 'all' ? tagFilter : undefined,
            year: yearFilter !== 'all' ? yearFilter : undefined,
            status: statusFilter !== 'all' ? statusFilter : undefined,
            sort: sortOrder,
            type_filter: typeFilter !== 'all' ? typeFilter : undefined,
            quick_filter: activeCustomFilterId ?? undefined,
          })
        }
        const [recordsResponse, filtersResponse, statsResponse, optionsResponse] = await Promise.all([
          api.get('records/', {
            params: requestParams,
            signal: controller.signal,
          }),
          api.get('quick-filters/', { signal: controller.signal }),
          api.get('records/stats/', { signal: controller.signal }),
          api.get('records/filter-options/', { signal: controller.signal }),
        ])
        const recordData = recordsResponse.data
        const filterData = filtersResponse.data
        setRecords(Array.isArray(recordData) ? recordData : recordData.results ?? [])
        setRecordsTotal(Array.isArray(recordData) ? recordData.length : recordData.count)
        setHasNextPage(!Array.isArray(recordData) && Boolean(recordData.next))
        setHasPreviousPage(!Array.isArray(recordData) && Boolean(recordData.previous))
        setLastLoadedPage(currentPage)
        setCustomQuickFilters(Array.isArray(filterData) ? filterData : filterData.results ?? [])
        setStatsData(statsResponse.data)
        setFilterOptions(optionsResponse.data)
      } catch (requestError) {
        if (requestError.code === 'ERR_CANCELED') return

        if (requestError.response?.status === 401) {
          clearTokens()
          navigate('/login', { replace: true, state: { from: location } })
          return
        }

        setError('追番记录加载失败，请稍后重试。')
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadRecords()
    return () => controller.abort()
  }, [currentPage, location, navigate, pageSize, paginatedFilterKey, recordsRefreshKey])

  useEffect(() => {
    if (lastLoadedPage !== currentPage || !shouldScrollAfterPageChange.current) return undefined
    const frame = window.requestAnimationFrame(() => {
      document.querySelector('#dashboardPaginationTop')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
      shouldScrollAfterPageChange.current = false
    })
    return () => window.cancelAnimationFrame(frame)
  }, [currentPage, lastLoadedPage])

  const db = useMemo(() => records.map(mapRecordToAnimeItem), [records])
  const activeCustomFilter = useMemo(
    () => customQuickFilters.find((filter) => filter.id === activeCustomFilterId) ?? null,
    [activeCustomFilterId, customQuickFilters],
  )

  const filteredAnime = useMemo(() => {
    if (pageSize !== 'all') return db

    const keyword = searchQuery.trim().toLowerCase()

    const filtered = db.filter((item) => {
      const matchesSearch = !keyword
        || item.title.toLowerCase().includes(keyword)
        || item.japaneseTitle.toLowerCase().includes(keyword)
      const matchesTag = tagFilter === 'all' || item.tags.includes(tagFilter)
      const matchesYear = yearFilter === 'all' || item.period.startsWith(yearFilter)
      const matchesStatus = statusFilter === 'all' || item.status === statusFilter

      return matchesSearch
        && matchesTag
        && matchesYear
        && matchesStatus
        && matchesQuickFilter(item, typeFilter)
        && matchesCustomQuickFilter(item, activeCustomFilter)
    })

    return [...filtered].sort((a, b) => {
      const priorityDifference = compareJournalPriority(a, b)
      if (priorityDifference !== 0) return priorityDifference

      switch (sortOrder) {
        case 'date-asc':
          return comparePeriod(a.period, b.period)
        case 'score-desc':
          return compareScores(a, b, 'desc')
        case 'score-asc':
          return compareScores(a, b, 'asc')
        case 'updated-desc':
          return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
        case 'updated-asc':
          return new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime()
        case 'date-desc':
        default:
          return comparePeriod(b.period, a.period)
      }
    })
  }, [activeCustomFilter, db, pageSize, searchQuery, sortOrder, statusFilter, tagFilter, typeFilter, yearFilter])

  const visibleUnratedCount = filteredAnime.reduce(
    (count, item) => count + (item.score === null ? 1 : 0),
    0,
  )
  const totalPages = pageSize === 'all'
    ? 1
    : Math.max(1, Math.ceil((recordsTotal ?? 0) / Number(pageSize)))
  const listStartIndex = pageSize === 'all'
    ? 0
    : (currentPage - 1) * Number(pageSize)

  const handlePageSizeChange = (nextPageSize) => {
    setPageSize(nextPageSize)
    setCurrentPage(1)
  }

  const handlePageChange = (nextPage) => {
    if (nextPage < 1 || nextPage > totalPages || nextPage === currentPage) return
    shouldScrollAfterPageChange.current = true
    setCurrentPage(nextPage)
  }

  const handleLogout = () => {
    setIsLogoutConfirmOpen(true)
  }

  const forceLogout = () => {
    clearTokens()
    navigate('/login', { replace: true })
  }

  const handleAccountDeleted = () => {
    clearTokens()
    navigate('/login', {
      replace: true,
      state: { message: '账户已永久注销，该邮箱现在可以重新注册。' },
    })
  }

  const confirmLogout = () => {
    setIsLogoutConfirmOpen(false)
    forceLogout()
  }

  const handleRecordUpdated = (updatedRecord) => {
    setRecords((current) => current.map((record) => (
      record.id === updatedRecord.id ? updatedRecord : record
    )))
    setSelectedItem(mapRecordToAnimeItem(updatedRecord))
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleRecordDeleted = (recordId) => {
    setRecords((current) => current.filter((record) => record.id !== recordId))
    setSelectedItem(null)
    if (records.length === 1 && currentPage > 1) {
      setCurrentPage((page) => page - 1)
    } else {
      setRecordsRefreshKey((key) => key + 1)
    }
  }

  const handleRecordAdded = (newRecord) => {
    if (records.some((record) => record.id === newRecord.id)) return
    setCurrentPage(1)
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleRecordsImported = () => {
    setSelectedItem(null)
    setCurrentPage(1)
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleQuickFilterSaved = (savedFilter) => {
    setCustomQuickFilters((current) => {
      const exists = current.some((filter) => filter.id === savedFilter.id)
      return exists
        ? current.map((filter) => filter.id === savedFilter.id ? savedFilter : filter)
        : [...current, savedFilter]
    })
  }

  const handleQuickFilterDeleted = (filterId) => {
    setCustomQuickFilters((current) => current.filter((filter) => filter.id !== filterId))
    setActiveCustomFilterId((current) => current === filterId ? null : current)
  }

  const showToast = (message, type = 'success') => {
    setToast({ message, type, id: Date.now() })
  }

  const togglePreviewMode = () => {
    setSelectedItem(null)
    setIsQuickFilterEditorOpen(false)
    setIsPreviewMode((current) => !current)
  }

  useGSAP(() => {
    const consoleElement = dashboardRef.current?.querySelector('.dashboard-memphis-console')
    if (!consoleElement) return undefined
    if (hasPlayedDashboardHeaderEntrance) {
      setIsHeaderEntranceComplete(true)
      return undefined
    }

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion) {
      hasPlayedDashboardHeaderEntrance = true
      gsap.set(consoleElement, { clearProps: 'transform,opacity,visibility' })
      setIsHeaderEntranceComplete(true)
      return undefined
    }

    gsap.set(consoleElement, { willChange: 'transform,opacity' })
    const entrance = gsap.fromTo(
      consoleElement,
      { y: -110, rotation: -1.5, autoAlpha: 0 },
      {
        y: 0,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.78,
        ease: 'bounce.out',
        onComplete: () => {
          hasPlayedDashboardHeaderEntrance = true
          gsap.set(consoleElement, { clearProps: 'transform,opacity,visibility,willChange' })
          setIsHeaderEntranceComplete(true)
        },
      },
    )

    return () => {
      entrance.kill()
      gsap.set(consoleElement, { clearProps: 'transform,opacity,visibility,willChange' })
    }
  }, { scope: dashboardRef })

  useGSAP(() => {
    if (!isHeaderEntranceComplete || isLoading || error) return undefined
    if (hasPlayedDashboardContentEntrance) {
      setIsContentEntranceComplete(true)
      return undefined
    }

    const targets = gsap.utils.toArray('.dashboard-entrance-piece')
    if (!targets.length) {
      hasPlayedDashboardContentEntrance = true
      setIsContentEntranceComplete(true)
      return undefined
    }
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion) {
      hasPlayedDashboardContentEntrance = true
      gsap.set(targets, { clearProps: 'transform,opacity,visibility' })
      setIsContentEntranceComplete(true)
      return undefined
    }

    gsap.set(targets, { willChange: 'transform,opacity' })
    const entrance = gsap.fromTo(
      targets,
      { y: 42, scale: 0.96, autoAlpha: 0 },
      {
        y: 0,
        scale: 1,
        autoAlpha: 1,
        duration: 0.48,
        stagger: 0.1,
        ease: 'back.out(1.5)',
        onComplete: () => {
          hasPlayedDashboardContentEntrance = true
          gsap.set(targets, { clearProps: 'transform,opacity,visibility,willChange' })
          setIsContentEntranceComplete(true)
        },
      },
    )

    return () => {
      entrance.kill()
      gsap.set(targets, { clearProps: 'transform,opacity,visibility,willChange' })
    }
  }, {
    scope: dashboardRef,
    dependencies: [isHeaderEntranceComplete, isLoading, error],
  })

  return (
    <main
      ref={dashboardRef}
      className="memphis-public memphis-dashboard relative min-h-screen overflow-x-hidden bg-[#fff5ee] pb-8 text-black"
    >
      <div className="memphis-grid-texture pointer-events-none absolute inset-0" aria-hidden="true" />
      <div className="memphis-float-ring pointer-events-none absolute right-[-64px] top-[30rem] z-0 h-32 w-32 rounded-full border-[18px] border-black bg-[#4ecdc4]" aria-hidden="true" />
      <div className="memphis-float-triangle pointer-events-none absolute left-[-32px] top-[54rem] z-0 h-0 w-0" aria-hidden="true" />
      <div className="memphis-float-zigzag pointer-events-none absolute bottom-24 right-0 z-0 h-16 w-44" aria-hidden="true" />

      <DashboardHeader
        isPreviewMode={isPreviewMode}
        onTogglePreview={togglePreviewMode}
        onAddAnime={() => setIsAddModalOpen(true)}
        onRecordsImported={handleRecordsImported}
        onLogout={handleLogout}
        onAccountDeleted={handleAccountDeleted}
        onUnauthorized={forceLogout}
        showToast={showToast}
      />

      {isPreviewMode && isHeaderEntranceComplete && !isLoading && !error && (
        <div className="dashboard-content-piece dashboard-entrance-piece relative z-20 mx-auto -mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
          <StatsSummary db={db} statsData={statsData} theme="memphis" />
        </div>
      )}

      <section className={`relative z-10 mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 ${isPreviewMode ? 'pb-8 pt-2' : 'py-7'}`}>
        {isHeaderEntranceComplete && isLoading && (
          <div className="flex items-center justify-center gap-3 border-4 border-black bg-[#ffe66d] py-12 font-black text-black shadow-[8px_8px_0_#4ecdc4]">
            <i className="fa-solid fa-spinner animate-spin text-xl"></i>
            正在读取你的追番手账...
          </div>
        )}

        {isHeaderEntranceComplete && !isLoading && error && (
          <div role="alert" className="border-4 border-black bg-[#ff6b6b] px-5 py-5 font-black text-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-circle-exclamation mr-2"></i>
            {error}
          </div>
        )}

        {isHeaderEntranceComplete && !isLoading && !error && (
          <>
            <div className="dashboard-content-piece dashboard-entrance-piece">
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
              showRecordTimeSort
              view={view}
              setView={setView}
              filteredCount={pageSize === 'all' ? filteredAnime.length : (recordsTotal ?? 0)}
              customQuickFilters={customQuickFilters}
              activeCustomFilterId={activeCustomFilterId}
              setActiveCustomFilterId={setActiveCustomFilterId}
              onOpenQuickFilterEditor={isPreviewMode ? null : () => setIsQuickFilterEditorOpen(true)}
              onResetPagination={() => handlePageSizeChange('all')}
              availableTags={filterOptions.tags}
              availableYears={filterOptions.years}
              enablePosterViewHint={false}
              theme="memphis"
            />
            </div>

            <div
              id="dashboardPaginationTop"
              className="dashboard-content-piece dashboard-entrance-piece mb-5 grid grid-cols-2 scroll-mt-5 items-stretch gap-3"
            >
              <PageSizeSelector
                pageSize={pageSize}
                onChange={handlePageSizeChange}
                totalCount={recordsTotal}
                isLoading={isLoading}
                theme="memphis"
              />
              <PriorityNotice unratedCount={visibleUnratedCount} theme="memphis" />
            </div>

            <div className="dashboard-content-piece dashboard-entrance-piece mb-5 h-2 w-full border-2 border-black bg-[repeating-linear-gradient(135deg,#111_0,#111_8px,#ffe66d_8px,#ffe66d_16px)]" />

            {isContentEntranceComplete && filteredAnime.length > 0 && view === 'list' && (
              <div className="dashboard-content-piece">
              <AnimeList
                items={filteredAnime}
                sortOrder={sortOrder}
                setSortOrder={setSortOrder}
                onOpenDetail={setSelectedItem}
                startIndex={listStartIndex}
                theme="memphis"
                editable={!isPreviewMode}
              />
              </div>
            )}

            {isContentEntranceComplete && filteredAnime.length > 0 && view === 'grid' && (
              <div className="dashboard-content-piece">
                <AnimeGrid
                  items={filteredAnime}
                  onOpenDetail={setSelectedItem}
                  theme="memphis"
                  editable={!isPreviewMode}
                />
              </div>
            )}

            {isContentEntranceComplete && filteredAnime.length === 0 && db.length === 0 && (
              <button
                type="button"
                onClick={() => setIsAddModalOpen(true)}
                className="dashboard-content-piece memphis-pressable group relative w-full overflow-hidden border-4 border-black bg-[#ffe66d] py-14 text-center shadow-[8px_8px_0_#ff6b6b] transition-none active:translate-x-[6px] active:translate-y-[6px] active:shadow-none"
              >
                <span className="absolute -right-10 -top-10 h-28 w-28 rotate-12 border-4 border-black bg-[#ff8fab]"></span>
                <span className="absolute -bottom-10 -left-10 h-24 w-24 -rotate-12 border-4 border-black bg-[#4ecdc4]"></span>
                <span className="relative mx-auto flex h-16 w-16 items-center justify-center border-4 border-black bg-white text-2xl text-black shadow-[5px_5px_0_#111]">
                  <i className="fa-solid fa-plus"></i>
                </span>
                <h2 className="relative mt-5 text-xl font-black text-slate-700">手账还是空的</h2>
                <p className="relative mt-1 text-sm font-medium text-slate-500">点击这里，添加第一部属于你的番剧。</p>
                <span className="relative mt-5 inline-flex items-center rounded-xl bg-white px-4 py-2 text-xs font-black text-indigo-600 shadow-md ring-1 ring-indigo-100 transition group-hover:shadow-lg">
                  开始添加 <i className="fa-solid fa-arrow-right ml-2 transition-transform group-hover:translate-x-1"></i>
                </span>
              </button>
            )}

            {isContentEntranceComplete && filteredAnime.length === 0 && db.length > 0 && (
              <div className="dashboard-content-piece border-4 border-black bg-white py-14 text-center shadow-[8px_8px_0_#ff6b6b]">
                <i className="fa-solid fa-filter-circle-xmark text-5xl text-slate-300"></i>
                <h2 className="mt-4 text-lg font-black text-slate-700">没有符合条件的追番记录</h2>
                <p className="mt-1 text-sm text-slate-500">尝试调整搜索或过滤条件。</p>
              </div>
            )}

            {isContentEntranceComplete && pageSize !== 'all' && recordsTotal > 0 && (
              <PageNavigation
                currentPage={currentPage}
                totalPages={totalPages}
                hasPrevious={hasPreviousPage}
                hasNext={hasNextPage}
                isLoading={isLoading}
                onPageChange={handlePageChange}
              />
            )}
          </>
        )}
      </section>

      {isPreviewMode ? (
        <DetailModal
          selectedItem={selectedItem}
          closeModal={() => setSelectedItem(null)}
        />
      ) : (
        <EditableDetailModal
          selectedItem={selectedItem}
          closeModal={() => setSelectedItem(null)}
          onRecordUpdated={handleRecordUpdated}
          onRecordDeleted={handleRecordDeleted}
          showToast={showToast}
        />
      )}
      <AddAnimeModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        existingAnimeIds={new Set(db.map((item) => item.animeId))}
        onRecordAdded={handleRecordAdded}
        showToast={showToast}
      />
      {!isPreviewMode && (
        <QuickFilterEditorModal
          isOpen={isQuickFilterEditorOpen}
          onClose={() => setIsQuickFilterEditorOpen(false)}
          availableTags={filterOptions.tags}
          filters={customQuickFilters}
          onSaved={handleQuickFilterSaved}
          onDeleted={handleQuickFilterDeleted}
          showToast={showToast}
        />
      )}
      {isLogoutConfirmOpen && (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both]"
          role="dialog"
          aria-modal="true"
          aria-labelledby="logoutConfirmTitle"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsLogoutConfirmOpen(false)
          }}
        >
          <section className="w-full max-w-sm overflow-hidden rounded-3xl border border-white/60 bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="h-2 bg-gradient-to-r from-pink-400 to-indigo-500"></div>
            <div className="px-6 py-7 text-center">
              <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-50 text-xl text-rose-500 shadow-inner">
                <i className="fa-solid fa-arrow-right-from-bracket"></i>
              </span>
              <h2 id="logoutConfirmTitle" className="mt-4 text-xl font-black text-slate-800">确定退出登录吗？</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">退出后需要重新登录才能进入你的私人手账。</p>
              <div className="mt-6 grid grid-cols-2 gap-3">
                <button type="button" onClick={() => setIsLogoutConfirmOpen(false)} className="rounded-xl bg-slate-100 px-4 py-2.5 text-sm font-bold text-slate-600 transition hover:bg-slate-200 active:scale-95">继续整理</button>
                <button type="button" onClick={confirmLogout} className="rounded-xl bg-gradient-to-r from-rose-500 to-pink-500 px-4 py-2.5 text-sm font-black text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg active:scale-95">确认退出</button>
              </div>
            </div>
          </section>
        </div>
      )}
      <BackToTop isModalOpen={Boolean(selectedItem) || isLogoutConfirmOpen} theme="memphis" />

      {toast && (
        <div
          key={toast.id}
          role="status"
          className={`fixed bottom-6 right-6 z-[200] flex max-w-sm items-center gap-3 border-[3px] border-black px-5 py-4 font-black text-black shadow-[6px_6px_0_#111] animate-[parallaxReveal_0.25s_ease-out_forwards] ${toast.type === 'error' ? 'bg-[#ff6b6b]' : 'bg-[#4ecdc4]'}`}
        >
          <i className={`fa-solid ${toast.type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-check'} text-lg`}></i>
          {toast.message}
        </div>
      )}
    </main>
  )
}

export default Dashboard
