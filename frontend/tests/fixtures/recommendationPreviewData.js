// Public catalog snapshots for isolated UI tests only; never imported by production.
// Fixture identities cannot be mistaken for backend Anime/UserAnimeRecord IDs.
const snapshot = [
  [51, 'CLANNAD', 'CLANNAD -クラナド-', 2007, 'TV', 8.4, 'familiar', ['校园', '治愈'], '角色之间细腻的情感变化', '在樱花盛开的坡道上，一段关于陪伴与成长的故事展开。'],
  [238, '攻壳机动队2 无罪', 'イノセンス', 2004, '剧场版', 8.7, 'explore', ['科幻', '悬疑'], '更富思辨色彩的科幻叙事', '在人与机器的边界，寻找关于记忆与自我的答案。'],
  [12, '人形电脑天使心', 'ちょびっツ', 2002, 'TV', 7.5, 'familiar', ['日常', '恋爱'], '日常相处中的温柔与心动', '一场偶然的相遇，让平凡的日常变得不一样。'],
  [233, '剧场版 空之境界 第一章 俯瞰风景', '劇場版 空の境界 第一章 俯瞰風景', 2007, '剧场版', 7.9, 'explore', ['奇幻', '悬疑'], '带有独特氛围的奇幻世界', '都市夜色中，一段关于异常事件与人物命运的故事。'],
  [234, 'AIR', 'AIR', 2005, 'TV', 7.9, 'familiar', ['治愈', '奇幻'], '夏日氛围与情感表达', '夏日小镇中的相遇，连接着过去与现在。'],
  [50, '机动战士高达', '機動戦士ガンダム', 1979, 'TV', 7.8, 'explore', ['科幻', '机战'], '经典作品中的人物抉择', '以宇宙战争为背景，描绘少年与时代的交汇。'],
  [8, 'Code Geass 反叛的鲁路修R2', 'コードギアス 反逆のルルーシュR2', 2008, 'TV', 8.3, 'explore', ['战斗', '原创'], '紧凑的剧情与鲜明的人物关系', '在冲突与抉择之间，故事走向新的篇章。'],
  [236, '超时空要塞Frontier', 'マクロスFrontier', 2008, 'TV', 7.4, 'familiar', ['音乐', '科幻'], '音乐与角色关系的交织', '星际旅途中，歌声与梦想成为联系彼此的纽带。'],
]

export const RECOMMENDATION_PREVIEW_ITEMS = snapshot.map(([id, title, originalTitle, year, type, score, lane, tags, evidence, synopsis]) => ({
  id: `demo-${id}`, bangumiId: id, title, originalTitle, year, type, score, lane, tags,
  nsfw: false, coverUrl: `https://media.xh-anime.com/covers/${id}.jpg`, synopsis,
  reason: `演示理由：从喜爱的作品出发，探索${evidence}。`,
})).concat([
  { id: 'demo-missing', title: '那些还未被找到的故事：封面缺失演示', originalTitle: 'MISSING COVER', year: 2026,
    type: 'TV', score: 8, lane: 'familiar', nsfw: false, coverUrl: '', tags: ['日常'],
    reason: '演示理由：留一个位置，给下一次心动。', synopsis: '这是缺图占位演示，不是真实推荐作品。' },
  { id: 'demo-external', title: '不加载外部原图的安全占位演示', originalTitle: 'SAFE PLACEHOLDER', year: 2026,
    type: 'OVA', score: 7.8, lane: 'explore', nsfw: false, coverUrl: 'https://external.invalid/cover.jpg', tags: ['奇幻'],
    reason: '演示理由：没有托管封面，也可以安心浏览。', synopsis: '外部原图被拦截，统一使用本地 CSS 占位。' },
  { id: 'demo-adult', title: '成人隔离测试条目', nsfw: true, score: 9, type: 'TV', lane: 'familiar' },
  { id: 'demo-unknown', title: '未知分级隔离测试条目', nsfw: null, score: 9, type: 'TV', lane: 'familiar' },
])

export const PREVIEW_EXISTING_RECORDS = Object.freeze({
  'demo-12': Object.freeze({ status: 'watched', score: 9.1, review: '演示已有长评：再次点击不能覆盖这段评价。', demoOnly: true }),
  'demo-8': Object.freeze({ status: 'watching', score: 8.8, review: '演示中的在看记录。', demoOnly: true }),
})
