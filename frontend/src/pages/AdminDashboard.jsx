import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'
import AdminAuditConfirmModal from '../components/AdminAuditConfirmModal.jsx'
import AdminPublicJournalPanel from '../components/AdminPublicJournalPanel.jsx'
import DetailModal from '../components/DetailModal.jsx'
import FilterBar from '../components/FilterBar.jsx'
import MemphisToastStack from '../components/MemphisToastStack.jsx'
import QuickFilterEditorModal from '../components/QuickFilterEditorModal.jsx'
import { comparePeriod } from '../utils/animeDisplay.js'
import {
  formatAiringPeriod,
  mapFeaturedSubmission,
} from '../utils/featuredDisplay.js'
import { matchesCustomQuickFilter } from '../utils/quickFilter.js'

gsap.registerPlugin(useGSAP)

const STATUS_META = {
  pending: {
    label: '待审核投稿',
    icon: 'fa-hourglass-half',
    className: 'bg-[#ffdf3e]',
  },
  removal_requested: {
    label: '请求撤下',
    icon: 'fa-arrow-down',
    className: 'bg-[#ff5b68]',
  },
}

const ADMIN_MODULES = [
  {
    key: 'featured',
    number: '01',
    eyebrow: 'Featured Column',
    title: '精选专栏',
    description: '审核用户投稿、处理撤下申请，并发送结果通知。',
    icon: 'fa-star',
    color: 'bg-[#ffdf3e]',
    shadow: 'shadow-[9px_9px_0_#ff5b68]',
    status: '已启用',
    enabled: true,
  },
  {
    key: 'public-journals',
    number: '02',
    eyebrow: 'Public Journal Pass',
    title: '通行证审批',
    description: '审阅用户完整手账，发放、打回并吊销番剧共创公开权限。',
    icon: 'fa-ticket',
    color: 'bg-[#4ecdc4]',
    shadow: 'shadow-[9px_9px_0_#ffdf3e]',
    status: '已启用',
    enabled: true,
  },
  {
    key: 'module-3',
    number: '03',
    eyebrow: 'Future Module',
    title: '模块 3',
    description: '可继续扩展用户、内容或系统配置功能。',
    icon: 'fa-diagram-project',
    color: 'bg-[#ff5b68]',
    shadow: 'shadow-[9px_9px_0_#4ecdc4]',
    status: '待接入',
    enabled: false,
  },
]

function normalizeNextUrl(nextUrl) {
  if (!nextUrl) return null
  const parsed = new URL(nextUrl, window.location.origin)
  return `${parsed.pathname.replace(/^\/api\//, '')}${parsed.search}`
}

function getErrorMessage(error, fallback = '操作失败，请稍后重试。') {
  const data = error.response?.data
  if (typeof data?.detail === 'string') return data.detail
  const firstMessage = data && Object.values(data).flat().find(
    (message) => typeof message === 'string',
  )
  return firstMessage || fallback
}

