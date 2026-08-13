function getProgressPercent(watchedEps, totalEps) {
  if (!totalEps) return 0
  return Math.min(100, Math.max(0, (watchedEps / totalEps) * 100))
}

function WatchingProgress({ item, onIncrement, disabled = false, compact = false }) {
  if (item.status !== 'watching') return null

  const watchedEps = Math.max(0, Number(item.watchedEps) || 0)
  const totalEps = Math.max(0, Number(item.totalEps) || 0)
  const percent = getProgressPercent(watchedEps, totalEps)
  const isComplete = totalEps > 0 && watchedEps >= totalEps

  return (
    <span className="watching-progress-control inline-flex items-center gap-1">
      <span
        className={`watching-progress-pill${compact ? ' is-compact' : ''}`}
        style={{ '--watching-progress': `${percent}%` }}
        title={totalEps > 0 ? `观看进度 ${watchedEps}/${totalEps}` : `观看进度 ${watchedEps}/未知`}
      >
        在看 {watchedEps}/{totalEps || '?'}
      </span>
      {onIncrement && (
        <button
          type="button"
          className={`watching-progress-plus${compact ? ' is-compact' : ''}`}
          disabled={disabled || isComplete}
          aria-label={`《${item.title}》观看进度加一集`}
          title={isComplete ? '已达到总集数' : '观看进度 +1'}
          onClick={(event) => {
            event.stopPropagation()
            onIncrement(item)
          }}
        >
          +1
        </button>
      )}
    </span>
  )
}

export default WatchingProgress
