"""Versioned, neutral subject vocabulary; raw source/user tags stay untouched."""
import unicodedata
from types import MappingProxyType

TAXONOMY_VERSION = 'genres.v6'
APPROVED_ALIAS_RELEASE = 'aliases.2026-10-20.approved-9'
APPROVED_GENRE_RELEASE = 'genres.2026-10-20.approved-8'
MAX_PREFERENCE_TAGS = 20
# These definitions document exact labels only; they are NOT classification
# prompts, parent relationships, synonyms, or rules for mining titles/reviews.
PREVIOUS_APPROVED_GENRES = {
    '热血': '强调拼搏、挑战、昂扬情绪的叙事气质；不自动等同于战斗或运动。',
    '后宫': '围绕同一主角展开的多角色恋爱或暧昧关系结构；不自动等同于恋爱、成人内容或古代宫廷。',
    '玄幻': '具有超自然力量、修炼体系等幻想设定的题材；只依据已有标签识别，不自动等同于奇幻或异世界。',
    '穿越': '角色跨越时间或世界的叙事机制；不自动等同于异世界或转生。',
    '魔法少女': '以魔法少女身份、能力及相关叙事为题材；不自动等同于魔法或变身。',
    '恐怖': '以恐惧、惊骇体验为主要表达方向；不自动等同于悬疑、猎奇或成人内容。',
    '武侠': '以武者、武学、江湖及侠义关系为题材；不自动等同于战斗、历史或修仙。',
}
APPROVED_GENRE_BATCH = {
    '萌系': '可爱角色及其审美体验的明确特征；不等同角色年龄、日常、治愈或安全评级，不自动吸收萌、卖萌、萌豚、废萌、美少女。',
    '战争': '战争冲突、经历及影响；不把战斗、历史、军事或兵器自动视为战争。',
    '动作': '动态动作场面的表现特征；独立于战斗，可共存但不相互生成标签。',
    '励志': '面对困难、努力追求目标的激励叙事气质；不自动等同成长、热血或运动。',
    '超能力': '角色具有超常能力的明确设定；不自动等同魔法、科幻、奇幻或战斗。',
    '童话': '明确标识的童话题材或表达；不合并童话改，不推导子供向或全年龄分级。',
    '犯罪': '围绕犯罪行为及相关关系的题材；不自动等同推理、悬疑、暴力或成人。',
    '赛博朋克': '明确赛博朋克设定或审美；不根据科幻、都市、机器人或视觉推断，不自动补科幻父级。',
}
APPROVED_GENRES = {**PREVIOUS_APPROVED_GENRES, **APPROVED_GENRE_BATCH}
AVAILABLE_TAGS = (
    '日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行',
    *APPROVED_GENRES,
)
# Preserve the previous approval and append ONLY the nine reviewed exact labels.
# This does not strip generic suffixes or split compound/free-form labels.
PREVIOUS_APPROVED_ALIASES = {
    '治愈系': '治愈', '治癒': '治愈', 'comedy': '喜剧', '日常系': '日常',
    '戀愛': '恋爱', 'music': '音乐', '戰鬥': '战斗', '旅行系': '旅行',
    '校園': '校园', '百合向': '百合', '職場': '职场',
}
APPROVED_ALIAS_BATCH = {
    '爱情': '恋爱', '治癒系': '治愈', '运动番': '运动', '运动系': '运动',
    '美食番': '美食', '日常番': '日常', '推理系': '推理', '恋爱番': '恋爱',
    '搞笑类': '喜剧',
}
APPROVED_ALIASES = {**PREVIOUS_APPROVED_ALIASES, **APPROVED_ALIAS_BATCH}
ALIASES = {
    '搞笑': '喜剧', '真百': '百合', '真百合': '百合',
    '轻百': '百合', '轻百合': '百合', 'ゆり': '百合',
    'sci-fi': '科幻', 'scifi': '科幻', 'fantasy': '奇幻', 'slice of life': '日常',
    **APPROVED_ALIASES,
}