function AdminDashboard() {
  const pageRef = useRef(null)
  const navigate = useNavigate()
  const { moduleKey } = useParams()
  const currentModule = ADMIN_MODULES.find((module) => module.key === moduleKey)
  const isMainMenu = !moduleKey
  const isFeaturedModule = moduleKey === 'featured'
  const isPublicJournalModule = moduleKey === 'public-journals'
  const [admin, setAdmin] = useState(null)
  const [submissions, setSubmissions] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmAction, setConfirmAction] = useState(null)
  const [selectedSubmission, setSelectedSubmission] = useState(null)
  const [processingId, setProcessingId] = useState(null)
  const [toasts, setToasts] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('all')
  const [yearFilter, setYearFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('date-desc')
  const [typeFilter, setTypeFilter] = useState('all')
  const [featuredQuickFilters, setFeaturedQuickFilters] = useState([])
  const [featuredAvailableTags, setFeaturedAvailableTags] = useState([])
  const [activeCustomFilterId, setActiveCustomFilterId] = useState(null)
  const [isQuickFilterEditorOpen, setIsQuickFilterEditorOpen] = useState(false)

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    gsap.timeline()
      .from('.admin-console-header', {
        y: -70,
        autoAlpha: 0,
        duration: 0.7,
        ease: 'bounce.out',
      })
      .from('.admin-console-panel', {
        y: 45,
        scale: 0.96,
        autoAlpha: 0,
        duration: 0.5,
        ease: 'back.out(1.5)',
      }, '-=0.2')
  }, {
    scope: pageRef,
    dependencies: [moduleKey],
    revertOnUpdate: true,
  })

  function pushToast(message, type = 'success') {
    setToasts((current) => [
      ...current,
      { id: `${Date.now()}-${Math.random()}`, message, type },
    ])
  }

  async function loadAdminSession() {
    try {
      const response = await api.get('admin/session/')
      setAdmin(response.data)
    } catch (requestError) {
      setError(getErrorMessage(requestError, '无法读取管理员身份信息。'))
    }
  }

  async function loadQueue() {
    setIsLoading(true)
    setError('')
    try {
      const filtersPromise = api.get('featured-filters/', {
        params: { page_size: 100 },
      })
      const availableTagsPromise = api.get('featured-filters/available-tags/')
      const allItems = []
      let nextUrl = 'admin/featured-submissions/?page_size=100'

      while (nextUrl) {
        // The loop keeps the control room complete even when the global DRF
        // paginator splits a large audit queue across multiple pages.
        const response = await api.get(nextUrl)
        const payload = response.data
        if (Array.isArray(payload)) {
          allItems.push(...payload)
          nextUrl = null
        } else {
          allItems.push(...(payload.results || []))
          nextUrl = normalizeNextUrl(payload.next)
        }
      }

      const filtersResponse = await filtersPromise
      const availableTagsResponse = await availableTagsPromise
      const filtersPayload = filtersResponse.data
      setSubmissions(allItems)
      setFeaturedAvailableTags(availableTagsResponse.data.tags ?? [])
      setFeaturedQuickFilters(
        Array.isArray(filtersPayload)
          ? filtersPayload
          : filtersPayload.results ?? [],
      )
    } catch (requestError) {
      setError(getErrorMessage(requestError, '无法读取审核队列，请检查后端服务。'))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadAdminSession()
  }, [])

  useEffect(() => {
    if (isFeaturedModule) {
      loadQueue()
    } else {
      setIsLoading(false)
      setError('')
    }
  }, [isFeaturedModule])

  async function handleAudit() {
    if (!confirmAction) return
    const { action, submission } = confirmAction
    setProcessingId(submission.id)
    try {
      const response = await api.post(
        `admin/featured-submissions/${submission.id}/${action}/`,
      )
      setSubmissions((current) => current.filter((item) => item.id !== submission.id))
      setConfirmAction(null)
      pushToast(response.data.detail || '审核结果已保存，并已通知用户。')
    } catch (requestError) {
      pushToast(getErrorMessage(requestError), 'error')
    } finally {
      setProcessingId(null)
    }
  }

  function handleLogout() {
    clearTokens()
    navigate('/admin-login', { replace: true })
  }

  const pendingCount = submissions.filter(
    (item) => item.audit_status === 'pending',
  ).length
  const removalCount = submissions.length - pendingCount
  const availableTags = useMemo(
    () => [...new Set([
      ...featuredAvailableTags,
      ...submissions.flatMap((submission) => submission.tags ?? []),
    ])].sort(),
    [featuredAvailableTags, submissions],
  )
  const availableYears = useMemo(
    () => [...new Set(
      submissions
        .map((submission) => submission.year)
        .filter(Boolean)
        .map(String),
    )].sort((left, right) => Number(right) - Number(left)),
    [submissions],
  )
  const filteredSubmissions = useMemo(() => {
    const search = searchQuery.trim().toLowerCase()
    const activeQuickFilter = featuredQuickFilters.find(
      (filter) => filter.id === activeCustomFilterId,
    )

    return submissions
      .filter((submission) => {
        const submitter = submission.submitter ?? {}
        const searchableText = [
          submission.chinese_title,
          submission.japanese_title,
          submitter.nickname,
          submitter.username,
          submitter.email,
        ].filter(Boolean).join(' ').toLowerCase()
        const filterItem = {
          title: submission.chinese_title ?? '',
          tags: submission.tags ?? [],
        }
        return (!search || searchableText.includes(search))
          && (tagFilter === 'all' || filterItem.tags.includes(tagFilter))
          && (yearFilter === 'all' || String(submission.year) === yearFilter)
          && (statusFilter === 'all' || submission.watch_status === statusFilter)
          && matchesCustomQuickFilter(filterItem, activeQuickFilter)
      })
      .sort((left, right) => {
        if (sortOrder === 'score-desc') return (right.score ?? -1) - (left.score ?? -1)
        if (sortOrder === 'score-asc') return (left.score ?? 11) - (right.score ?? 11)
        const leftPeriod = left.airing_period || String(left.year || '')
        const rightPeriod = right.airing_period || String(right.year || '')
        if (sortOrder === 'date-asc') return comparePeriod(leftPeriod, rightPeriod)
        return comparePeriod(rightPeriod, leftPeriod)
      })
  }, [
    activeCustomFilterId,
    featuredQuickFilters,
    searchQuery,
    sortOrder,
    statusFilter,
    submissions,
    tagFilter,
    yearFilter,
  ])
  const headerTitle = isMainMenu
    ? '管理员控制台'
    : currentModule?.title || '未知管理模块'
  const headerDescription = isMainMenu
    ? '先选择需要管理的功能模块，再进入对应的独立控制面板。'
    : currentModule?.description || '该管理模块不存在，请返回控制台主菜单。'

  function handleFeaturedQuickFilterSaved(savedFilter) {
    setFeaturedQuickFilters((current) => {
      const exists = current.some((filter) => filter.id === savedFilter.id)
      return exists
        ? current.map((filter) => filter.id === savedFilter.id ? savedFilter : filter)
        : [...current, savedFilter]
    })
  }

  function handleFeaturedQuickFilterDeleted(filterId) {
    setFeaturedQuickFilters((current) => (
      current.filter((filter) => filter.id !== filterId)
    ))
    setActiveCustomFilterId((current) => current === filterId ? null : current)
  }

  return (
    <main
      ref={pageRef}
      className="memphis-public admin-control-grid min-h-screen overflow-x-hidden bg-zinc-900 px-4 py-5 text-white sm:px-7 lg:px-10"
    >
      <header className="admin-console-header relative mx-auto max-w-[1500px] overflow-hidden border-4 border-black bg-zinc-800 p-5 shadow-[10px_10px_0_#c7ff4a] sm:p-7">
        <div className="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full border-[16px] border-[#ffdf3e]" />
        <div className="relative flex flex-col justify-between gap-6 lg:flex-row lg:items-center">
          <div className="flex items-center gap-4">
            <span className="flex h-16 w-16 shrink-0 items-center justify-center border-4 border-black bg-[#c7ff4a] text-2xl text-black shadow-[5px_5px_0_#ffdf3e]">
              <i className="fa-solid fa-tower-broadcast" />
            </span>
            <div>
              <span className="mb-1 inline-block border-2 border-black bg-[#ff5b68] px-2 py-1 text-[10px] font-black uppercase tracking-[0.28em] text-black">
                Control Room · {isMainMenu ? 'Main Menu' : `Module ${currentModule?.number || '--'}`}
              </span>
              <h1 className="text-3xl font-black tracking-tight sm:text-5xl">{headerTitle}</h1>
              <p className="mt-2 font-bold text-zinc-300">{headerDescription}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="border-4 border-black bg-white px-4 py-2 text-black shadow-[5px_5px_0_#ffdf3e]">
              <span className="block text-[9px] font-black uppercase tracking-[0.2em]">Authenticated Staff</span>
              <strong className="text-sm">{admin?.username || '超级管理员'}</strong>
            </div>
            {!isMainMenu && (
              <button
                type="button"
                onClick={() => navigate('/admin-dashboard')}
                className="border-4 border-black bg-white px-4 py-3 font-black text-black shadow-[5px_5px_0_#4ecdc4] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
              >
                <i className="fa-solid fa-table-cells-large mr-2" />
                返回主菜单
              </button>
            )}
            {isFeaturedModule && (
              <button
                type="button"
                onClick={loadQueue}
                disabled={isLoading}
                className="border-4 border-black bg-[#ffdf3e] px-4 py-3 font-black text-black shadow-[5px_5px_0_#c7ff4a] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-60"
              >
                <i className={`fa-solid ${isLoading ? 'fa-spinner animate-spin' : 'fa-rotate'} mr-2`} />
                刷新队列
              </button>
            )}
            <button
              type="button"
              onClick={handleLogout}
              className="border-4 border-black bg-[#ff5b68] px-4 py-3 font-black text-black shadow-[5px_5px_0_#ffdf3e] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
            >
              <i className="fa-solid fa-right-from-bracket mr-2" />
              安全退出
            </button>
          </div>
        </div>
      </header>

      {isMainMenu ? (
        <section className="admin-console-panel mx-auto mt-8 max-w-[1500px] border-4 border-black bg-[#fff5ee] p-5 text-black shadow-[10px_10px_0_#ffdf3e] sm:p-8">
          <div className="mb-7 border-b-4 border-black pb-5">
            <span className="inline-flex items-center gap-2 border-2 border-black bg-[#c7ff4a] px-3 py-1 text-xs font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
              <i className="fa-solid fa-table-cells-large" />
              Module Directory
            </span>
            <h2 className="mt-3 text-3xl font-black sm:text-4xl">选择管理模块</h2>
            <p className="mt-2 max-w-2xl font-bold leading-7 text-zinc-600">
              每个模块拥有独立的管理界面。后续增加功能时，只需在这里新增入口，不会再直接挤入精选专栏页面。
            </p>
          </div>

          <div className="grid gap-7 md:grid-cols-2 xl:grid-cols-3">
            {ADMIN_MODULES.map((module) => (
              <button
                key={module.key}
                type="button"
                onClick={() => navigate(`/admin-dashboard/${module.key}`)}
                className={`group relative min-h-72 border-4 border-black p-5 text-left text-black transition-none active:translate-x-[9px] active:translate-y-[9px] active:shadow-none ${module.color} ${module.shadow}`}
              >
                <span className="absolute right-4 top-4 border-2 border-black bg-white px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em]">
                  {module.status}
                </span>
                <span className="block text-6xl font-black leading-none opacity-20">{module.number}</span>
                <span className="mt-5 flex h-16 w-16 items-center justify-center border-4 border-black bg-white text-2xl shadow-[5px_5px_0_#111] group-active:shadow-none">
                  <i className={`fa-solid ${module.icon}`} />
                </span>
                <span className="mt-6 block text-[10px] font-black uppercase tracking-[0.24em]">{module.eyebrow}</span>
                <strong className="mt-1 block text-3xl font-black">{module.title}</strong>
                <span className="mt-3 block text-sm font-bold leading-6">{module.description}</span>
                <span className="mt-5 inline-flex items-center gap-2 border-2 border-black bg-zinc-900 px-3 py-2 text-xs font-black text-white">
                  {module.enabled ? '进入管理界面' : '查看预留模块'}
                  <i className="fa-solid fa-arrow-right" />
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : isFeaturedModule ? (
      <section className="admin-console-panel mx-auto mt-8 max-w-[1500px] border-4 border-black bg-[#fff5ee] p-4 text-black shadow-[10px_10px_0_#ffdf3e] sm:p-6">
        <div className="mb-6 flex flex-col justify-between gap-4 border-b-4 border-black pb-5 md:flex-row md:items-end">
          <div>
            <span className="mb-2 inline-flex items-center gap-2 border-2 border-black bg-[#c7ff4a] px-3 py-1 text-xs font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
              <i className="fa-solid fa-list-check" />
              Audit Queue
            </span>
            <h2 className="text-2xl font-black sm:text-3xl">管理精选番剧</h2>
          </div>
          <div className="flex flex-wrap gap-3 text-sm font-black">
            <span className="border-3 border-black bg-[#ffdf3e] px-4 py-2 shadow-[4px_4px_0_#111]">
              待审核 {pendingCount}
            </span>
            <span className="border-3 border-black bg-[#ff5b68] px-4 py-2 shadow-[4px_4px_0_#111]">
              待撤下 {removalCount}
            </span>
            <span className="border-3 border-black bg-white px-4 py-2 shadow-[4px_4px_0_#111]">
              总计 {submissions.length}
            </span>
          </div>
        </div>

        <FilterBar
          db={submissions.map(mapFeaturedSubmission)}
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
          view="list"
          setView={() => {}}
          filteredCount={filteredSubmissions.length}
          customQuickFilters={featuredQuickFilters}
          activeCustomFilterId={activeCustomFilterId}
          setActiveCustomFilterId={setActiveCustomFilterId}
          onOpenQuickFilterEditor={() => setIsQuickFilterEditorOpen(true)}
          availableTags={availableTags}
          availableYears={availableYears}
          enablePosterViewHint={false}
          showViewToggle={false}
          theme="memphis"
        />

        {error && (
          <div role="alert" className="mb-6 border-4 border-black bg-[#ff5b68] p-4 font-black shadow-[6px_6px_0_#111]">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            {error}
          </div>
        )}

        {isLoading ? (
          <div className="flex min-h-64 flex-col items-center justify-center border-4 border-black bg-white text-center shadow-[7px_7px_0_#c7ff4a]">
            <i className="fa-solid fa-radar mb-4 animate-pulse text-4xl" />
            <p className="font-black uppercase tracking-[0.18em]">正在扫描审核队列</p>
          </div>
        ) : submissions.length === 0 ? (
          <div className="flex min-h-64 flex-col items-center justify-center border-4 border-black bg-[#c7ff4a] p-8 text-center shadow-[7px_7px_0_#111]">
            <span className="mb-4 flex h-16 w-16 items-center justify-center border-4 border-black bg-white text-2xl shadow-[4px_4px_0_#ffdf3e]">
              <i className="fa-solid fa-check-double" />
            </span>
            <h3 className="text-2xl font-black">审核队列已清空</h3>
            <p className="mt-2 font-bold">目前没有待审核投稿或撤下申请。</p>
          </div>
        ) : filteredSubmissions.length === 0 ? (
          <div className="flex min-h-64 flex-col items-center justify-center border-4 border-black bg-white p-8 text-center shadow-[7px_7px_0_#ff5b68]">
            <span className="flex h-16 w-16 rotate-3 items-center justify-center border-4 border-black bg-[#ffdf3e] text-2xl shadow-[5px_5px_0_#111]">
              <i className="fa-solid fa-filter-circle-xmark" />
            </span>
            <h3 className="mt-5 text-2xl font-black">没有匹配的待审核信息</h3>
            <p className="mt-2 font-bold text-zinc-600">请调整关键词、标签、状态或快速筛选组合。</p>
          </div>
        ) : (
          <div className="space-y-5">
            {filteredSubmissions.map((submission, index) => {
              const meta = STATUS_META[submission.audit_status]
              const author = submission.submitter?.nickname
                || submission.submitter?.username
                || submission.user
              const isRemoval = submission.audit_status === 'removal_requested'

              return (
                <article
                  key={submission.id}
                  className="grid gap-5 border-4 border-black bg-white p-4 shadow-[7px_7px_0_#111] lg:grid-cols-[110px_minmax(0,1fr)_auto] lg:items-center"
                >
                  <div className="relative mx-auto w-[110px] lg:mx-0">
                    {submission.poster || submission.poster_url ? (
                      <img
                        src={submission.poster || submission.poster_url}
                        alt={`${submission.chinese_title}海报`}
                        className="aspect-[2/3] w-full border-4 border-black object-cover"
                      />
                    ) : (
                      <div className="flex aspect-[2/3] items-center justify-center border-4 border-black bg-[#ffdf3e] text-3xl">
                        <i className="fa-solid fa-image" />
                      </div>
                    )}
                    <span className="absolute -left-3 -top-3 border-2 border-black bg-[#c7ff4a] px-2 py-1 text-xs font-black">
                      #{String(index + 1).padStart(2, '0')}
                    </span>
                  </div>

                  <div className="min-w-0">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <span className={`border-2 border-black px-3 py-1 text-xs font-black shadow-[3px_3px_0_#111] ${meta.className}`}>
                        <i className={`fa-solid ${meta.icon} mr-1.5`} />
                        {meta.label}
                      </span>
                      <span className="border-2 border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black">
                        {submission.watch_status_display || submission.watch_status}
                      </span>
                      {submission.score !== null && submission.score !== undefined && (
                        <span className="border-2 border-black bg-[#ffdf3e] px-3 py-1 text-xs font-black">
                          <i className="fa-solid fa-star mr-1" />
                          {submission.score}
                        </span>
                      )}
                    </div>

                    <h3 className="truncate text-xl font-black sm:text-2xl">{submission.chinese_title}</h3>
                    <p className="mt-1 truncate text-sm font-bold text-zinc-500">
                      {submission.japanese_title || '暂无日文名'} · {formatAiringPeriod(
                        submission.airing_period || submission.year,
                      )}
                    </p>
                    <p className="mt-3 line-clamp-2 border-l-4 border-[#ff5b68] pl-3 text-sm font-bold leading-6 text-zinc-700">
                      {submission.review || '该投稿暂未填写个人长评。'}
                    </p>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span className="inline-flex items-center gap-2 border-2 border-black bg-[#4ecdc4] p-1 pr-2 font-black">
                        {submission.submitter?.avatar ? (
                          <img
                            src={submission.submitter.avatar}
                            alt={`${author}的头像`}
                            className="h-8 w-8 border-2 border-black bg-white object-cover"
                          />
                        ) : (
                          <span className="flex h-8 w-8 items-center justify-center border-2 border-black bg-white">
                            <i className="fa-solid fa-user-pen" />
                          </span>
                        )}
                        {author}
                      </span>
                      {submission.submitter?.email && (
                        <span className="text-xs font-bold text-zinc-500">{submission.submitter.email}</span>
                      )}
                      {(submission.tags || []).map((tag, tagIndex) => (
                        <span
                          key={`${submission.id}-${tag}`}
                          className={`-rotate-1 border-2 border-black px-2 py-1 text-xs font-black ${
                            tagIndex % 2 ? 'bg-[#4ecdc4]' : 'bg-[#ffdf3e]'
                          }`}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="grid min-w-[180px] grid-cols-2 gap-3 lg:grid-cols-1">
                    <button
                      type="button"
                      onClick={() => setSelectedSubmission(submission)}
                      className="col-span-2 border-4 border-black bg-white px-4 py-3 font-black shadow-[5px_5px_0_#4ecdc4] hover:bg-[#4ecdc4] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none lg:col-span-1"
                    >
                      <i className="fa-solid fa-eye mr-2" />
                      查看详细信息
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmAction({ action: 'approve', submission })}
                      className="border-4 border-black bg-[#c7ff4a] px-4 py-3 font-black shadow-[5px_5px_0_#111] hover:bg-[#ffdf3e] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
                    >
                      <i className={`fa-solid ${isRemoval ? 'fa-box-archive' : 'fa-check'} mr-2`} />
                      {isRemoval ? '批准撤下' : '通过收录'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmAction({ action: 'reject', submission })}
                      className="border-4 border-black bg-[#ff5b68] px-4 py-3 font-black shadow-[5px_5px_0_#111] hover:bg-white active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
                    >
                      <i className={`fa-solid ${isRemoval ? 'fa-rotate-left' : 'fa-xmark'} mr-2`} />
                      {isRemoval ? '驳回撤下' : '打回投稿'}
                    </button>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </section>
      ) : isPublicJournalModule ? (
        <AdminPublicJournalPanel />
      ) : (
        <section className="admin-console-panel mx-auto mt-8 flex min-h-[420px] max-w-[1100px] flex-col items-center justify-center border-4 border-black bg-[#fff5ee] p-8 text-center text-black shadow-[10px_10px_0_#4ecdc4]">
          <span className={`flex h-24 w-24 items-center justify-center border-4 border-black text-4xl shadow-[7px_7px_0_#111] ${currentModule?.color || 'bg-[#ff5b68]'}`}>
            <i className={`fa-solid ${currentModule?.icon || 'fa-triangle-exclamation'}`} />
          </span>
          <span className="mt-7 border-2 border-black bg-[#ffdf3e] px-3 py-1 text-xs font-black uppercase tracking-[0.18em]">
            {currentModule ? 'Reserved Module' : 'Unknown Module'}
          </span>
          <h2 className="mt-4 text-3xl font-black sm:text-5xl">
            {currentModule?.title || '未找到该管理模块'}
          </h2>
          <p className="mt-4 max-w-xl font-bold leading-7 text-zinc-600">
            {currentModule
              ? '该入口和独立路由已经准备完成，后续接入功能时可直接替换此占位面板。'
              : '你访问的后台模块不存在，请返回主菜单重新选择。'}
          </p>
          <button
            type="button"
            onClick={() => navigate('/admin-dashboard')}
            className="mt-8 border-4 border-black bg-[#c7ff4a] px-6 py-3 font-black shadow-[6px_6px_0_#111] active:translate-x-[6px] active:translate-y-[6px] active:shadow-none"
          >
            <i className="fa-solid fa-arrow-left mr-2" />
            返回控制台主菜单
          </button>
        </section>
      )}

      {confirmAction && (
        <AdminAuditConfirmModal
          action={confirmAction.action}
          submission={confirmAction.submission}
          isSubmitting={processingId === confirmAction.submission.id}
          onConfirm={handleAudit}
          onClose={() => setConfirmAction(null)}
        />
      )}
      <DetailModal
        selectedItem={
          selectedSubmission ? mapFeaturedSubmission(selectedSubmission) : null
        }
        closeModal={() => setSelectedSubmission(null)}
      />
      <MemphisToastStack
        toasts={toasts}
        onDismiss={(id) => setToasts((current) => current.filter((toast) => toast.id !== id))}
      />
      <QuickFilterEditorModal
        isOpen={isQuickFilterEditorOpen}
        onClose={() => setIsQuickFilterEditorOpen(false)}
        availableTags={availableTags}
        filters={featuredQuickFilters}
        onSaved={handleFeaturedQuickFilterSaved}
        onDeleted={handleFeaturedQuickFilterDeleted}
        showToast={pushToast}
        endpoint="featured-filters"
        description="由超级管理员维护，保存后会同步显示在所有用户的精选专栏页面。"
      />
    </main>
  )
}

export default AdminDashboard
