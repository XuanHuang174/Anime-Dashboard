import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'
import AnimeGrid from '../components/AnimeGrid.jsx'
import AnimeList from '../components/AnimeList.jsx'
import AddAnimeModal from '../components/AddAnimeModal.jsx'
import BackToTop from '../components/BackToTop.jsx'
import BangumiImportModal from '../components/BangumiImportModal.jsx'
import DashboardHeader from '../components/DashboardHeader.jsx'
import JournalResourceSearch from '../components/JournalResourceSearch.jsx'
import { downloadJournalCsv } from '../utils/journalCsv.js'
import DetailModal from '../components/DetailModal.jsx'
import EditableDetailModal from '../components/EditableDetailModal.jsx'
import FeaturedActionConfirmModal from '../components/FeaturedActionConfirmModal.jsx'
import FilterBar from '../components/FilterBar.jsx'
import MemphisToastStack from '../components/MemphisToastStack.jsx'
import QuickFilterEditorModal from '../components/QuickFilterEditorModal.jsx'
import { matchesCustomQuickFilter } from '../utils/quickFilter.js'
import {
  PageNavigation,
  PageSizeSelector,
  PriorityNotice,
} from '../components/RecordPaginationControls.jsx'
import StatsSummary from '../components/StatsSummary.jsx'
import { comparePeriod } from '../utils/animeDisplay.js'
import { mapRecordToAnimeItem } from '../utils/recordMapping.js'
import { notifyRecommendationChange } from '../utils/recommendationReload.js'
import ShowcasePublicationPanel from '../components/ShowcasePublicationPanel.jsx'
import recommendationClient from '../api/recommendations.js'
import useExplorationTopics from '../hooks/useExplorationTopics.js'

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

function compareScores(a, b, direction) {
  if (a.score === null && b.score === null) return 0
  if (a.score === null) return 1
  if (b.score === null) return -1
  return direction === 'asc' ? a.score - b.score : b.score - a.score
}

function compareRatedPriority(a, b) {
  const aIsRated = a.score !== null
  const bIsRated = b.score !== null
  if (aIsRated !== bIsRated) return aIsRated ? -1 : 1
  return 0
}

function compareCompletedTieBreaker(a, b) {
  const aIsCompleted = a.status === 'completed'
  const bIsCompleted = b.status === 'completed'
  if (aIsCompleted !== bIsCompleted) return aIsCompleted ? -1 : 1
  return 0
}

