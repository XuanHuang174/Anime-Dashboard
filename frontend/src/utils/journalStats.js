// Keep the offline/local fallback aligned with the full-queryset stats API.
export function countJournalStatuses(records) {
  return records.reduce((counts, item) => {
    if (item.status === 'watching') counts.watchingCount += 1
    const period = String(item.period ?? '')
    if (item.status === 'plan' || item.status === 'on_hold' || period.includes('待定') || period.includes('未定档')) {
      counts.pendingCount += 1
    }
    return counts
  }, { watchingCount: 0, pendingCount: 0 })
}
