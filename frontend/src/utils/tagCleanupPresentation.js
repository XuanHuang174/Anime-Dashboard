// Presentation only. Server-held plans remain the sole source of writes.
export const cleanupPhases = { scanning: 'scan', applying: 'apply', undoing: 'undo' }
export const cleanupLabels = { scanning: '扫描已暂停', ready: '预览待确认', applying: '应用已暂停', applied: '应用已完成', undoing: '撤销已暂停', undone: '撤销已完成', expired: '已过期', cancelled: '已取消' }
export const cleanupItemLabels = { proposed: '拟整理', unchanged: '无需修改', missing: '已跳过', no_source: '无标准来源', applied: '已应用', conflict: '应用冲突', undone: '已撤销', undo_conflict: '撤销冲突' }

export function cleanupCounts(job) {
  const counts = job?.counts || {}
  return {
    changes: job?.change_records ?? ['proposed', 'applied', 'undone', 'conflict', 'undo_conflict'].reduce((sum, status) => sum + (counts[status] || 0), 0),
    conflicts: (counts.conflict || 0) + (counts.undo_conflict || 0),
    protected: job?.protected_records || 0, review: job?.needs_review || 0,
  }
}

export function defaultCleanupTab(job) {
  const counts = cleanupCounts(job)
  if (['applied', 'undone'].includes(job?.status) && counts.conflicts) return 'conflicts'
  return !cleanupPhases[job?.status] && counts.changes ? 'changes' : 'all'
}

export function cleanupProgress(job) {
  const phase = cleanupPhases[job?.status] || (job?.status === 'applied' ? 'apply' : job?.status === 'undone' ? 'undo' : 'scan')
  const total = phase === 'scan' ? (job?.total || 0) : (job?.selected_count || 0)
  const done = Math.min(total, Math.max(0, job?.[`${phase}_cursor`] || 0))
  return { phase, name: { scan: '扫描', apply: '应用', undo: '撤销' }[phase], done, total, percent: total ? Math.round(done / total * 100) : 0 }
}

export function cleanupResult(job) {
  const counts = job?.counts || {}, conflicts = cleanupCounts(job).conflicts
  if (job?.status === 'applied') return `已整理 ${counts.applied || 0} 部，跳过冲突 ${counts.conflict || 0} 部，其余 ${Math.max(0, job.total - (counts.applied || 0) - (counts.conflict || 0))} 部未修改。`
  if (job?.status === 'undone') return `已撤销 ${counts.undone || 0} 部，${counts.undo_conflict || 0} 部因后续变化而保留。`
  if (job?.status === 'ready') return `扫描完成，${counts.proposed || 0} 部可整理。核对勾选后再应用。`
  return conflicts ? '部分作品有冲突，已保留新修改。' : '进度会逐批保存，中断后可继续。'
}

export function cleanupScope(job) {
  if (!job) return ''
  const filters = job.filters || {}, hints = []
  if (filters.search) hints.push(`搜索「${filters.search}」`)
  if (filters.tag && filters.tag !== 'all') hints.push(`标签「${filters.tag}」`)
  if (filters.year && filters.year !== 'all') hints.push(`${filters.year} 年`)
  if (filters.status && filters.status !== 'all') hints.push(({ plan: '想看', watching: '在看', completed: '看过', on_hold: '搁置' })[filters.status] || '观看状态筛选')
  if (filters.type_filter && filters.type_filter !== 'all') hints.push(({ yuri: '百合', moe: '萌系', comedy: '搞笑', og: '原创', special: '特殊类型' })[filters.type_filter] || '分类筛选')
  if (filters.quick_filter) hints.push('已保存的快捷筛选')
  return `${job.scope === 'all' ? '全部私人手账' : '扫描时的筛选作品'} · ${job.total} 部${job.scope !== 'all' && hints.length ? ` · ${hints.join(' / ')}` : ''}`
}

export function cleanupTagTone(item, tag, side) {
  if (item.protected.includes(tag)) return 'protected'
  if (item.changes.some(change => change.kind === 'normalize' && change[side === 'before' ? 'from' : 'to'] === tag)) return 'normalize'
  if (side === 'before' && !item.after.includes(tag)) return 'remove'
  if (side === 'after' && !item.before.includes(tag)) return 'add'
  return 'keep'
}