function Dashboard() {
  const dashboardRef = useRef(null)
  const navigate = useNavigate()
  const location = useLocation()
  const { username: sharedUsername = '' } = useParams()
  const isSharedJournal = Boolean(sharedUsername)
  const explorationTags = useExplorationTopics(recommendationClient, isSharedJournal ? false : undefined)
  const isAdminJournalReview = location.pathname.startsWith(
    '/admin-dashboard/public-journal-review/',
  )
  const [records, setRecords] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedItem, setSelectedItem] = useState(null)
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [isBangumiImportOpen, setIsBangumiImportOpen] = useState(false)
  const [resourceSearch, setResourceSearch] = useState(null)
  const closeResourceSearch = useCallback(() => setResourceSearch(null), [])
  const openResourceSearch = (item) => setResourceSearch({
    key: `${Date.now()}-${Math.random()}`, title: item?.title || '', autoSearch: Boolean(item?.title),
  })
  const [toasts, setToasts] = useState([])
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
  const [logoutDestination, setLogoutDestination] = useState('login')
  const [isPreviewMode, setIsPreviewMode] = useState(isSharedJournal)
  const isEditMode = !isSharedJournal && !isPreviewMode
  const [sharedProfile, setSharedProfile] = useState(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState('all')
  const [recordsTotal, setRecordsTotal] = useState(null)
  const [recordsRefreshKey, setRecordsRefreshKey] = useState(0)
  const [statsData, setStatsData] = useState(null)
  const [filterOptions, setFilterOptions] = useState({ tags: [], years: [] })
  const [featuredSubmissions, setFeaturedSubmissions] = useState([])
  const [featuredLimit, setFeaturedLimit] = useState(3)
  const [featuredRefreshKey, setFeaturedRefreshKey] = useState(0)
  const [featuredConfirmAction, setFeaturedConfirmAction] = useState(null)
  const [isFeaturedActionProcessing, setIsFeaturedActionProcessing] = useState(false)
  const [isSharedExporting, setIsSharedExporting] = useState(false)
  const [progressUpdatingIds, setProgressUpdatingIds] = useState(() => new Set())
  const progressUpdatingIdsRef = useRef(new Set())
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [lastLoadedPage, setLastLoadedPage] = useState(0)
  const [isHeaderEntranceComplete, setIsHeaderEntranceComplete] = useState(
    hasPlayedDashboardHeaderEntrance,
  )
  const [isContentEntranceComplete, setIsContentEntranceComplete] = useState(
    hasPlayedDashboardContentEntrance,
  )
  const shouldScrollAfterPageChange = useRef(false)
  const recordsTotalRef = useRef(null)
  const paginatedFilterKey = pageSize === 'all'
    ? 'all'
    : JSON.stringify([
        debouncedSearchQuery, tagFilter, yearFilter, statusFilter,
        sortOrder, typeFilter, activeCustomFilterId,
      ])

  const showToast = useCallback((message, type = 'success') => {
    setToasts((current) => [
      ...current,
      {
        message,
        type,
        id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      },
    ])
  }, [])

  const dismissToast = useCallback((toastId) => {
    setToasts((current) => current.filter((toast) => toast.id !== toastId))
  }, [])

  useEffect(() => {
    if (window.innerWidth < 768) setView('grid')
  }, [])

  useEffect(() => {
    if (!isSharedJournal) return
    setIsPreviewMode(true)
    setIsAddModalOpen(false)
    setIsQuickFilterEditorOpen(false)
  }, [isSharedJournal])

  useEffect(() => {
    if (!isSharedJournal) {
      setSharedProfile(null)
      return undefined
    }

    if (location.state?.sharedProfile) {
      setSharedProfile(location.state.sharedProfile)
    }
    const controller = new AbortController()
    api.get(
      isAdminJournalReview ? 'admin/public-journals/' : 'public-journal/users/',
      {
      params: { search: sharedUsername, page_size: 1 },
      signal: controller.signal,
      },
    )
      .then((response) => {
        const payload = response.data
        const profiles = Array.isArray(payload) ? payload : payload.results ?? []
        setSharedProfile(profiles[0] ?? null)
      })
      .catch((requestError) => {
        if (requestError.code !== 'ERR_CANCELED') setSharedProfile(null)
      })
    return () => controller.abort()
  }, [
    isAdminJournalReview,
    isSharedJournal,
    location.state,
    sharedUsername,
  ])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearchQuery(searchQuery.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [searchQuery])

  useEffect(() => {
    if (pageSize !== 'all') setCurrentPage(1)
  }, [activeCustomFilterId, debouncedSearchQuery, pageSize, sortOrder, statusFilter, tagFilter, typeFilter, yearFilter])

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
        const sharedBase = isAdminJournalReview
          ? `admin/public-journals/${encodeURIComponent(sharedUsername)}`
          : `public-journal/users/${encodeURIComponent(sharedUsername)}`
        const recordsEndpoint = isSharedJournal ? `${sharedBase}/records/` : 'records/'
        const statsEndpoint = isSharedJournal ? `${sharedBase}/stats/` : 'records/stats/'
        const optionsEndpoint = isSharedJournal
          ? `${sharedBase}/filter-options/`
          : 'records/filter-options/'
        const filtersRequest = isSharedJournal
          ? Promise.resolve(null)
          : api.get('quick-filters/', { signal: controller.signal })
        const numericPageSize = Number(pageSize)
        const knownRecordsTotal = Number(recordsTotalRef.current)
        const isVirtualPlaceholderPage = isEditMode
          && pageSize !== 'all'
          && Number.isFinite(numericPageSize)
          && numericPageSize > 0
          && knownRecordsTotal > 0
          && (currentPage - 1) * numericPageSize >= knownRecordsTotal
        const recordsRequest = isVirtualPlaceholderPage
          ? Promise.resolve({
              data: {
                count: knownRecordsTotal,
                next: null,
                previous: currentPage > 1,
                results: [],
              },
            })
          : api.get(recordsEndpoint, {
              params: requestParams,
              signal: controller.signal,
            })
        const [recordsResult, filtersResult, statsResult, optionsResult] = await Promise.allSettled([
          recordsRequest,
          filtersRequest,
          api.get(statsEndpoint, { signal: controller.signal }),
          api.get(optionsEndpoint, { signal: controller.signal }),
        ])
        if (recordsResult.status === 'rejected') throw recordsResult.reason

        const recordsResponse = recordsResult.value
        const filtersResponse = filtersResult.status === 'fulfilled' ? filtersResult.value : null
        const statsResponse = statsResult.status === 'fulfilled' ? statsResult.value : null
        const optionsResponse = optionsResult.status === 'fulfilled' ? optionsResult.value : null
        const recordData = recordsResponse.data
        setRecords(Array.isArray(recordData) ? recordData : recordData.results ?? [])
        const nextRecordsTotal = Array.isArray(recordData) ? recordData.length : recordData.count
        recordsTotalRef.current = nextRecordsTotal
        setRecordsTotal(nextRecordsTotal)
        setLastLoadedPage(currentPage)

        const filterData = isSharedJournal
          ? optionsResponse?.data?.quick_filters
          : filtersResponse?.data
        if (filterData) {
          setCustomQuickFilters(Array.isArray(filterData) ? filterData : filterData.results ?? [])
        }
        if (statsResponse) setStatsData(statsResponse.data)
        if (optionsResponse) setFilterOptions(optionsResponse.data)
      } catch (requestError) {
        if (requestError.code === 'ERR_CANCELED') return

        if (!isSharedJournal && requestError.response?.status === 401) {
          clearTokens()
          navigate('/login', { replace: true, state: { from: location } })
          return
        }

        setError(
          isSharedJournal && requestError.response?.status === 404
            ? (
                isAdminJournalReview
                  ? '该待审手账不存在，或申请状态已经发生变化。'
                  : '该手账尚未公开，或用户不存在。'
              )
            : isSharedJournal
              ? (
                  isAdminJournalReview
                    ? '管理员审阅数据加载失败，请稍后重试。'
                    : '公开手账加载失败，请稍后重试。'
                )
              : '追番记录加载失败，请稍后重试。',
        )
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadRecords()
    return () => controller.abort()
  }, [
    currentPage,
    isAdminJournalReview,
    isSharedJournal,
    location,
    navigate,
    pageSize,
    paginatedFilterKey,
    recordsRefreshKey,
    sharedUsername,
  ])

  useEffect(() => {
    if (isSharedJournal) {
      setFeaturedSubmissions([])
      return undefined
    }
    const controller = new AbortController()

    api.get('featured-submissions/state/', { signal: controller.signal })
      .then((response) => {
        setFeaturedSubmissions(response.data.submissions ?? [])
        setFeaturedLimit(Number(response.data.max_featured_limit) || 0)
      })
      .catch((requestError) => {
        if (requestError.code !== 'ERR_CANCELED') {
          showToast('精选专栏状态加载失败，请稍后刷新重试。', 'error')
        }
      })

    return () => controller.abort()
  }, [featuredRefreshKey, isSharedJournal, showToast])

  useEffect(() => {
    if (isSharedJournal) return undefined
    const controller = new AbortController()
    let isActive = true
    let isPolling = false

    async function pollNotifications() {
      if (!isActive || isPolling) return
      isPolling = true
      try {
        const response = await api.get('notifications/poll/', {
          signal: controller.signal,
        })
        const notifications = Array.isArray(response.data) ? response.data : []
        notifications.forEach((notification) => {
          const isNegative = /抱歉|未能|失败|拒绝/.test(notification.message)
          showToast(notification.message, isNegative ? 'error' : 'success')
        })
      } catch (requestError) {
        if (
          requestError.code !== 'ERR_CANCELED'
          && requestError.response?.status !== 401
        ) {
          showToast('站内通知读取失败，请稍后重试。', 'error')
        }
      } finally {
        isPolling = false
      }
    }

    function handleVisibilityChange() {
      if (document.visibilityState === 'visible') {
        pollNotifications()
      }
    }

    pollNotifications()
    const pollingTimer = window.setInterval(pollNotifications, 30000)
    window.addEventListener('focus', pollNotifications)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      isActive = false
      window.clearInterval(pollingTimer)
      window.removeEventListener('focus', pollNotifications)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      controller.abort()
    }
  }, [isSharedJournal, showToast])

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
      const priorityDifference = compareRatedPriority(a, b)
      if (priorityDifference !== 0) return priorityDifference

      let primaryDifference = 0
      switch (sortOrder) {
        case 'date-asc':
          primaryDifference = comparePeriod(a.period, b.period)
          break
        case 'score-desc':
          primaryDifference = compareScores(a, b, 'desc')
          break
        case 'score-asc':
          primaryDifference = compareScores(a, b, 'asc')
          break
        case 'updated-desc':
          primaryDifference = new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
          break
        case 'updated-asc':
          primaryDifference = new Date(a.updatedAt).getTime() - new Date(b.updatedAt).getTime()
          break
        case 'date-desc':
        default:
          primaryDifference = comparePeriod(b.period, a.period)
      }

      return primaryDifference || compareCompletedTieBreaker(a, b)
    })
  }, [activeCustomFilter, db, pageSize, searchQuery, sortOrder, statusFilter, tagFilter, typeFilter, yearFilter])

  const approvedFeaturedCount = useMemo(
    () => featuredSubmissions.filter(
      (submission) => submission.audit_status === 'approved',
    ).length,
    [featuredSubmissions],
  )
  const featuredAction = useMemo(() => {
    if (isSharedJournal || !selectedItem) return null

    const relatedSubmissions = featuredSubmissions.filter(
      (submission) => submission.source_anime_id === selectedItem.animeId,
    )
    const related = relatedSubmissions.find(
      (submission) => submission.audit_status === 'pending',
    ) || relatedSubmissions.find(
      (submission) => submission.audit_status === 'removal_requested',
    ) || relatedSubmissions.find(
      (submission) => submission.audit_status === 'approved',
    )

    if (related?.audit_status === 'pending') {
      return { state: 'pending', submission: related }
    }
    if (related?.audit_status === 'removal_requested') {
      return { state: 'removal_requested', submission: related }
    }
    if (related?.audit_status === 'approved') {
      return { state: 'approved', submission: related }
    }

    const hasProcessingSubmission = featuredSubmissions.some(
      (submission) => (
        submission.audit_status === 'pending'
        || submission.audit_status === 'removal_requested'
      ),
    )
    if (hasProcessingSubmission) return { state: 'blocked', submission: null }
    if (approvedFeaturedCount >= featuredLimit) {
      return { state: 'limit', submission: null }
    }
    return { state: 'default', submission: null }
  }, [
    approvedFeaturedCount,
    featuredLimit,
    featuredSubmissions,
    isSharedJournal,
    selectedItem,
  ])

  const visibleUnratedCount = filteredAnime.reduce(
    (count, item) => count + (item.score === null ? 1 : 0),
    0,
  )
  const hasLoadedRecords = recordsTotal !== null
  const hasAnyRecords = Number(statsData?.total_count ?? recordsTotal ?? 0) > 0
  const virtualItemCount = isEditMode && Number(recordsTotal) > 0 ? 1 : 0
  const totalPages = pageSize === 'all'
    ? 1
    : Math.max(1, Math.ceil(((recordsTotal ?? 0) + virtualItemCount) / Number(pageSize)))
  const listStartIndex = pageSize === 'all'
    ? 0
    : (currentPage - 1) * Number(pageSize)
  const placeholderPage = pageSize === 'all'
    ? 1
    : Math.floor((recordsTotal ?? 0) / Number(pageSize)) + 1
  const showAddPlaceholder = isEditMode
    && hasLoadedRecords
    && !isLoading
    && Number(recordsTotal) > 0
    && (pageSize === 'all' ? filteredAnime.length > 0 : currentPage === placeholderPage)
  const hasCompactResult = filteredAnime.length <= 1

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
    setLogoutDestination('login')
    setIsLogoutConfirmOpen(true)
  }

  const handleReturnHome = () => {
    setLogoutDestination('home')
    setIsLogoutConfirmOpen(true)
  }

  const handleSharedJournalExport = async () => {
    if (!isSharedJournal || isSharedExporting) return
    setIsSharedExporting(true)
    try {
      const sharedBase = isAdminJournalReview
        ? `admin/public-journals/${encodeURIComponent(sharedUsername)}`
        : `public-journal/users/${encodeURIComponent(sharedUsername)}`
      const response = await api.get(`${sharedBase}/records/`, {
        params: { page_size: 'all' },
      })
      const exportedRecords = Array.isArray(response.data)
        ? response.data
        : response.data?.results ?? []
      const payload = {
        version: 1,
        exported_at: new Date().toISOString(),
        owner: {
          username: sharedProfile?.username || sharedUsername,
          nickname: sharedProfile?.nickname || '',
          showcase_subtitle: sharedProfile?.showcase_subtitle || '',
        },
        records: exportedRecords,
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json;charset=utf-8',
      })
      const blobUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      const safeOwner = (sharedProfile?.nickname || sharedUsername || 'shared-journal')
        .replace(/[\\/:*?"<>|]/g, '-')
      link.href = blobUrl
      link.download = `${safeOwner}-公开番剧手账-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(blobUrl)
      showToast('公开手账数据已导出。', 'success')
    } catch (requestError) {
      showToast(requestError.response?.data?.detail || '公开手账导出失败，请稍后重试。', 'error')
    } finally {
      setIsSharedExporting(false)
    }
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
    clearTokens()
    navigate(logoutDestination === 'home' ? '/' : '/login', { replace: true })
  }

  const handleRecordUpdated = (updatedRecord) => {
    if (!isSharedJournal && !isAdminJournalReview) notifyRecommendationChange()
    setRecords((current) => current.map((record) => (
      record.id === updatedRecord.id ? updatedRecord : record
    )))
    setSelectedItem(mapRecordToAnimeItem(updatedRecord))
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleProgressIncrement = async (item) => {
    if (isSharedJournal || item.status !== 'watching') return
    if (progressUpdatingIdsRef.current.has(item.id)) return
    if (item.totalEps > 0 && item.watchedEps >= item.totalEps) return

    const previousRecord = records.find((record) => record.id === item.id)
    if (!previousRecord) return
    const optimisticRecord = {
      ...previousRecord,
      watched_eps: Number(previousRecord.watched_eps || 0) + 1,
    }
    progressUpdatingIdsRef.current.add(item.id)
    setProgressUpdatingIds(new Set(progressUpdatingIdsRef.current))
    setRecords((current) => current.map((record) => (
      record.id === item.id ? optimisticRecord : record
    )))
    setSelectedItem((current) => (
      current?.id === item.id ? mapRecordToAnimeItem(optimisticRecord) : current
    ))

    try {
      const response = await api.patch(`records/${item.id}/progress/`, { increment: 1 })
      setRecords((current) => current.map((record) => (
        record.id === item.id ? response.data : record
      )))
      setSelectedItem((current) => (
        current?.id === item.id ? mapRecordToAnimeItem(response.data) : current
      ))
      if (response.data.suggest_completed) {
        showToast('已追至最后一集，可以将状态改为“看过”啦！', 'success')
      }
    } catch (requestError) {
      setRecords((current) => current.map((record) => (
        record.id === item.id ? previousRecord : record
      )))
      setSelectedItem((current) => (
        current?.id === item.id ? mapRecordToAnimeItem(previousRecord) : current
      ))
      showToast(requestError.response?.data?.detail || '进度更新失败，请稍后重试。', 'error')
    } finally {
      progressUpdatingIdsRef.current.delete(item.id)
      setProgressUpdatingIds(new Set(progressUpdatingIdsRef.current))
    }
  }

  const handleRecordDeleted = (recordId) => {
    if (!isSharedJournal && !isAdminJournalReview) notifyRecommendationChange()
    setRecords((current) => current.filter((record) => record.id !== recordId))
    setSelectedItem(null)
    const remainingTotal = Math.max(0, Number(recordsTotal ?? 0) - 1)
    recordsTotalRef.current = remainingTotal
    const nextTotalPages = pageSize === 'all'
      ? 1
      : Math.max(1, Math.ceil((remainingTotal + (remainingTotal > 0 ? 1 : 0)) / Number(pageSize)))
    if (currentPage > nextTotalPages) setCurrentPage(nextTotalPages)
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleRecordAdded = (newRecord) => {
    if (!isSharedJournal && !isAdminJournalReview) notifyRecommendationChange()
    // Keep the POST response (including its newly persisted R2 poster URL) in
    // local state immediately; the refresh below remains the source of truth
    // for pagination, sorting and aggregate counts.
    setRecords((current) => {
      const remaining = current.filter((record) => record.id !== newRecord.id)
      return [newRecord, ...remaining]
    })
    setCurrentPage(1)
    setRecordsRefreshKey((key) => key + 1)
  }

  const handleRecordsImported = () => {
    if (!isSharedJournal && !isAdminJournalReview) notifyRecommendationChange()
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

  const openFeaturedConfirmation = () => {
    if (!selectedItem || !featuredAction) return
    if (featuredAction.state === 'default') {
      setFeaturedConfirmAction({
        type: 'submit',
        item: selectedItem,
        submission: null,
      })
    } else if (featuredAction.state === 'approved') {
      setFeaturedConfirmAction({
        type: 'removal',
        item: selectedItem,
        submission: featuredAction.submission,
      })
    }
  }

  const performFeaturedAction = async (action) => {
    setIsFeaturedActionProcessing(true)
    try {
      const response = action.type === 'submit'
        ? await api.post('featured-submissions/submit/', {
            anime_id: action.item.animeId,
          })
        : await api.post(
            `featured-submissions/${action.submission.id}/request-removal/`,
          )
      const updatedSubmission = response.data
      setFeaturedSubmissions((current) => {
        const exists = current.some(
          (submission) => submission.id === updatedSubmission.id,
        )
        return exists
          ? current.map((submission) => (
              submission.id === updatedSubmission.id
                ? updatedSubmission
                : submission
            ))
          : [updatedSubmission, ...current]
      })
      showToast(
        action.type === 'submit'
          ? '已提交，请留意后续结果。'
          : '撤下申请已提交，请留意后续结果。',
        'success',
      )
      return true
    } catch (requestError) {
      const detail = requestError.response?.data?.detail
      showToast(
        detail || (
          action.type === 'submit'
            ? '提交失败，请稍后重试。'
            : '撤下申请失败，请稍后重试。'
        ),
        'error',
      )
      if ([403, 409].includes(requestError.response?.status)) {
        setFeaturedRefreshKey((key) => key + 1)
      }
      return false
    } finally {
      setIsFeaturedActionProcessing(false)
    }
  }

  const togglePreviewMode = () => {
    if (isSharedJournal) return
    setSelectedItem(null)
    setIsQuickFilterEditorOpen(false)
    if (!isPreviewMode && pageSize !== 'all') {
      const realTotalPages = Math.max(1, Math.ceil(Number(recordsTotal ?? 0) / Number(pageSize)))
      setCurrentPage((page) => Math.min(page, realTotalPages))
    }
    if (isPreviewMode) {
      // Re-entering edit mode always reloads server-normalized metadata (for
      // example, a Bangumi poster that has just been persisted to R2).
      setRecordsRefreshKey((key) => key + 1)
    }
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
      className={`memphis-public memphis-dashboard ${isPreviewMode ? 'dashboard-preview-mode' : 'dashboard-edit-mode'} relative min-h-screen overflow-x-clip bg-[#fff5ee] text-black ${hasCompactResult ? 'pb-0' : 'pb-8'}`}
    >
      <div className="memphis-grid-texture pointer-events-none absolute inset-0" aria-hidden="true" />
      <div className="memphis-float-ring pointer-events-none absolute right-[-64px] top-[30rem] z-0 h-32 w-32 rounded-full border-[18px] border-black bg-[#4ecdc4]" aria-hidden="true" />
      <div
        className={`memphis-float-triangle pointer-events-none absolute left-[-32px] z-0 h-0 w-0 ${
          hasCompactResult ? 'bottom-28' : 'top-[54rem]'
        }`}
        aria-hidden="true"
      />
      <div className="memphis-float-zigzag pointer-events-none absolute bottom-24 right-0 z-0 h-16 w-44" aria-hidden="true" />

      <DashboardHeader
        isPreviewMode={isPreviewMode}
        onTogglePreview={togglePreviewMode}
        onAddAnime={() => setIsAddModalOpen(true)}
        onImportBangumi={() => setIsBangumiImportOpen(true)}
        onReturnHome={handleReturnHome}
        onRecordsImported={handleRecordsImported}
        onFindResources={() => openResourceSearch()}
        onExportCsv={() => {
          try {
            downloadJournalCsv(filteredAnime)
            showToast(`已导出当前筛选列表的 ${filteredAnime.length} 条记录${pageSize !== 'all' ? '（当前页）' : ''}。`)
          } catch { showToast('CSV 导出失败，请稍后重试。', 'error') }
        }}
        csvCount={filteredAnime.length}
        csvDisabled={isLoading || Boolean(error) || !hasLoadedRecords
          || (pageSize !== 'all' && (lastLoadedPage !== currentPage || searchQuery.trim() !== debouncedSearchQuery))}
        onLogout={handleLogout}
        onAccountDeleted={handleAccountDeleted}
        onUnauthorized={forceLogout}
        showToast={showToast}
        isSharedJournal={isSharedJournal}
        sharedProfile={sharedProfile ?? { username: sharedUsername }}
        onReturnToUniverse={() => navigate(
          isAdminJournalReview ? '/admin-dashboard/public-journals' : '/universe',
        )}
        onExportSharedJournal={handleSharedJournalExport}
        isSharedExporting={isSharedExporting}
        returnLabel={isAdminJournalReview ? '返回通行证审批' : '返回共创大厅'}
      />

      {isPreviewMode && isHeaderEntranceComplete && hasLoadedRecords && !error && (
        <div className="dashboard-content-piece dashboard-entrance-piece relative z-20 mx-auto -mt-6 max-w-7xl px-4 sm:px-6 lg:px-8">
          <StatsSummary db={db} statsData={statsData} theme="memphis" />
        </div>
      )}

      <section className={`relative z-10 mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 ${
        isPreviewMode
          ? `${hasCompactResult ? 'pb-0' : 'pb-8'} pt-2`
          : hasCompactResult
            ? 'pb-0 pt-7'
            : 'py-7'
      }`}>
        {!isSharedJournal && !isAdminJournalReview && <ShowcasePublicationPanel refreshKey={recordsRefreshKey} />}
        {isHeaderEntranceComplete && isLoading && !hasLoadedRecords && (
          <div className="flex items-center justify-center gap-3 border-4 border-black bg-[#ffe66d] py-12 font-black text-black shadow-[8px_8px_0_#4ecdc4]">
            <i className="fa-solid fa-spinner animate-spin text-xl"></i>
            {isSharedJournal
              ? (isAdminJournalReview ? '正在读取待审手账...' : '正在同步公开手账...')
              : '正在读取你的追番手账...'}
          </div>
        )}

        {isHeaderEntranceComplete && error && (
          <div role="alert" className="border-4 border-black bg-[#ff6b6b] px-5 py-5 font-black text-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-circle-exclamation mr-2"></i>
            {error}
          </div>
        )}

        {isHeaderEntranceComplete && hasLoadedRecords && !error && (
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

            {isContentEntranceComplete && (filteredAnime.length > 0 || showAddPlaceholder) && view === 'list' && (
              <div className="dashboard-content-piece">
              <AnimeList
                items={filteredAnime}
                sortOrder={sortOrder}
                setSortOrder={setSortOrder}
                onOpenDetail={setSelectedItem}
                startIndex={listStartIndex}
                theme="memphis"
                editable={!isPreviewMode}
                showAddPlaceholder={showAddPlaceholder}
                onAddAnime={() => setIsAddModalOpen(true)}
                onIncrementProgress={isEditMode ? handleProgressIncrement : undefined}
                progressUpdatingIds={progressUpdatingIds}
                onFindResources={isSharedJournal ? undefined : openResourceSearch}
              />
              </div>
            )}

            {isContentEntranceComplete && (filteredAnime.length > 0 || showAddPlaceholder) && view === 'grid' && (
              <div className="dashboard-content-piece">
                <AnimeGrid
                  items={filteredAnime}
                  onOpenDetail={setSelectedItem}
                  theme="memphis"
                  editable={!isPreviewMode}
                  showAddPlaceholder={showAddPlaceholder}
                  onAddAnime={() => setIsAddModalOpen(true)}
                  onIncrementProgress={isEditMode ? handleProgressIncrement : undefined}
                  progressUpdatingIds={progressUpdatingIds}
                />
              </div>
            )}

            {isContentEntranceComplete && filteredAnime.length === 0 && !hasAnyRecords && !isSharedJournal && (
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

            {isContentEntranceComplete && filteredAnime.length === 0 && !hasAnyRecords && isSharedJournal && (
              <div className="dashboard-content-piece border-4 border-black bg-white px-6 py-14 text-center shadow-[8px_8px_0_#4ecdc4]">
                <span className="mx-auto flex h-16 w-16 items-center justify-center border-4 border-black bg-[#ffe66d] text-2xl shadow-[5px_5px_0_#111]">
                  <i className="fa-solid fa-book-open" />
                </span>
                <h2 className="mt-5 text-2xl font-black">这本公开手账暂时没有记录</h2>
                <p className="mt-2 text-sm font-bold text-zinc-600">主理人添加内容后，这里会实时同步显示。</p>
              </div>
            )}

            {isContentEntranceComplete && filteredAnime.length === 0 && hasAnyRecords && !showAddPlaceholder && (
              <div className="dashboard-content-piece relative overflow-hidden border-4 border-black bg-white px-5 py-14 text-center shadow-[9px_9px_0_#ff6b6b]">
                <span className="absolute -left-7 -top-7 h-20 w-20 rotate-12 border-4 border-black bg-[#4ecdc4]" aria-hidden="true"></span>
                <span className="absolute -bottom-8 -right-5 h-20 w-20 rounded-full border-4 border-black bg-[#ff8fab]" aria-hidden="true"></span>
                <span className="relative mx-auto flex h-20 w-20 rotate-3 items-center justify-center border-4 border-black bg-[#ffe66d] text-3xl text-black shadow-[6px_6px_0_#111]">
                  <i className="fa-solid fa-filter-circle-xmark"></i>
                </span>
                <h2 className="relative mt-6 text-2xl font-black text-black">没有符合条件的追番记录</h2>
                <p className="relative mt-2 inline-block border-2 border-black bg-[#fff5ee] px-4 py-2 text-sm font-bold text-zinc-600 shadow-[3px_3px_0_#4ecdc4]">
                  尝试调整搜索或过滤条件。
                </p>
              </div>
            )}

            {isContentEntranceComplete && pageSize !== 'all' && recordsTotal > 0 && (
              <PageNavigation
                currentPage={currentPage}
                totalPages={totalPages}
                hasPrevious={currentPage > 1}
                hasNext={currentPage < totalPages}
                isLoading={isLoading}
                onPageChange={handlePageChange}
              />
            )}
          </>
        )}
      </section>

      {isPreviewMode || isSharedJournal ? (
        <DetailModal
          selectedItem={selectedItem}
          closeModal={() => setSelectedItem(null)}
          isPrivateJournal={!isSharedJournal}
          explorationTags={explorationTags}
          onSimilar={!isSharedJournal ? item => navigate('/recommendations/similar', { state: { journalRecord: item.id } }) : undefined}
          showToast={showToast}
          featuredAction={featuredAction ? {
            ...featuredAction,
            isProcessing: isFeaturedActionProcessing,
          } : null}
          onFeaturedAction={openFeaturedConfirmation}
        />
      ) : (
        <EditableDetailModal
          key={selectedItem?.id ?? 'closed-edit-modal'}
          selectedItem={selectedItem}
          closeModal={() => setSelectedItem(null)}
          onRecordUpdated={handleRecordUpdated}
          onRecordDeleted={handleRecordDeleted}
          showToast={showToast}
        />
      )}
      {!isSharedJournal && (
        <>
          <FeaturedActionConfirmModal
            action={featuredConfirmAction}
            onClose={() => setFeaturedConfirmAction(null)}
            onConfirm={performFeaturedAction}
          />
          <AddAnimeModal
            isOpen={isAddModalOpen}
            onClose={() => setIsAddModalOpen(false)}
            existingAnimeIds={new Set(db.map((item) => item.animeId))}
            existingAnimeItems={db}
            onRecordAdded={handleRecordAdded}
            showToast={showToast}
          />
          <BangumiImportModal
            isOpen={isBangumiImportOpen}
            onClose={() => setIsBangumiImportOpen(false)}
            onCompleted={handleRecordsImported}
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
        </>
      )}
      {!isSharedJournal && isLogoutConfirmOpen && (
        <div
          className="memphis-modal-backdrop fixed inset-0 z-[140] flex items-center justify-center bg-zinc-950/80 p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both]"
          role="dialog"
          aria-modal="true"
          aria-labelledby="logoutConfirmTitle"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsLogoutConfirmOpen(false)
          }}
        >
          <section className="relative w-full max-w-md border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]" onMouseDown={(event) => event.stopPropagation()}>
            <div className="h-4 border-b-4 border-black bg-[repeating-linear-gradient(135deg,#ff6b6b_0,#ff6b6b_18px,#ffe66d_18px,#ffe66d_36px)]"></div>
            <span className="absolute right-5 top-8 grid grid-cols-3 gap-1.5 opacity-30" aria-hidden="true">
              {Array.from({ length: 9 }, (_, index) => <i key={index} className="h-2 w-2 rounded-full bg-black"></i>)}
            </span>
            <div className="px-7 py-8 text-center">
              <span className="mx-auto flex h-20 w-20 -rotate-3 items-center justify-center border-4 border-black bg-[#ff6b6b] text-3xl text-black shadow-[6px_6px_0_#ffe66d]">
                <i className={`fa-solid ${logoutDestination === 'home' ? 'fa-house' : 'fa-arrow-right-from-bracket'}`}></i>
              </span>
              <span className="mt-6 inline-block border-2 border-black bg-[#ffe66d] px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] shadow-[3px_3px_0_#111]">
                {logoutDestination === 'home' ? 'Back To Showcase' : 'Session Exit'}
              </span>
              <h2 id="logoutConfirmTitle" className="mt-4 text-2xl font-black">
                {logoutDestination === 'home' ? '确定回到主界面吗？' : '确定退出登录吗？'}
              </h2>
              <p className="mx-auto mt-3 max-w-xs border-l-[6px] border-black bg-white px-4 py-3 text-sm font-bold leading-6 text-zinc-600">
                {logoutDestination === 'home'
                  ? '返回展示主界面会登出当前账号，再次进入私人手账时需要重新登录。'
                  : '退出后需要重新登录才能进入你的私人手账。'}
              </p>
              <div className="mt-7 grid grid-cols-2 gap-4">
                <button type="button" onClick={() => setIsLogoutConfirmOpen(false)} className="border-[3px] border-black bg-white px-4 py-3 text-sm font-black shadow-[5px_5px_0_#4ecdc4] transition-none active:translate-x-[5px] active:translate-y-[5px] active:shadow-none">继续整理</button>
                <button type="button" onClick={confirmLogout} className="border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[5px_5px_0_#111] transition-none hover:bg-[#ff8fab] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none">
                  {logoutDestination === 'home' ? '确认返回' : '确认退出'}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
      {!isSharedJournal && resourceSearch && <JournalResourceSearch key={resourceSearch.key}
        title={resourceSearch.title} autoSearch={resourceSearch.autoSearch}
        onClose={closeResourceSearch} showToast={showToast} />}
      <BackToTop
        isModalOpen={Boolean(selectedItem) || (!isSharedJournal && (
          isLogoutConfirmOpen || isBangumiImportOpen || Boolean(featuredConfirmAction) || Boolean(resourceSearch)
        ))}
        theme="memphis"
      />
      <MemphisToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  )
}

export default Dashboard
