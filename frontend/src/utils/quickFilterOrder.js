export function sortedQuickFilters(filters) {
  if (!filters.every(filter => Number.isInteger(filter.sort_order))) return [...filters]
  return [...filters].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
}

// All existing list endpoints retain their pagination contract. Consumers load every
// page, including editors and public featured bars, rather than silently truncating.
export async function loadCompleteQuickFilters(client, endpoint, signal) {
  const rows = [], seen = new Set()
  let expectedCount = null
  for (let page = 1; page <= 1000; page++) {
    const { data } = await client.get(endpoint, { params: { page_size: 100, page }, signal })
    const items = Array.isArray(data) ? data : data?.results
    if (!Array.isArray(items)) throw new Error('组合列表读取不完整，请重新读取。')
    if (!Array.isArray(data)) {
      if (!Number.isSafeInteger(data.count) || data.count < 0 || expectedCount !== null && data.count !== expectedCount) throw new Error('组合列表数量已变化，请重新读取。')
      expectedCount = data.count
    }
    for (const row of items) {
      if (!Number.isSafeInteger(row.id) || seen.has(row.id)) throw new Error('组合列表已变化，请重新读取。')
      seen.add(row.id); rows.push(row)
    }
    if (Array.isArray(data) || !data.next) {
      if (expectedCount !== null && rows.length !== expectedCount) throw new Error('组合列表读取不完整，请重新读取。')
      return { data: sortedQuickFilters(rows) }
    }
    if (!items.length) break
  }
  throw new Error('组合列表读取不完整，请重新读取。')
}

export function moveQuickFilter(ids, id, targetId) {
  if (id === targetId || !ids.includes(id) || !ids.includes(targetId)) return ids
  const target = ids.indexOf(targetId), result = ids.filter(value => value !== id)
  result.splice(target, 0, id)
  return result
}
