"""Versioned, neutral subject vocabulary; raw source/user tags stay untouched."""
import unicodedata

TAXONOMY_VERSION = 'genres.v1'
MAX_PREFERENCE_TAGS = 20
AVAILABLE_TAGS = (
    '日常', '治愈', '喜剧', '校园', '恋爱', '百合', '科幻', '奇幻', '冒险', '悬疑',
    '推理', '战斗', '机战', '音乐', '运动', '历史', '职场', '亲情', '成长', '友情',
    '青春', '魔法', '异世界', '偶像', '美食', '旅行',
)
ALIASES = {
    '搞笑': '喜剧', '轻百': '百合', '轻百合': '百合', 'ゆり': '百合',
    'sci-fi': '科幻', 'scifi': '科幻', 'fantasy': '奇幻', 'slice of life': '日常',
}


def canonical_tag(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value).strip()
    value = ALIASES.get(value.casefold(), value)
    return value if value in AVAILABLE_TAGS else None


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
