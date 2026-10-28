import { CREDIT_DEFAULTS } from '../../src/utils/recommendationCredits.js'
import { scope, adultScope } from './explorationContract.js'
export const creditsCapability = { contract_version: 'recommendations.credits.capabilities.v1', voice_actor: true, studio: true }
export const creditsCoverage = { status: 'scoped_current_sources', complete_filmography: false, detail: '仅展示当前范围及有效来源确认的关联，不代表完整作品年表。' }
export const creditsCommon = (version, contentScope = 'safe') => ({ contract_version: `recommendations.credits.${version}.v1`,
  credits_revision: 1, coverage: { ...creditsCoverage }, content_scope_context: contentScope === 'safe' ? scope : adultScope })
export const creditsEntity = (kind = 'voice_actor', person = kind === 'studio' ? 201 : 101, count = 25) => ({ kind, person_id: person,
  name: kind === 'studio' ? '星河动画制作' : '合成声优 · 白羽', work_count: count, alias_status: 'observed' })
export const creditsEvidence = (subject = 1, kind = 'voice_actor', person = kind === 'studio' ? 201 : 101) => ({ subject_id: subject,
  person_id: person, kind, position_or_cast_type: kind === 'studio' ? 67 : 0,
  characters: kind === 'studio' ? [] : [301, 302].map((id, i) => ({ character_id: id, name: i ? '旅人' : '清风',
    role_type: i ? 999 : 1, source_order: i, annotation_warnings: i ? ['unmapped_character_role'] : [], source_episode_note: i ? '第 2 集' : '' })),
  source_episode_note: '', snapshot_version: 'dump-2026-10-20.000000Z', source_at: '2026-10-20T00:00:00+00:00' })
export function creditsSearch(kind = 'voice_actor', contentScope = 'safe') {
  return { ...creditsCommon('search', contentScope), kind, limit: 15, has_more: false, results: [creditsEntity(kind)] }
}
export function creditsSummary(subject = 1, expanded = false, contentScope = 'safe') {
  return { ...creditsCommon('summary', contentScope), bangumi_id: subject, sections: {
    studio: { status: 'observed', last_attempt_status: 'observed', count: 1, limit: expanded ? 5000 : 50, has_more: false,
      results: [{ person_id: 201, name: '星河动画制作', kind: 'studio', evidence: creditsEvidence(subject, 'studio', 201) }] },
    voice_actor: { status: 'observed', last_attempt_status: 'partial', count: 61, limit: expanded ? 5000 : 50, has_more: !expanded,
      results: Array.from({ length: expanded ? 61 : 50 }, (_, i) => ({ person_id: i ? 1000 + i : 101,
        name: i ? `合成声优 ${i + 1}` : '合成声优 · 白羽', kind: 'voice_actor', evidence: creditsEvidence(subject, 'voice_actor', i ? 1000 + i : 101) })) } } }
}
export function creditsPage({ kind = 'voice_actor', person = kind === 'studio' ? 201 : 101, offset = 0, count = 25,
  options = CREDIT_DEFAULTS, contentScope = 'safe', expires = Math.floor(Date.now() / 1000) + 900 } = {}) {
  return { ...creditsCommon('works', contentScope), entity: creditsEntity(kind, person, 26), conditions: { ...options }, count,
    ordering: 'credits.catalog-period.v1', context: `fixture:${offset}`, expires_at: expires,
    next_cursor: offset + 12 < count ? `fixture:${offset + 12}` : null,
    results: Array.from({ length: Math.min(12, count - offset) }, (_, i) => {
      const identity = offset + i + 100, adult = contentScope !== 'safe' && identity === 125
      return { bangumi_id: identity, title_zh: `风与星的旅程 ${offset + i + 1}`, title_jp: 'Synthetic Story', score: 8,
        season: '2026-01', status_type: 'TV', tags: ['日常', '音乐'], cover_url: '', content_rating: adult ? 'adult' : 'non_adult',
        watch_status: null, action_context: `fixture-action:${kind}:${person}:${identity}`, release_period: '2026-01', release_precision: 'catalog_period', evidence: creditsEvidence(identity, kind, person) }
    }) }
}
export function creditsDetail(row, kind = 'voice_actor', person = kind === 'studio' ? 201 : 101, contentScope = 'safe') {
  return { ...creditsCommon('detail', contentScope), kind, person_id: person, item: { ...row, synopsis: '循着熟悉的声音，踏上新的旅程。合成剧情简介。' } }
}
