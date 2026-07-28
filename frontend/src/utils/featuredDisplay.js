const statusLabels = {
  plan: '想看',
  watching: '在看',
  completed: '看过',
  on_hold: '搁置',
}

export function formatAiringPeriod(period) {
  if (!period) return '未定档'
  const match = String(period).match(/^(\d{4})-(\d{1,2})$/)
  if (!match) return String(period)
  return `${match[1]}年${Number(match[2])}月`
}

export function mapFeaturedSubmission(submission) {
  const numericScore = submission.score === null || submission.score === ''
    ? null
    : Number(submission.score)
  const year = submission.year ? String(submission.year) : '未定档'
  const authorName = submission.author_name
    || submission.submitter?.nickname
    || submission.submitter?.username
    || submission.user
    || '手账主理人'

  return {
    id: submission.id,
    title: submission.chinese_title || '未命名番剧',
    japaneseTitle: submission.japanese_title || '',
    period: submission.airing_period || (submission.year ? `${year}-全年` : year),
    cover: submission.poster || submission.poster_url || '',
    posterImage: submission.poster || '',
    posterUrl: submission.poster_url || '',
    score: Number.isFinite(numericScore) ? numericScore : null,
    tags: Array.isArray(submission.tags) ? submission.tags : [],
    tagColors: submission.tag_colors || {},
    status: submission.watch_status,
    statusLabel: submission.watch_status_display
      || statusLabels[submission.watch_status]
      || submission.watch_status,
    review: submission.review || '',
    description: submission.description || '',
    studio: submission.studio || '',
    episodes: submission.episodes || '',
    baikeUrl: submission.baike_url || '',
    authorName,
    authorAvatar: submission.author_avatar || submission.submitter?.avatar || '',
    createdAt: submission.created_at,
    updatedAt: submission.updated_at,
    rawSubmission: submission,
  }
}
