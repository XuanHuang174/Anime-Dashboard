import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from './api/api.js'
import AnimeGrid from './components/AnimeGrid.jsx'
import AnimeList from './components/AnimeList.jsx'
import BackToTop from './components/BackToTop.jsx'
import CustomCursor from './components/CustomCursor.jsx'
import DetailModal from './components/DetailModal.jsx'
import FilterBar from './components/FilterBar.jsx'
import Header from './components/Header.jsx'
import {
  PageNavigation,
  PageSizeSelector,
  PriorityNotice,
} from './components/RecordPaginationControls.jsx'
import StatsSummary from './components/StatsSummary.jsx'
import animeData from './data/animeData.js'
import { comparePeriod } from './utils/animeDisplay.js'
import { mapRecordToAnimeItem } from './utils/recordMapping.js'

function matchesShowcaseQuickFilter(item, filter) {
  if (!filter) return true
  const titleKeywords = filter.title_keywords ?? []
  if (filter.match_mode === 'all') {
    return filter.tags.every((tag) => item.tags.includes(tag))
      && titleKeywords.every((keyword) => item.title.toUpperCase().includes(keyword.toUpperCase()))
  }
  return filter.tags.some((tag) => item.tags.includes(tag))
    || titleKeywords.some((keyword) => item.title.toUpperCase().includes(keyword.toUpperCase()))
}

