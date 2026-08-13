export function mapRecordToAnimeItem(record) {
  const anime = record.anime ?? {}
  const baseTags = Array.isArray(anime.base_tags) ? anime.base_tags : []
  const customTags = Array.isArray(record.custom_tags) ? record.custom_tags : []
  const tagColors = anime.tag_colors && typeof anime.tag_colors === 'object'
    ? anime.tag_colors
    : {}
  const score = record.personal_score === null || record.personal_score === ''
    ? null
    : Number(record.personal_score)

  return {
    id: record.id,
    animeId: anime.id,
    title: anime.chinese_title ?? anime.title ?? '未命名番剧',
    japaneseTitle: anime.japanese_title ?? '',
    period: anime.airing_period ?? '未定档',
    cover: anime.poster_image || anime.poster_url || '',
    posterImage: anime.poster_image || '',
    posterUrl: anime.poster_url || '',
    score: Number.isFinite(score) ? score : null,
    tags: [...new Set([...baseTags, ...customTags])],
    baseTags,
    customTags,
    tagColors,
    status: record.watch_status,
    statusLabel: record.watch_status_display ?? record.watch_status,
    watchedEps: Math.max(0, Number(record.watched_eps) || 0),
    totalEps: Math.max(0, Number(record.total_eps) || 0),
    review: record.review ?? '',
    description: anime.description ?? anime.synopsis ?? '',
    studio: anime.studio ?? '',
    episodes: anime.episodes ?? '',
    baikeUrl: anime.baike_url ?? anime.baikeUrl ?? '',
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    rawRecord: record,
  }
}