# Frozen pre-expansion baseline. Explicit copies keep historical offline
# comparisons reproducible; never derive by subtracting future approvals.
PREVIOUS_TAXONOMY_VERSION = 'genres.v4'
PREVIOUS_AVAILABLE_TAGS = (
    '日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行',
    '热血', '后宫', '玄幻', '穿越', '魔法少女', '恐怖', '武侠',
)
PREVIOUS_ALIASES = MappingProxyType({
    '搞笑': '喜剧', '真百': '百合', '真百合': '百合', '轻百': '百合', '轻百合': '百合',
    'ゆり': '百合', 'sci-fi': '科幻', 'scifi': '科幻', 'fantasy': '奇幻', 'slice of life': '日常',
    '治愈系': '治愈', '治癒': '治愈', 'comedy': '喜剧', '日常系': '日常',
    '戀愛': '恋爱', 'music': '音乐', '戰鬥': '战斗', '旅行系': '旅行',
    '校園': '校园', '百合向': '百合', '職場': '职场',
})

# The nine-alias release is also frozen, independently of future approvals.
ALIAS_NINE_TAXONOMY_VERSION = 'genres.v5'
ALIAS_NINE_AVAILABLE_TAGS = PREVIOUS_AVAILABLE_TAGS
ALIAS_NINE_ALIASES = MappingProxyType({
    **PREVIOUS_ALIASES,
    '爱情': '恋爱', '治癒系': '治愈', '运动番': '运动', '运动系': '运动',
    '美食番': '美食', '日常番': '日常', '推理系': '推理', '恋爱番': '恋爱', '搞笑类': '喜剧',
})
AUDIT_TAXONOMY_VERSIONS = (TAXONOMY_VERSION, PREVIOUS_TAXONOMY_VERSION, ALIAS_NINE_TAXONOMY_VERSION)

# Frozen pre-5B-1 vocabulary, verified against the 5A baseline (eca457f).
# Explicit copies, NOT derived by subtracting future releases. Offline only.
LEGACY_TAXONOMY_VERSION = 'genres.v2'
LEGACY_AVAILABLE_TAGS = (
    '日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行',
)
LEGACY_ALIASES = MappingProxyType({
    '搞笑': '喜剧', '真百': '百合', '真百合': '百合', '轻百': '百合', '轻百合': '百合',
    'ゆり': '百合', 'sci-fi': '科幻', 'scifi': '科幻', 'fantasy': '奇幻', 'slice of life': '日常',
})


def genre_rules(version=None):
    if version in (None, TAXONOMY_VERSION):
        return AVAILABLE_TAGS, ALIASES
    if version == LEGACY_TAXONOMY_VERSION:
        return LEGACY_AVAILABLE_TAGS, LEGACY_ALIASES
    if version == PREVIOUS_TAXONOMY_VERSION:
        return PREVIOUS_AVAILABLE_TAGS, PREVIOUS_ALIASES
    if version == ALIAS_NINE_TAXONOMY_VERSION:
        return ALIAS_NINE_AVAILABLE_TAGS, ALIAS_NINE_ALIASES
    raise ValueError('Unsupported genre-rule version')


def canonical_tag(value, *, taxonomy_version=None):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value).strip()
    available, aliases = genre_rules(taxonomy_version)
    value = aliases.get(value.casefold(), value)
    return value if value in available else None


def canonical_tags(values):
    if not isinstance(values, (list, tuple)):
        return []
    accepted = {tag for value in values if (tag := canonical_tag(value))}
    # Deterministic order gives stable summaries and idempotent PATCH versions.
    return [tag for tag in AVAILABLE_TAGS if tag in accepted]


def validate_preference_tags(values):
    from django.core.exceptions import ValidationError
    if (not isinstance(values, list) or len(values) > MAX_PREFERENCE_TAGS
            or any(not isinstance(tag, str) or tag not in AVAILABLE_TAGS for tag in values)
            or len(set(values)) != len(values)):
        raise ValidationError('偏好必须是最多 20 个、不重复的规范题材标签。')
