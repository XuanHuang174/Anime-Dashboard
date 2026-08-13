import { useMemo, useState } from 'react'
import SearchableSelect from './SearchableSelect.jsx'

const quickFilters = [
  { value: 'all', label: '全部' },
]

const activeCategoryClass =
  'cat-btn active px-3 py-1.5 bg-indigo-500 border border-indigo-600 border-b-[3px] border-b-indigo-700 text-white shadow-md shadow-indigo-300/50 active:translate-y-[2px] active:border-b-[1px] hover:-translate-y-0.5 rounded-full text-xs font-bold transition-all'
const inactiveCategoryClass =
  'cat-btn px-3 py-1.5 bg-white border border-slate-200 border-b-[3px] border-b-slate-300 text-slate-600 shadow-sm active:translate-y-[2px] active:border-b-[1px] hover:text-indigo-500 hover:-translate-y-0.5 hover:shadow-md rounded-full text-xs font-bold transition-all'
const activeViewClass =
  'px-3 py-1.5 rounded-lg bg-white shadow-sm text-indigo-600 transition-all text-xs font-bold flex items-center gap-1.5'
const inactiveViewClass =
  'px-3 py-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-200/50 transition-all text-xs font-bold flex items-center gap-1.5'

function FilterBar({
  db,
  searchQuery,
  setSearchQuery,
  tagFilter,
  setTagFilter,
  yearFilter,
  setYearFilter,
  sortOrder,
  setSortOrder,
  typeFilter,
  setTypeFilter,
  statusFilter = 'all',
  setStatusFilter = null,
  showRecordTimeSort = false,
  view,
  setView,
  filteredCount,
  customQuickFilters = [],
  activeCustomFilterId = null,
  setActiveCustomFilterId = null,
  onOpenQuickFilterEditor = null,
  onResetPagination = null,
  availableTags = null,
  availableYears = null,
  enablePosterViewHint = true,
  showViewToggle = true,
  theme = 'default',
}) {
  const [isMobileOpen, setIsMobileOpen] = useState(false)
  const [showViewHint, setShowViewHint] = useState(true)
  const shouldShowViewHint = enablePosterViewHint && showViewHint

  const tags = useMemo(
    () => availableTags ?? [...new Set(db.flatMap((item) => item.tags))].sort(),
    [availableTags, db],
  )
  const years = useMemo(
    () => availableYears ?? [...new Set(
      db.map((item) => item.period.split('-')[0]).filter((year) => !Number.isNaN(Number(year))),
    )].sort((a, b) => b - a),
    [availableYears, db],
  )

  const dismissViewHint = () => {
    setShowViewHint(false)
  }

  const resetFilters = () => {
    setSearchQuery('')
    setTagFilter('all')
    setYearFilter('all')
    setSortOrder('date-desc')
    setTypeFilter('all')
    if (setActiveCustomFilterId) setActiveCustomFilterId(null)
    if (setStatusFilter) setStatusFilter('all')
    if (onResetPagination) onResetPagination()
  }

  const selectQuickFilter = (filter) => {
    if (setActiveCustomFilterId) setActiveCustomFilterId(null)
    setTypeFilter(typeFilter === filter && filter !== 'all' ? 'all' : filter)
  }

  const selectCustomQuickFilter = (filterId) => {
    setTypeFilter('all')
    if (setActiveCustomFilterId) {
      setActiveCustomFilterId(activeCustomFilterId === filterId ? null : filterId)
    }
  }

  const switchView = (nextView) => {
    dismissViewHint()
    if (view === nextView) return
    if (document.startViewTransition) {
      document.startViewTransition(() => setView(nextView))
    } else {
      setView(nextView)
    }
  }

  const toggleMobileFilter = () => {
    if (window.innerWidth >= 768) return
    setIsMobileOpen((isOpen) => !isOpen)
  }

  return (
    <div className={`${theme === 'memphis' ? 'memphis-filter' : ''} bg-gradient-to-br from-white to-indigo-50/50 rounded-2xl shadow-md border border-indigo-100 p-4 sm:p-6 mb-6 ring-1 ring-white/60`}>
      <div
        className="flex cursor-pointer items-center justify-between"
        onClick={toggleMobileFilter}
      >
        <h2 className="text-lg font-bold text-slate-800 flex items-center gap-2 mb-0 md:mb-4">
          <i className="fa-solid fa-sliders text-indigo-500"></i> 数据检索与智能过滤
        </h2>
        <i
          id="mobileFilterIcon"
          className={`fa-solid fa-chevron-down text-slate-400 md:hidden transition-transform duration-300${isMobileOpen ? ' rotate-180' : ''}`}
        ></i>
      </div>

      <div
        id="filterContent"
        className={`${isMobileOpen ? '' : 'hidden '}md:block mt-4 md:mt-0 transition-all duration-300`}
      >
        <div className={`memphis-filter-grid grid grid-cols-1 ${setStatusFilter ? 'md:grid-cols-5' : 'md:grid-cols-4'} gap-4`}>
          <div className="relative group">
            <label
              htmlFor="searchInput"
              className="block text-xs font-semibold text-slate-500 mb-1 group-focus-within:text-indigo-500 transition-colors"
            >
              搜索番名 / 日文名
            </label>
            <div className="relative">
              <input
                type="text"
                id="searchInput"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="输入番剧中文或日文名..."
                className="w-full pl-10 pr-4 py-2 bg-white border border-slate-200 border-b-[3px] border-b-slate-300 rounded-xl focus:outline-none focus:border-indigo-400 focus:border-b-[1px] focus:translate-y-[2px] focus:shadow-inner focus:ring-2 focus:ring-indigo-100 transition-all text-sm shadow-sm hover:shadow-md hover:-translate-y-0.5"
              />
              <i className="fa-solid fa-magnifying-glass absolute left-3.5 top-3 text-slate-400 group-focus-within:text-indigo-400 group-focus-within:translate-y-[2px] transition-all"></i>
            </div>
          </div>

          <div className="group">
            <label
              htmlFor="tagSelect"
              className="block text-xs font-semibold text-slate-500 mb-1 group-focus-within:text-indigo-500 transition-colors"
            >
              标签过滤
            </label>
            <SearchableSelect
              id="tagSelect"
              value={tagFilter}
              onChange={setTagFilter}
              options={tags.map((tag) => ({ value: tag, label: tag }))}
              allOption={{ value: 'all', label: '全部标签' }}
              searchPlaceholder="搜索标签..."
            />
          </div>

          {setStatusFilter && (
            <div className="group">
              <label
                htmlFor="statusSelect"
                className="block text-xs font-semibold text-slate-500 mb-1 group-focus-within:text-indigo-500 transition-colors"
              >
                观看状态
              </label>
              <select
                id="statusSelect"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
                className="w-full px-3 py-2 bg-white border border-slate-200 border-b-[3px] border-b-slate-300 rounded-xl focus:outline-none focus:border-indigo-400 focus:border-b-[1px] focus:translate-y-[2px] focus:shadow-inner focus:ring-2 focus:ring-indigo-100 transition-all text-sm shadow-sm hover:shadow-md hover:-translate-y-0.5 cursor-pointer"
              >
                <option value="all">全部状态</option>
                <option value="plan">想看</option>
                <option value="watching">在看</option>
                <option value="completed">看过</option>
                <option value="on_hold">搁置</option>
              </select>
            </div>
          )}

          <div className="group">
            <label
              htmlFor="yearSelect"
              className="block text-xs font-semibold text-slate-500 mb-1 group-focus-within:text-indigo-500 transition-colors"
            >
              年份区间
            </label>
            <SearchableSelect
              id="yearSelect"
              value={yearFilter}
              onChange={setYearFilter}
              options={years.map((year) => ({ value: String(year), label: `${year} 年` }))}
              allOption={{ value: 'all', label: '全部年份' }}
              searchPlaceholder="搜索年份..."
            />
          </div>

          <div className="group">
            <div className="flex justify-between items-center mb-1">
              <label
                htmlFor="sortSelect"
                className="block text-xs font-semibold text-slate-500 group-focus-within:text-indigo-500 transition-colors"
              >
                排序规则 (默认)
              </label>
              <button
                type="button"
                onClick={resetFilters}
                className="memphis-reset-btn group/btn flex items-center gap-1 text-sm font-bold text-indigo-500 transition-all hover:text-indigo-700 active:scale-95"
                title="一键恢复所有默认过滤"
              >
                <i className="fa-solid fa-rotate-left transform group-hover/btn:-rotate-180 transition-transform duration-500 ease-out"></i>
                恢复默认
              </button>
            </div>
            <select
              id="sortSelect"
              value={sortOrder}
              onChange={(event) => setSortOrder(event.target.value)}
              className="w-full px-3 py-2 bg-white border border-slate-200 border-b-[3px] border-b-slate-300 rounded-xl focus:outline-none focus:border-indigo-400 focus:border-b-[1px] focus:translate-y-[2px] focus:shadow-inner focus:ring-2 focus:ring-indigo-100 transition-all text-sm shadow-sm hover:shadow-md hover:-translate-y-0.5 cursor-pointer"
            >
              <option value="date-desc">开播时间 (新 → 旧) [推荐]</option>
              <option value="date-asc">开播时间 (旧 → 新)</option>
              <option value="score-desc">评分 (高 → 低)</option>
              <option value="score-asc">评分 (低 → 高)</option>
              {showRecordTimeSort && (
                <>
                  <option value="updated-desc">记录更新 (新 → 旧)</option>
                  <option value="updated-asc">记录更新 (旧 → 新)</option>
                </>
              )}
            </select>
          </div>
        </div>

        <div className="mt-4 grid gap-3 border-t border-slate-100 pt-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
          <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-2" id="quickCategoryFilter">
            <span className="pt-2 text-xs text-slate-500 font-medium whitespace-nowrap">
              <i className="fa-solid fa-tags"></i> 快速筛选:
            </span>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              {quickFilters.map((filter) => (
                <button
                  key={filter.value}
                  type="button"
                  className={typeFilter === filter.value && activeCustomFilterId === null ? activeCategoryClass : inactiveCategoryClass}
                  data-type={filter.value}
                  onClick={() => selectQuickFilter(filter.value)}
                >
                  {filter.label}
                </button>
              ))}
              {customQuickFilters.map((filter) => (
                <button
                  key={`custom-${filter.id}`}
                  type="button"
                  className={activeCustomFilterId === filter.id ? activeCategoryClass : inactiveCategoryClass}
                  onClick={() => selectCustomQuickFilter(filter.id)}
                  title={`${filter.match_mode === 'all' ? '同时包含' : '包含任意'}：${[...filter.tags, ...(filter.title_keywords ?? [])].join('、')}`}
                >
                  <i className="fa-solid fa-filter mr-1.5 text-[10px]"></i>{filter.name}
                </button>
              ))}
              {onOpenQuickFilterEditor && (
                <button
                  type="button"
                  onClick={onOpenQuickFilterEditor}
                  className="flex h-8 w-8 items-center justify-center rounded-full border border-indigo-200 border-b-[3px] border-b-indigo-300 bg-indigo-50 text-indigo-500 shadow-sm transition-all hover:-translate-y-0.5 hover:bg-indigo-100 hover:shadow-md active:translate-y-[2px] active:border-b"
                  title="编辑自定义快速筛选"
                  aria-label="编辑自定义快速筛选"
                >
                  <i className="fa-solid fa-pen-to-square"></i>
                </button>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3 w-full md:w-auto justify-between md:justify-end mt-2 md:mt-0">
            {showViewToggle && (
              <div
                id="viewToggleGroup"
                className={`relative bg-slate-100/80 p-1 rounded-xl flex gap-1 shadow-inner border ${shouldShowViewHint ? 'border-indigo-200/80 ring-2 ring-indigo-500/20' : 'border-slate-200/50'} transition-all duration-500`}
                onClick={dismissViewHint}
              >
                <button
                  id="btnViewList"
                  type="button"
                  onClick={() => switchView('list')}
                  className={view === 'list' ? activeViewClass : inactiveViewClass}
                >
                  <i className="fa-solid fa-list-ul"></i> 列表
                </button>
                <button
                  id="btnViewGrid"
                  type="button"
                  onClick={() => switchView('grid')}
                  className={`relative ${view === 'grid' ? activeViewClass : inactiveViewClass}`}
                >
                  <i className="fa-solid fa-border-all"></i> 海报墙
                  {shouldShowViewHint && (
                    <span className="memphis-view-hint pointer-events-none absolute -right-2 -top-4 border-2 border-black bg-[#ffe66d] px-1.5 py-0.5 text-[8px] font-black leading-none text-black shadow-[2px_2px_0_#111]">
                      试试
                    </span>
                  )}
                </button>
              </div>
            )}

            <div className="memphis-filter-result flex items-center gap-1.5 rounded-xl border-b border-b-white border-t-2 border-t-slate-200/50 bg-slate-100/80 px-3.5 py-1.5 text-xs font-medium text-slate-500 shadow-[inset_0_2px_5px_rgba(0,0,0,0.06)] transition-all">
              <i className="fa-solid fa-chart-pie text-slate-400"></i> 已筛选出
              <span
                id="filteredCount"
                className="font-black text-indigo-600 text-sm px-1.5 py-0.5 bg-white rounded flex items-center justify-center shadow-sm border border-slate-200/60 min-w-[1.75rem] transform -translate-y-px transition-all"
              >
                {filteredCount}
              </span>
              部番剧
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default FilterBar
