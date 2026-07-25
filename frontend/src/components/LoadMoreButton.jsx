function LoadMoreButton({
  hasMore,
  isLoading,
  onLoadMore,
  loadedCount,
  totalCount,
  error = '',
}) {
  if (!hasMore && !error) return null

  return (
    <div className="flex flex-col items-center gap-3 py-7">
      {error && (
        <p role="alert" className="text-sm font-bold text-rose-500">
          <i className="fa-solid fa-circle-exclamation mr-2"></i>
          {error}
        </p>
      )}

      {hasMore && (
        <button
          type="button"
          onClick={onLoadMore}
          disabled={isLoading}
          className="group inline-flex min-w-44 items-center justify-center gap-2 rounded-2xl border border-white/80 bg-white/85 px-6 py-3 text-sm font-black text-indigo-600 shadow-[0_8px_24px_rgba(99,102,241,0.14)] backdrop-blur-xl transition-all duration-300 hover:-translate-y-0.5 hover:bg-white hover:shadow-[0_12px_30px_rgba(99,102,241,0.22)] focus:outline-none focus:ring-4 focus:ring-indigo-100 disabled:cursor-wait disabled:opacity-70 disabled:hover:translate-y-0"
        >
          <i className={`fa-solid ${isLoading ? 'fa-spinner animate-spin' : 'fa-angles-down'} text-pink-500`}></i>
          {isLoading ? '正在加载…' : '加载更多'}
        </button>
      )}

      {Number.isFinite(totalCount) && (
        <span className="text-xs font-bold text-slate-400">
          已加载 {loadedCount} / {totalCount} 部
        </span>
      )}
    </div>
  )
}

export default LoadMoreButton
