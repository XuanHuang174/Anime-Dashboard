export function bangumiSubjectUrl(id) {
  return Number.isInteger(id) && id > 0 && id <= 2147483647 ? `https://bgm.tv/subject/${id}` : ''
}
export function safeExternalUrl(value) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''
  } catch { return '' }
}
export function normalizeBangumiLink(value) {
  try {
    const url = new URL(value), match = /^\/subject\/([1-9]\d*)\/?$/.exec(url.pathname)
    return ['http:', 'https:'].includes(url.protocol) && ['bgm.tv', 'bangumi.tv'].includes(url.host)
      && !url.username && !url.password && match ? bangumiSubjectUrl(Number(match[1])) : ''
  } catch { return '' }
}
export function moegirlTitleUrl(title) {
  return title?.trim() ? `https://zh.moegirl.org.cn/${encodeURIComponent(title.trim().replace(/^《|》$/g, ''))}` : ''
}
export function journalIdentityMessage(state) {
  return ({
    unlinked: '尚未关联标准作品，核对后可读取系列与制作资料。',
    stale_link: '已保存的身份关联需要重新核对，不必修改评分或观看状态。',
    identity_conflict: '作品身份存在冲突，请核对资料；已有权威来源冲突需管理员处理。',
    not_in_catalog: '已有作品来源，但当前标准库暂未收录该条目，可先查看原站资料。',
    unsafe_or_unknown: '当前内容范围暂不能读取这部作品的身份与关联资料。',
  })[state] || '尚未关联标准作品，可先核对资料。'
}
export const canReviewJournalIdentity = state => ['unlinked', 'stale_link', 'identity_conflict'].includes(state)