function App() {
  const navigate = useNavigate()
  const [db, setDb] = useState(() => animeData.map((item) => ({ ...item })))
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedItem, setSelectedItem] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('all')
  const [yearFilter, setYearFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('date-desc')
  const [typeFilter, setTypeFilter] = useState('all')
  const [view, setView] = useState('list')
  const [showcaseQuickFilters, setShowcaseQuickFilters] = useState([])
  const [activeCustomFilterId, setActiveCustomFilterId] = useState(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState('all')
  const [showcaseTotal, setShowcaseTotal] = useState(null)
  const [hasNextPage, setHasNextPage] = useState(false)
  const [hasPreviousPage, setHasPreviousPage] = useState(false)
  const [isShowcaseLoading, setIsShowcaseLoading] = useState(false)
  const [statsData, setStatsData] = useState(null)
  const [filterOptions, setFilterOptions] = useState({ tags: [], years: [] })
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [lastLoadedPage, setLastLoadedPage] = useState(0)
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
    let isMounted = true

    const loadPublicShowcase = async () => {
      setIsShowcaseLoading(true)
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
      const [showcaseResult, filtersResult, statsResult, optionsResult] = await Promise.allSettled([
        api.get('showcase/', {
          params: requestParams,
        }),
        api.get('showcase-filters/'),
        api.get('showcase/stats/'),
        api.get('showcase/filter-options/'),
      ])

      if (!isMounted) return
      if (showcaseResult.status === 'fulfilled') {
        const payload = showcaseResult.value.data
        const data = Array.isArray(payload) ? payload : payload.results ?? []
        setDb(data.map(mapRecordToAnimeItem))
        setShowcaseTotal(Array.isArray(payload) ? data.length : payload.count)
        setHasNextPage(!Array.isArray(payload) && Boolean(payload.next))
        setHasPreviousPage(!Array.isArray(payload) && Boolean(payload.previous))
        setLastLoadedPage(currentPage)
      }
      if (filtersResult.status === 'fulfilled') {
        const filters = Array.isArray(filtersResult.value.data)
          ? filtersResult.value.data
          : filtersResult.value.data.results ?? []
        setShowcaseQuickFilters(filters)
        setActiveCustomFilterId((current) => (
          current === null || filters.some((filter) => filter.id === current)
            ? current
            : null
        ))
      }
      if (statsResult.status === 'fulfilled') {
        setStatsData(statsResult.value.data)
      }
      if (optionsResult.status === 'fulfilled') {
        setFilterOptions(optionsResult.value.data)
      }
      setIsShowcaseLoading(false)
    }

    loadPublicShowcase()
    window.addEventListener('focus', loadPublicShowcase)
    return () => {
      isMounted = false
      window.removeEventListener('focus', loadPublicShowcase)
    }
  }, [currentPage, pageSize, paginatedFilterKey])

  useEffect(() => {
    if (lastLoadedPage !== currentPage || !shouldScrollAfterPageChange.current) return undefined
    const frame = window.requestAnimationFrame(() => {
      document.querySelector('#showcasePaginationTop')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
      shouldScrollAfterPageChange.current = false
    })
    return () => window.cancelAnimationFrame(frame)
  }, [currentPage, lastLoadedPage])

  const activeShowcaseFilter = useMemo(
    () => showcaseQuickFilters.find((filter) => filter.id === activeCustomFilterId) ?? null,
    [activeCustomFilterId, showcaseQuickFilters],
  )

  const filteredAnime = useMemo(() => {
    if (pageSize !== 'all') return db

    const searchValue = searchQuery.toLowerCase().trim()
    const filtered = db.filter((item) => {
      const matchChinese = item.title.toLowerCase().includes(searchValue)
      const matchJapanese = item.japaneseTitle
        ? item.japaneseTitle.toLowerCase().includes(searchValue)
        : false
      const matchSearch = matchChinese || matchJapanese
      const matchTag = tagFilter === 'all' || item.tags.includes(tagFilter)
      const matchYear = yearFilter === 'all' || item.period.startsWith(yearFilter)
      const matchStatus = statusFilter === 'all' || item.status === statusFilter

      const matchQuick = matchesShowcaseQuickFilter(item, activeShowcaseFilter)

      return matchSearch && matchTag && matchYear && matchStatus && matchQuick
    })

    return filtered.sort((a, b) => {
      const isAPending =
        a.score === null || a.period.includes('待定') || a.period === '未定档'
      const isBPending =
        b.score === null || b.period.includes('待定') || b.period === '未定档'

      if (isAPending && !isBPending) return 1
      if (!isAPending && isBPending) return -1
      if (isAPending && isBPending) return comparePeriod(a.period, b.period)

      switch (sortOrder) {
        case 'date-desc':
          return comparePeriod(b.period, a.period)
        case 'date-asc':
          return comparePeriod(a.period, b.period)
        case 'score-desc':
          return b.score - a.score
        case 'score-asc':
          return a.score - b.score
        default:
          return 0
      }
    })
  }, [activeShowcaseFilter, db, pageSize, searchQuery, sortOrder, statusFilter, tagFilter, yearFilter])

  const visibleUnratedCount = filteredAnime.reduce(
    (count, item) => count + (item.score === null ? 1 : 0),
    0,
  )
  const totalPages = pageSize === 'all'
    ? 1
    : Math.max(1, Math.ceil((showcaseTotal ?? 0) / Number(pageSize)))
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

  const handleOpenDetail = (item) => {
    setSelectedItem(item)
    setIsModalOpen(true)
  }

  const closeModal = () => {
    setIsModalOpen(false)
    setSelectedItem(null)
  }

  const handleGetStarted = () => navigate('/login')

  return (
    <>
      <CustomCursor />
      <Header onGetStarted={handleGetStarted} />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 -mt-6 relative z-20">
        <StatsSummary db={db} statsData={statsData} />
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
          view={view}
          setView={setView}
          filteredCount={pageSize === 'all' ? filteredAnime.length : (showcaseTotal ?? 0)}
          customQuickFilters={showcaseQuickFilters}
          activeCustomFilterId={activeCustomFilterId}
          setActiveCustomFilterId={setActiveCustomFilterId}
          onResetPagination={() => handlePageSizeChange('all')}
          availableTags={filterOptions.tags}
          availableYears={filterOptions.years}
        />

        <div
          id="showcasePaginationTop"
          className="mb-5 grid grid-cols-2 scroll-mt-5 items-stretch gap-3"
        >
          <PageSizeSelector
            pageSize={pageSize}
            onChange={handlePageSizeChange}
            totalCount={showcaseTotal}
            isLoading={isShowcaseLoading}
          />
          <PriorityNotice unratedCount={visibleUnratedCount} />
        </div>

        <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-indigo-500/30 to-transparent opacity-0 group-hover/container:opacity-100 transition-opacity duration-700"></div>

        {filteredAnime.length > 0 && view === 'list' && (
          <AnimeList
            items={filteredAnime}
            sortOrder={sortOrder}
            setSortOrder={setSortOrder}
            onOpenDetail={handleOpenDetail}
            startIndex={listStartIndex}
          />
        )}
        {filteredAnime.length > 0 && view === 'grid' && (
          <AnimeGrid items={filteredAnime} onOpenDetail={handleOpenDetail} />
        )}

        {filteredAnime.length === 0 && (
          <div id="noResults" className="py-16 px-4 text-center">
            <i className="fa-solid fa-mug-hot text-slate-300 text-5xl mb-4 animate-bounce"></i>
            <h3 className="text-slate-600 font-bold text-lg">没有找到匹配的番剧</h3>
            <p className="text-slate-400 text-xs mt-1">尝试调整过滤条件或搜索更精准的内容</p>
          </div>
        )}

        {pageSize !== 'all' && showcaseTotal > 0 && (
          <PageNavigation
            currentPage={currentPage}
            totalPages={totalPages}
            hasPrevious={hasPreviousPage}
            hasNext={hasNextPage}
            isLoading={isShowcaseLoading}
            onPageChange={handlePageChange}
          />
        )}
      </div>

      <DetailModal
        selectedItem={isModalOpen ? selectedItem : null}
        closeModal={closeModal}
      />
      <BackToTop isModalOpen={isModalOpen} />
    </>
  )
}

export default App
