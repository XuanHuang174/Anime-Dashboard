export const RATING_COLORS = {
  slate: { label: '墨灰', color: '#334155', light: '#64748b' }, violet: { label: '紫色', color: '#7c3aed', light: '#c026d3' },
  red: { label: '红色', color: '#dc2626', light: '#e11d48' }, amber: { label: '金橙', color: '#b45309', light: '#ea580c' },
  teal: { label: '青绿', color: '#0f766e', light: '#059669' }, blue: { label: '蓝色', color: '#2563eb', light: '#4f46e5' },
  pink: { label: '粉色', color: '#be185d', light: '#db2777' }, green: { label: '绿色', color: '#15803d', light: '#0d9488' },
  ink: { label: '经典墨色', color: '#1e293b', light: '#475569' },
  orange: { label: '明橙', color: '#f97316', light: '#fb923c' },
  gold: { label: '经典金星', color: '#fbbf24', light: '#f59e0b' },
  deeporange: { label: '经典橙星', color: '#ea580c', light: '#f97316' },
  classic: { label: '紫蓝渐变', color: '#9333ea', light: '#d946ef', accent: '#a78bfa',
    gradient: ['#4f46e5', '#9333ea', '#d946ef'], starGradient: ['#ec4899', '#a855f7', '#6366f1'] },
  rainbow: { label: '霓虹虹彩', color: '#ff2a85', light: '#b537f2', accent: '#a78bfa',
    gradient: ['#ff2a85', '#ff7b00', '#ffd000', '#00e5ff', '#4d7cff', '#b537f2', '#ff2a85'],
    starGradient: ['#ff2a85', '#ff7b00', '#ffd000', '#00e5ff', '#4d7cff', '#b537f2', '#ff2a85'] },
}
export const defaultRatingDisplay = () => ({ mobile_stars: false, detail_stars: false, poster_style: 'badge' })
export const MAX_RATING_TIERS = 11
export const STAT_CARDS = { completed: '追番数量', average: '平均评分', movie: '剧场版', ova: 'OVA', short: '泡面番', highlight: '自定义推荐统计' }
export function legacyRatingPreferences() {
  return { version: 1, star_max: 5, show_stars: true, tiers: [
    [9.9, '殿堂', 5, 'violet', 'violet', 'gradient'], [9.5, '极高推荐', 5, 'red', 'amber', 'plain'],
    [9, '强烈推荐', 5, 'amber', 'amber', 'plain'], [8, '推荐', 4, 'slate', 'amber', 'plain'],
    [7, '值得一看', 3, 'slate', 'amber', 'plain'], [6, '一般', 2, 'slate', 'amber', 'plain'],
    [0.1, '其他评分', 1, 'slate', 'amber', 'plain'], [0, '零分', 0, 'slate', 'amber', 'plain'],
  ].map(([min, label, stars, color, star_color, effect]) => ({ min, label, stars, color, star_color, effect })),
  statistics: { label: '极高推荐', min: 9.5, max: 10, cards: Object.keys(STAT_CARDS) } }
}
// Keep the old palette tokens intact for people who picked those colors themselves.
// Only the complete former default appearance is upgraded, without touching thresholds or statistics.
export function restoreClassicAppearance(value) {
  return { ...value, tiers: value.tiers.map(tier => ({ ...tier,
    color: tier.min >= 9.9 ? 'rainbow' : tier.min >= 9.5 ? 'red' : tier.min >= 9 ? 'orange' : 'ink',
    star_color: tier.min >= 9.9 ? 'rainbow' : tier.min >= 9.5 ? 'deeporange' : 'gold',
    effect: tier.min >= 9.9 ? 'glow' : 'plain',
  })) }
}
export function defaultRatingPreferences() { return { ...restoreClassicAppearance(legacyRatingPreferences()), display: defaultRatingDisplay() } }
export function hasLegacyDefaultAppearance(value) {
  const old = legacyRatingPreferences().tiers
  return value?.tiers?.length === old.length && value.tiers.every((tier, i) => ['min', 'color', 'star_color', 'effect'].every(key => tier[key] === old[i][key]))
}
function hasPreviousDefaultAppearance(value) {
  if (Object.hasOwn(value, 'display') || value.tiers.length !== 8) return false
  const previous = restoreClassicAppearance(legacyRatingPreferences()).tiers.map(tier => tier.color === 'rainbow' ? { ...tier, color: 'classic', star_color: 'classic', effect: 'gradient' } : tier)
  return value.tiers.every((tier, index) => ['min', 'color', 'star_color', 'effect'].every(key => tier[key] === previous[index][key]))
}
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join('|') === [...expected].sort().join('|')
const boundary = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10 && Math.abs(value * 10 - Math.round(value * 10)) < 1e-8
const label = value => typeof value === 'string' && value.trim() && [...value].length <= 16 && !/[\u0000-\u001f]/.test(value)
export function ratingPreferencesError(value) {
  if (!keys(value, ['version', 'star_max', 'show_stars', 'tiers', 'statistics', ...(Object.hasOwn(value ?? {}, 'display') ? ['display'] : [])]) || value.version !== 1 || !Number.isInteger(value.star_max) || value.star_max < 1 || value.star_max > 10 || typeof value.show_stars !== 'boolean') return '请选择 1–10 的星级上限。'
  if (Object.hasOwn(value, 'display') && (!keys(value.display, ['mobile_stars', 'detail_stars', 'poster_style']) || typeof value.display.mobile_stars !== 'boolean' || typeof value.display.detail_stars !== 'boolean' || !['badge', 'stars', 'number'].includes(value.display.poster_style))) return '请选择合法的手机列表、作品详情及海报墙显示方式。'
  if (!Array.isArray(value.tiers) || !value.tiers.length || value.tiers.length > MAX_RATING_TIERS) return '评分档位需为 1–11 档。'
  let previous = 11
  for (const tier of value.tiers) {
    if (!keys(tier, ['min', 'label', 'stars', 'color', 'star_color', 'effect']) || !boundary(tier.min) || tier.min >= previous) return '起点需在 0–10 内，最多一位小数；档位需从高到低且不重复。'
    if (!label(tier.label) || !Number.isInteger(tier.stars) || tier.stars < 0 || tier.stars > value.star_max || !Object.hasOwn(RATING_COLORS, tier.color) || !Object.hasOwn(RATING_COLORS, tier.star_color) || !['plain', 'gradient', 'glow'].includes(tier.effect)) return '请填写档位名称、合法星数和配色。'
    previous = tier.min
  }
  if (previous !== 0) return '最低档从 0 分开始，上限随相邻档位自动联动。'
  const stats = value.statistics
  if (!keys(stats, ['label', 'min', 'max', 'cards']) || !label(stats.label) || !boundary(stats.min) || !boundary(stats.max) || stats.min > stats.max) return '统计名称需为 1–16 字，范围需在 0–10 内且下限不高于上限。'
  if (!Array.isArray(stats.cards) || !stats.cards.length || stats.cards.some(key => !Object.hasOwn(STAT_CARDS, key)) || new Set(stats.cards).size !== stats.cards.length) return '请至少选择一张统计卡片。'
  return ''
}
export function normalizeRatingPreferences(value) {
  if (ratingPreferencesError(value)) return defaultRatingPreferences()
  const usesDefaultAppearance = hasLegacyDefaultAppearance(value) || hasPreviousDefaultAppearance(value)
  const config = usesDefaultAppearance ? restoreClassicAppearance(value) : value
  return config.display ? config : { ...config, display: usesDefaultAppearance ? defaultRatingDisplay() : { mobile_stars: true, detail_stars: true, poster_style: 'stars' } }
}
export function ratingForScore(score, preferences) {
  const config = normalizeRatingPreferences(preferences)
  if (score === null || score === undefined || score === '' || typeof score === 'boolean') return null
  const value = Number(score)
  if (!Number.isFinite(value) || value < 0 || value > 10) return null
  const tier = config.tiers.find(row => value >= row.min)
  return { ...tier, score: value, max: config.star_max, show_stars: config.show_stars, display: config.display }
}
export function ratingStyle(tier, stars = false) {
  if (!tier) return { color: '#64748b' }
  const palette = RATING_COLORS[stars ? tier.star_color : tier.color]
  if (tier.effect === 'gradient') return { color: 'transparent', WebkitTextFillColor: 'transparent', backgroundImage: `linear-gradient(to right, ${(stars ? palette.starGradient : palette.gradient)?.join(', ') || `${palette.color}, ${palette.light}`})`, backgroundClip: 'text', WebkitBackgroundClip: 'text' }
  return { color: palette.color, ...(tier.effect === 'glow' ? { textShadow: `0 1px 7px ${palette.light}55` } : {}) }
}
export function highlightRange(preferences) {
  const { min, max } = normalizeRatingPreferences(preferences).statistics
  return max === 10 ? `${min}+` : `${min}–${max}`
}
export function histogramHighlight(histogram, preferences) {
  const { min, max } = normalizeRatingPreferences(preferences).statistics
  return histogram.reduce((sum, row) => sum + (row.score !== null && Number(row.score) >= min && Number(row.score) <= max ? row.count : 0), 0)
}
export function ratingPreset(name) {
  const value = defaultRatingPreferences()
  if (name === 'bangumi') {
    value.star_max = 10
    value.tiers = [[10, '满分', 'rainbow'], [9, '神作', 'classic'], [8, '力荐', 'red'], [7, '推荐', 'orange'], [6, '还行', 'teal'], [5, '一般', 'ink'], [4, '较差', 'ink'], [3, '差', 'ink'], [2, '很差', 'ink'], [1, '不推荐', 'ink'], [0, '零分档', 'ink']].map(([min, label, color]) => ({ min, label, stars: min, color, star_color: ['rainbow', 'classic'].includes(color) ? color : color === 'red' ? 'deeporange' : 'gold', effect: color === 'rainbow' ? 'glow' : color === 'classic' ? 'gradient' : 'plain' }))
    value.statistics = { ...value.statistics, min: 8, label: '强烈推荐' }
  }
  if (name === 'numbers') value.show_stars = false
  return value
}

export function ratingTierRange(tiers, index) {
  if (index === 0 && tiers[0].min === 10) return '分数 = 10'
  return `${tiers[index].min === '' ? '…' : tiers[index].min} ≤ 分数 ${index ? `< ${tiers[index - 1].min === '' ? '…' : tiers[index - 1].min}` : '≤ 10'}`
}

// Splitting the endpoint preserves every existing interval below 10 and the user's scale.
export function isolateFullScoreTier(config) {
  if (ratingPreferencesError(config) || config.tiers[0].min === 10 || config.tiers.length >= MAX_RATING_TIERS) return config
  return { ...config, tiers: [{ ...config.tiers[0], min: 10, label: '满分', stars: config.star_max }, ...config.tiers] }
}
