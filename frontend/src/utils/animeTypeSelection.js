export const ANIME_TYPES = Object.freeze(['TV', '剧场版', 'OVA', 'WEB', '短片', '动态漫画'])
export function normalizeTypeSelection(value) {
  if (value === 'all') return value
  if (typeof value !== 'string') throw new Error('作品类型组合无效。')
  const parts = value.split(',')
  if (!parts.length || parts.length > ANIME_TYPES.length || new Set(parts).size !== parts.length || parts.some(type => !ANIME_TYPES.includes(type))) throw new Error('作品类型组合无效。')
  return parts.sort().join(',')
}
export function isTypeSelection(value) {
  try { return normalizeTypeSelection(value) === value } catch { return false }
}
export const matchesTypeSelection = (kind, value) => value === 'all' || value.split(',').includes(kind)
export const typeSelectionLabel = value => value === 'all' ? '全部类型' : value.split(',').join(' / ')
