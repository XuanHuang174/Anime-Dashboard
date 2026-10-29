export const SCORE_PRESETS = Object.freeze([
  ['0', '不限评分'], ['lt6', '6.0 以下（不含未知评分）'],
  ...['6', '7', '8', '9'].map(value => [value, `${value}.0 及以上`]), ['custom', '自定义区间'],
])

export function scoreError(filters) {
  if (!SCORE_PRESETS.some(([value]) => value === filters.minScore)) return '评分条件无效。'
  if (filters.minScore !== 'custom') return ''
  const values = [filters.scoreFrom, filters.scoreTo]
  if (values.some(value => !/^(?:[0-9](?:\.\d)?|10(?:\.0)?)$/.test(String(value ?? '')) || Number(value) < 0.1 || Number(value) > 10)) return '请填写 0.1–10.0 的评分上下限，最多一位小数。'
  return Number(values[0]) > Number(values[1]) ? '评分下限不能大于上限。' : ''
}

export function scoreConditions(filters) {
  const error = scoreError(filters)
  if (error) throw new Error(error)
  return { min_score: Number(filters.minScore === 'custom' ? filters.scoreFrom : filters.minScore === 'lt6' ? 0 : filters.minScore).toFixed(1),
    max_score: Number(filters.minScore === 'custom' ? filters.scoreTo : filters.minScore === 'lt6' ? 6 : 10).toFixed(1),
    max_score_exclusive: filters.minScore === 'lt6' }
}

// Keep the existing personal/legacy request shape; canonical response scores
// are still one-decimal strings, and an unrestricted ceiling is omitted.
export function scoreRequestConditions(filters) {
  const score = scoreConditions(filters)
  return { min_score: String(Number(score.min_score)),
    ...(Number(score.max_score) < 10 ? { max_score: score.max_score } : {}),
    ...(score.max_score_exclusive ? { max_score_exclusive: true } : {}) }
}

export function scoreDraft({ min_score = 0, max_score = 10, max_score_exclusive = false }) {
  if (max_score_exclusive && Number(min_score) === 0 && Number(max_score) === 6) return { minScore: 'lt6' }
  if (max_score_exclusive) throw new Error('不支持此排他评分区间，请重新选择。')
  if (!max_score_exclusive && Number(max_score) === 10 && ['0', '6', '7', '8', '9'].includes(String(Number(min_score)))) return { minScore: String(Number(min_score)) }
  return { minScore: 'custom', scoreFrom: Number(min_score).toFixed(1), scoreTo: Number(max_score).toFixed(1) }
}

export function scoreLabel(filters) {
  if (filters.minScore === 'custom') return `Bangumi 评分 ${Number(filters.scoreFrom).toFixed(1)}–${Number(filters.scoreTo).toFixed(1)}`
  if (filters.minScore === 'lt6') return 'Bangumi 评分 < 6.0（不含未知评分）'
  return Number(filters.minScore) > 0 ? `Bangumi 评分 ≥ ${Number(filters.minScore).toFixed(1)}` : ''
}

export function clearScore(filters) {
  const { scoreFrom, scoreTo, ...rest } = filters
  return { ...rest, minScore: '0' }
}
