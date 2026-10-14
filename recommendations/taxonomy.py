"""Versioned, neutral subject vocabulary; raw source/user tags stay untouched."""
import unicodedata
from types import MappingProxyType

TAXONOMY_VERSION = 'genres.v4'
APPROVED_ALIAS_RELEASE = 'aliases.2026-10-14.approved-11'
APPROVED_GENRE_RELEASE = 'genres.2026-10-14.approved-7'
MAX_PREFERENCE_TAGS = 20
# These definitions document exact labels only; they are NOT classification
# prompts, parent relationships, synonyms, or rules for mining titles/reviews.
APPROVED_GENRES = {
    '热血': '强调拼搏、挑战、昂扬情绪的叙事气质；不自动等同于战斗或运动。',
    '后宫': '围绕同一主角展开的多角色恋爱或暧昧关系结构；不自动等同于恋爱、成人内容或古代宫廷。',
    '玄幻': '具有超自然力量、修炼体系等幻想设定的题材；只依据已有标签识别，不自动等同于奇幻或异世界。',
    '穿越': '角色跨越时间或世界的叙事机制；不自动等同于异世界或转生。',
    '魔法少女': '以魔法少女身份、能力及相关叙事为题材；不自动等同于魔法或变身。',
    '恐怖': '以恐惧、惊骇体验为主要表达方向；不自动等同于悬疑、猎奇或成人内容。',
    '武侠': '以武者、武学、江湖及侠义关系为题材；不自动等同于战斗、历史或修仙。',
}
AVAILABLE_TAGS = (
    '日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行',
    *APPROVED_GENRES,
)
# Only the 11 entries explicitly approved after the 5B-1 review. Casefolding
# also accepts Comedy/MUSIC; no unobserved draft alias is activated.
APPROVED_ALIASES = {
    '治愈系': '治愈', '治癒': '治愈', 'comedy': '喜剧', '日常系': '日常',
    '戀愛': '恋爱', 'music': '音乐', '戰鬥': '战斗', '旅行系': '旅行',
    '校園': '校园', '百合向': '百合', '職場': '职场',
}
ALIASES = {
    '搞笑': '喜剧', '真百': '百合', '真百合': '百合',
    '轻百': '百合', '轻百合': '百合', 'ゆり': '百合',
    'sci-fi': '科幻', 'scifi': '科幻', 'fantasy': '奇幻', 'slice of life': '日常',
    **APPROVED_ALIASES,
}

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
