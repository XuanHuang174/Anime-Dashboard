"""5B-1 review proposals ONLY. Never imported by serving taxonomy/ranking.

Exact labels, not co-occurrence/title inference. These are NOT approved aliases.
Approval requires a separate reviewed taxonomy change and version bump.
"""
import unicodedata

REVIEW_RULE_VERSION = 'public-tag-review.v1'
PROPOSAL_VERSION = 'genres.v3.proposal.1'

# Traditional forms / explicit genre names only. Subgenres and slang are NOT
# flattened to a vaguely related existing genre to inflate coverage.
PENDING_ALIASES = {
    '喜劇': '喜剧', '搞笑向': '喜剧', 'コメディ': '喜剧', 'comedy': '喜剧',
    '療癒': '治愈', '治癒': '治愈', '治愈系': '治愈', '日常系': '日常',
    '校園': '校园', '戀愛': '恋爱', '恋愛': '恋爱', 'romance': '恋爱',
    '百合向': '百合', '百合系': '百合',
    '科學幻想': '科幻', '科学幻想': '科幻', 'science fiction': '科幻', 'sci fi': '科幻',
    'ファンタジー': '奇幻', '冒險': '冒险', '懸疑': '悬疑',
    '戰鬥': '战斗', '機戰': '机战', '音樂': '音乐', 'music': '音乐',
    '運動': '运动', 'sports': '运动', '歷史': '历史', '職場': '职场',
    '親情': '亲情', '成長': '成长', 'friendship': '友情',
    '異世界': '异世界', '偶像系': '偶像', '美食系': '美食', '旅行系': '旅行',
}

# Definitions must be confirmed separately. No candidate below is simulated as
# an active genre, and none is folded into another genre (玄幻 != 奇幻 etc.).
GENRE_CANDIDATES = {
    '热血': '强调昂扬、拼搏的叙事气质；不等同于战斗，需确认边界。',
    '后宫': '多角色恋爱关系结构；不等同于恋爱标签的直接同义词。',
    '玄幻': '需独立确认东方幻想等适用边界；不直接归入奇幻或异世界。',
    '穿越': '角色跨时间或世界的叙事机制；不等同于异世界。',
    '魔法少女': '魔法少女题材；不直接展平为魔法。',
    'BL': '男性间恋爱情感题材候选；需确认与耽美等标签的关系，不推断内容分级。',
    '耽美': '需确认题材定义及与 BL 的边界；不从人物关系猜测。',
    '超现实': '超现实表达题材/风格候选；需确认是否纳入题材体系。',
    '实验': '实验性表达风格候选；需确认是否另设风格维度。',
    '猎奇': '含义跨度大，需确认定义和安全边界后决定是否纳入。',
    '恐怖': '以恐惧体验为主的题材候选；不直接等同于悬疑。',
    '武侠': '武侠题材候选；不直接等同于战斗或历史。',
}

NON_GENRE_GROUPS = {
    'format_or_medium': ('TV', 'TVA', 'WEB', 'OVA', 'OAD', 'ONA', 'SP', '短片', 'Short',
        '剧场版', '电影', '动画电影', '动画', '动画片', '短片集', '泡面', '泡面番',
        'MV', 'PV', 'CM', 'MV・PV・CM', '独立ova', '总集篇', '续作'),
    'adaptation_source': ('原创', '漫画改', '漫改', '小说改', '轻小说改', '轻改', '游戏改', 'GAL改', '网文改'),
    'region': ('欧美', '国产', '国漫', '国创', '国产动画', '中国动画', '日本动画', '美国动画'),
    'production_or_distribution': ('SUNRISE', '迪士尼', 'Disney', '东映', '東映アニメーション',
        'J.C.STAFF', 'MADHouse', '上海美术电影制片厂', 'OLM', 'Production.I.G',
        'Netflix', 'AIC', 'TMS', 'A-1Pictures', 'studiodeen'),
    'technique': ('3D', '定格动画', '赛璐珞'),
    'management_or_placeholder': ('童年', '旧物', '空', '无', '未定档', '其它', '其他', '待定', '未知'),
    'audience_not_genre': ('子供向',),
    # Counts are a metadata review signal, NOT a new content-rating classifier.
    'safety_metadata_review': ('里番', 'R18', 'R-18', '18禁', '成人', 'NSFW', '卖肉', '肉'),
}


def review_key(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


_ALIAS_INDEX = {review_key(source): target for source, target in PENDING_ALIASES.items()}
_CANDIDATE_INDEX = {review_key(source): definition for source, definition in GENRE_CANDIDATES.items()}
_EXCLUSION_INDEX = {review_key(value): reason for reason, values in NON_GENRE_GROUPS.items() for value in values}


def validate_proposals():
    from .taxonomy import AVAILABLE_TAGS, canonical_tag
    seen = set()
    for source, target in PENDING_ALIASES.items():
        key = review_key(source)
        if key in seen or target not in AVAILABLE_TAGS or canonical_tag(source) is not None:
            raise ValueError('Draft aliases must be unique, inactive, and target an existing canonical genre')
        seen.add(key)
    candidate_keys = {review_key(value) for value in GENRE_CANDIDATES}
    excluded_keys = {review_key(value) for values in NON_GENRE_GROUPS.values() for value in values}
    if seen & (candidate_keys | excluded_keys) or candidate_keys & excluded_keys:
        raise ValueError('Review categories must not overlap')


def classify_unknown(value):
    """Classify an already-unknown token; no fuzzy matching or learned labels."""
    key = review_key(value)
    if key in _ALIAS_INDEX:
        return {'category': 'pending_synonym', 'target': _ALIAS_INDEX[key],
                'rationale': '明确题材名称/文字变体候选；仅供人工审核，未应用。'}
    if key in _CANDIDATE_INDEX:
        return {'category': 'candidate_genre', 'target': None, 'rationale': _CANDIDATE_INDEX[key]}
    if key in _EXCLUSION_INDEX:
        return {'category': 'excluded_or_unknown', 'target': None, 'rationale': _EXCLUSION_INDEX[key]}
    return {'category': 'excluded_or_unknown', 'target': None,
            'rationale': 'unknown_keep_excluded_no_semantic_guess'}
