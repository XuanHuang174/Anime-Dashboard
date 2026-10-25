"""Versioned allowlist extraction. No writes, guessing, reviews or external calls."""
from collections import Counter
from functools import lru_cache
import re
import unicodedata

from .identity import normalized_title
from .taxonomy import AVAILABLE_TAGS, canonical_tag, genre_rules, TAXONOMY_VERSION

FEATURE_RULE_VERSION = 'journal-genres.v3'
FEATURE_SOURCES = ('private_base_tags', 'shared_base_tags', 'custom_tags')
IGNORED_REASONS = ('invalid', 'year', 'country', 'management', 'work_title', 'unknown')
COUNTRIES = frozenset(('日本', '中国', '美国', '韩国', '英国', '法国', '加拿大', '德国',
                      '台湾', '香港', 'japan', 'china', 'usa', 'uk', 'korea'))
MANAGEMENT_TAGS = frozenset(('二刷', '三刷', '重看', '年度最佳', '年度推荐', '神作',
                            '补番', '待补', '收藏', '已看', '想看', '在看', '看过', '搁置'))


def extract_genres(base_tags, custom_tags=(), *, titles=(), base_source='private_base_tags', taxonomy_version=None):
    """Copy only known genres and their field provenance, never raw private labels.

    Duplicates/aliases within or across fields contribute one feature per work.
    Empty private tags remain empty; this function accepts no catalog fallback.
    Malformed legacy JSON is ignored instead of interpreting dictionary keys.
    """
    if base_source not in FEATURE_SOURCES[:2]:
        raise ValueError('Unsupported base genre source')
    available, _ = genre_rules(taxonomy_version)
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
            tag = canonical_tag(value) if taxonomy_version in (None, TAXONOMY_VERSION) else canonical_tag(value, taxonomy_version=taxonomy_version)
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
                    for tag in available if tag in origins},
        'ignored_counts': dict(ignored),
    }


@lru_cache(maxsize=8)
def _genre_title_keys(available):
    # Only the small, immutable released vocabulary is cached, never raw tags,
    # catalog titles or private values. Keys also provide constant-time membership.
    return {tag: normalized_title(tag) for tag in available}


def feature_tags(raw, titles=(), *, taxonomy_version=None):
    """Exact tag-only projection without constructing discarded audit metadata.

    Public index builds do this for every catalog row. Unknown labels cannot
    contribute a feature, so they need no title/rejection-reason processing.
    Keep the full provenance/reason extractor for profile and audit callers.
    No input strings or user data are memoized across requests.
    """
    available, aliases = genre_rules(taxonomy_version)  # Validate even empty input.
    known = _genre_title_keys(available)
    names = {normalized_title(title) for title in titles if isinstance(title, str) and title}
    if isinstance(raw, str):
        values = unicodedata.normalize('NFKC', raw).split(',')
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        return frozenset()
    tags = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        value = unicodedata.normalize('NFKC', value).strip()
        # Same NFKC/strip/casefold/alias steps as canonical_tag; avoid normalizing
        # an already-normalized string again and linearly scanning the vocabulary.
        tag = aliases.get(value.casefold(), value)
        if tag not in known:
            continue
        value_key = known[value] if value in known else normalized_title(value)
        if (value_key in names or known[tag] in names
                or re.fullmatch(r'\d{4}(?:年.*|[-/]\d{1,2}(?:[-/]\d{1,2})?)?', value)
                or value.casefold() in COUNTRIES or value in MANAGEMENT_TAGS
                or re.fullmatch(r'[二三四五六七八九十\d]+刷', value)):
            continue
        tags.add(tag)
    return frozenset(tags)
