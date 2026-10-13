"""Versioned allowlist extraction. No writes, guessing, reviews or external calls."""
from collections import Counter
import re
import unicodedata

from .identity import normalized_title
from .taxonomy import AVAILABLE_TAGS, canonical_tag

FEATURE_RULE_VERSION = 'journal-genres.v1'
FEATURE_SOURCES = ('private_base_tags', 'shared_base_tags', 'custom_tags')
IGNORED_REASONS = ('invalid', 'year', 'country', 'management', 'work_title', 'unknown')
COUNTRIES = frozenset(('日本', '中国', '美国', '韩国', '英国', '法国', '加拿大', '德国',
                      '台湾', '香港', 'japan', 'china', 'usa', 'uk', 'korea'))
MANAGEMENT_TAGS = frozenset(('二刷', '三刷', '重看', '年度最佳', '年度推荐', '神作',
                            '补番', '待补', '收藏', '已看', '想看', '在看', '看过', '搁置'))


def extract_genres(base_tags, custom_tags=(), *, titles=(), base_source='private_base_tags'):
    """Copy only known genres and their field provenance, never raw private labels.

    Duplicates/aliases within or across fields contribute one feature per work.
    Empty private tags remain empty; this function accepts no catalog fallback.
    Malformed legacy JSON is ignored instead of interpreting dictionary keys.
    """
    if base_source not in FEATURE_SOURCES[:2]:
        raise ValueError('Unsupported base genre source')
    names = {normalized_title(title) for title in titles if isinstance(title, str) and title}
    origins, ignored = {}, Counter({reason: 0 for reason in IGNORED_REASONS})
    for raw, source in ((base_tags, base_source), (custom_tags, 'custom_tags')):
        if raw is None:
            values = ()
        elif isinstance(raw, str):
            values = unicodedata.normalize('NFKC', raw).split(',')
        elif isinstance(raw, (list, tuple)):
            values = raw
        else:
            ignored['invalid'] += 1
            continue
        for value in values:
            if not isinstance(value, str) or not value.strip():
                ignored['invalid'] += 1
                continue
            value = unicodedata.normalize('NFKC', value).strip()
            tag = canonical_tag(value)
            if normalized_title(value) in names or (tag and normalized_title(tag) in names):
                ignored['work_title'] += 1
            elif re.fullmatch(r'\d{4}(?:年.*|[-/]\d{1,2}(?:[-/]\d{1,2})?)?', value):
                ignored['year'] += 1
            elif value.casefold() in COUNTRIES:
                ignored['country'] += 1
            elif value in MANAGEMENT_TAGS or re.fullmatch(r'[二三四五六七八九十\d]+刷', value):
                ignored['management'] += 1
            elif tag is None:
                ignored['unknown'] += 1
            else:
                origins.setdefault(tag, set()).add(source)
    return {
        'tags': frozenset(origins),
        'sources': {tag: tuple(source for source in FEATURE_SOURCES if source in origins[tag])
                    for tag in AVAILABLE_TAGS if tag in origins},
        'ignored_counts': dict(ignored),
    }


def feature_tags(raw, titles=()):
    # Public candidate features use the same vocabulary, not users' private rows.
    return extract_genres(raw, titles=titles, base_source='shared_base_tags')['tags']
