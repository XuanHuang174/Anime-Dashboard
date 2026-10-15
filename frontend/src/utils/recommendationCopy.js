// Display semantics only. Never used as ranking inputs or persistence values.
export const RECOMMENDATION_COPY = Object.freeze({
  publicScore: 'Bangumi 评分',
  publicScoreUnknown: '状态待核实',
  publicScoreMissing: '暂无数据',
  publicScoreHelp: 'Bangumi 评分是作品的公共评价，不是你的个人评分，也不是匹配率或喜欢概率。',
  publicZeroHelp: '目录评分值为 0：可能来自源站零值，或缺失、异常数据的归一化；目前无法区分，不据此判定为未评分或真实低分。',
  publicMissingHelp: '当前没有可展示的有效公共评分数据，不能据此判断作品质量。',
  personalScoreMissing: '评分数据暂不可用',
  ratedEvidence: '来自高分作品',
  explicitEvidence: '来自你选择的题材',
  evidenceUnavailable: '推荐依据暂不可用，请重新加载推荐后重试。',
  evidenceRecovery: '当前没有可展示的完整证据。请重新加载推荐后重试，不影响查看作品或手账中的原记录。',
  reloadEvidence: '重新加载推荐',
  evidenceBoundary: '共同题材用于解释关联，不代表唯一排序原因，也不是喜欢概率。Bangumi 评分是公共评价，不是你的个人评分。',
})

export const sourcePersonalScoreLabel = title => `你对《${title}》的评分：`
