import { useEffect, useState } from 'react'

const PAGE_SIZE_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: '12', label: '12 条/页' },
  { value: '24', label: '24 条/页' },
  { value: '48', label: '48 条/页' },
  { value: '96', label: '96 条/页' },
]

function JournalBatchTools({pageSize,batchAction}) {
  return <div className="journal-batch-tools"><button type="button" disabled={batchAction.disabled||!batchAction.count||batchAction.count>100} onClick={batchAction.onCollect}><i className="fa-solid fa-layer-group" aria-hidden="true"/> {pageSize==='all'?'收录当前结果':'收录本页作品'}到片单</button><small aria-live="polite">{batchAction.disabled?'正在更新当前结果，请稍候。':batchAction.count>100?`当前 ${batchAction.count} 部，请先选择分页数量，单次最多 100 部。`:`${pageSize==='all'?'当前结果':'本页'} ${batchAction.count} 部 · 打开后明确勾选，单次最多 100 部。`}</small></div>
}

export function JournalListTools({batchAction,unratedCount,...props}) {
  return <div className="journal-list-tools"><div className="journal-list-tools-row"><PageSizeSelector {...props}/><PriorityNotice unratedCount={unratedCount} theme={props.theme}/></div>{batchAction&&<JournalBatchTools pageSize={props.pageSize} batchAction={batchAction}/>}</div>
}

export function PageSizeSelector({ pageSize, onChange, totalCount, isLoading, theme = 'default', batchAction }) {
  return (
    <div className={`${theme === 'memphis' ? 'memphis-page-size' : ''} flex h-full flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/80 bg-white/80 px-4 py-3 shadow-[0_8px_24px_rgba(15,23,42,0.05)] backdrop-blur-xl`}>
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-50 text-indigo-500 shadow-inner">
          <i className="fa-solid fa-table-list"></i>
        </span>
        <span>
          切换分页数量
          {Number.isFinite(totalCount) && (
            <span className="ml-2 text-slate-400">共 {totalCount} 条记录</span>
          )}
        </span>
      </div>

      <div className="relative">
        <select
          value={pageSize}
          disabled={isLoading}
          onChange={(event) => onChange(event.target.value)}
          aria-label="选择每页显示数量"
          className="appearance-none rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-white py-2 pl-4 pr-10 text-xs font-black text-indigo-600 shadow-sm outline-none transition focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:ring-2 focus:ring-indigo-100 disabled:cursor-wait disabled:opacity-60"
        >
          {PAGE_SIZE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <i className="fa-solid fa-chevron-down pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-indigo-400"></i>
      </div>
      {batchAction&&<JournalBatchTools pageSize={pageSize} batchAction={batchAction}/>}
    </div>
  )
}

export function PriorityNotice({ unratedCount = 0, theme = 'default' }) {
  return (
    <div className={`${theme === 'memphis' ? 'memphis-priority-notice' : ''} flex h-full flex-wrap items-center justify-between gap-3 rounded-2xl border border-indigo-100 bg-white/80 px-4 py-3 text-xs font-bold text-slate-500 shadow-sm backdrop-blur-xl`}>
      <span className="flex items-center gap-2 text-indigo-600">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-50 shadow-inner">
          <i className="fa-solid fa-arrow-down-wide-short"></i>
        </span>
        已评分与“看过”作品优先展示
      </span>
      <span className="rounded-full bg-slate-100 px-3 py-1.5 text-slate-500 shadow-inner">
        {unratedCount} 部未评分作品置于底部
      </span>
    </div>
  )
}

export function PageNavigation({
  currentPage,
  totalPages,
  hasPrevious,
  hasNext,
  isLoading,
  onPageChange,
}) {
  const [jumpPage, setJumpPage] = useState(String(currentPage))

  useEffect(() => {
    setJumpPage(String(currentPage))
  }, [currentPage])

  const submitJump = (event) => {
    event.preventDefault()
    const requestedPage = Number.parseInt(jumpPage, 10)
    if (!Number.isFinite(requestedPage)) {
      setJumpPage(String(currentPage))
      return
    }
    const safePage = Math.min(totalPages, Math.max(1, requestedPage))
    setJumpPage(String(safePage))
    onPageChange(safePage)
  }

  const buttonClass = 'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-xs font-black text-slate-600 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:text-indigo-600 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0'

  return (
    <nav
      aria-label="追番记录分页"
      className="memphis-record-pagination mt-6 flex flex-wrap items-center justify-center gap-3 rounded-2xl border border-white/80 bg-white/75 px-4 py-4 shadow-[0_8px_24px_rgba(99,102,241,0.08)] backdrop-blur-xl"
    >
      <button
        type="button"
        title="转到第一页"
        aria-label="转到第一页"
        disabled={!hasPrevious || isLoading}
        onClick={() => onPageChange(1)}
        className={buttonClass}
      >
        <i className="fa-solid fa-angles-left"></i>
        <span className="hidden sm:inline">第一页</span>
      </button>

      <button
        type="button"
        disabled={!hasPrevious || isLoading}
        onClick={() => onPageChange(currentPage - 1)}
        className={buttonClass}
      >
        <i className="fa-solid fa-chevron-left"></i>
        上一页
      </button>

      <span className="min-w-28 rounded-xl bg-gradient-to-r from-pink-50 to-indigo-50 px-4 py-2.5 text-center text-xs font-black text-indigo-600 shadow-inner">
        {isLoading ? (
          <i className="fa-solid fa-spinner animate-spin"></i>
        ) : (
          <>第 {currentPage} / {totalPages} 页</>
        )}
      </span>

      <form onSubmit={submitJump} className="flex items-center gap-2">
        <label htmlFor="paginationJumpInput" className="text-xs font-bold text-slate-500">
          跳至
        </label>
        <input
          id="paginationJumpInput"
          type="number"
          min="1"
          max={totalPages}
          value={jumpPage}
          disabled={isLoading}
          onChange={(event) => setJumpPage(event.target.value)}
          className="w-16 rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-white px-2 py-2 text-center text-xs font-black text-indigo-600 shadow-sm outline-none transition focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:ring-2 focus:ring-indigo-100"
        />
        <button type="submit" disabled={isLoading} className={buttonClass}>
          跳转
        </button>
      </form>

      <button
        type="button"
        disabled={!hasNext || isLoading}
        onClick={() => onPageChange(currentPage + 1)}
        className={buttonClass}
      >
        下一页
        <i className="fa-solid fa-chevron-right"></i>
      </button>

      <button
        type="button"
        title="转到最后一页"
        aria-label="转到最后一页"
        disabled={!hasNext || isLoading}
        onClick={() => onPageChange(totalPages)}
        className={buttonClass}
      >
        <span className="hidden sm:inline">最后一页</span>
        <i className="fa-solid fa-angles-right"></i>
      </button>
    </nav>
  )
}
