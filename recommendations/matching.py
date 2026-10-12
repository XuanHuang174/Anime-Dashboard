"""Versioned public candidate lookup. Loose lookup is NEVER automatic evidence."""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict

from django.core.cache import cache
from django.db.models import Count, Max
from rest_framework.exceptions import APIException

from .identity import normalized_title
from .models import RecommendationAnime
from .normalization import clean_season

MATCHING_VERSION = 'dual-title-quarter.v1'


class IdentityCatalogUnavailable(APIException):
    status_code = 503
    default_detail = '作品身份资料暂不可用，请稍后重试。'


def quarter(value):
    value = clean_season(value, {})
    return value if re.fullmatch(r'\d{4}-(01|04|07|10)', value) else ''


def lookup_title(value):
    return ''.join(c for c in normalized_title(value) if not unicodedata.category(c).startswith(('P', 'S')))


def format_hint(zh, jp):
    text = f'{zh} {jp}'.casefold()
    if re.search(r'\bova\b|オリジナルビデオ', text):
        return 'OVA'
    if re.search(r'剧场版|劇場版|\bmovie\b', text):
        return '剧场版'
    return ''


def effective_metadata(row):
    if row['metadata_initialized']:
        return {'title_zh': row['private_chinese_title'], 'title_jp': row['private_japanese_title'],
                'season': row['private_airing_period'], 'tags': row['private_base_tags']}
    return {'title_zh': row['anime__chinese_title'], 'title_jp': row['anime__japanese_title'],
            'season': row['anime__airing_period'], 'tags': row['anime__base_tags']}


def metadata_signature(meta):
    # Unknown dates remain distinct rather than all collapsing to an empty quarter.
    return [normalized_title(meta['title_zh']), normalized_title(meta['title_jp']),
            quarter(meta['season']) or (meta['season'] or '').strip(),
            format_hint(meta['title_zh'], meta['title_jp'])]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def record_fingerprint(row):
    return digest([row['anime_id'], metadata_signature(effective_metadata(row))])


def catalog_fingerprint(anime):
    row = anime if isinstance(anime, dict) else {'title_zh': anime.title_zh, 'title_jp': anime.title_jp,
                                                'season': anime.season, 'status_type': anime.status_type}
    return digest([metadata_signature(row), row['status_type']])


class CatalogMatcher:
    def __init__(self, rows):
        self.rows = {row['bangumi_id']: row for row in rows}
        self.zh, self.jp, self.loose = defaultdict(set), defaultdict(set), defaultdict(set)
        for identity, row in self.rows.items():
            if row['title_zh']:
                self.zh[normalized_title(row['title_zh'])].add(identity)
                self.loose[lookup_title(row['title_zh'])].add(identity)
            if row['title_jp']:
                self.jp[normalized_title(row['title_jp'])].add(identity)
                self.loose[lookup_title(row['title_jp'])].add(identity)

    def match(self, meta):
        zh, jp, period = normalized_title(meta['title_zh']), normalized_title(meta['title_jp']), quarter(meta['season'])
        exact = self.zh.get(zh, set()) & self.jp.get(jp, set()) if zh and jp else set()
        hint = format_hint(meta['title_zh'], meta['title_jp'])
        same = [i for i in exact if period and quarter(self.rows[i]['season']) == period]
        # Include unsafe/unknown matches when deciding uniqueness, but never publish them.
        if len(same) == 1:
            row = self.rows[same[0]]
            if row['nsfw'] is False and (not hint or hint == row['status_type']):
                return {'status': 'strong_unique', 'target': row, 'candidates': [row]}
        hits = (self.zh.get(zh, set()) | self.jp.get(jp, set())
                | self.loose.get(lookup_title(meta['title_zh']), set())
                | self.loose.get(lookup_title(meta['title_jp']), set()))
        candidates = [self.rows[i] for i in hits if self.rows[i]['nsfw'] is False
                      and (not hint or hint == self.rows[i]['status_type'])]
        candidates.sort(key=lambda row: (quarter(row['season']) != period,
                                        row['bangumi_id'] not in exact, row['bangumi_id']))
        return {'status': 'needs_confirmation' if candidates else 'no_safe_candidate',
                'target': None, 'candidates': candidates[:5]}


def catalog_matcher(force=False):
    query = RecommendationAnime.objects.filter(bangumi_id__isnull=False)
    revision = query.aggregate(count=Count('pk'), updated=Max('updated_at'))
    if revision['count'] > 50000:
        raise IdentityCatalogUnavailable()
    signature = (revision['count'], str(revision['updated']), MATCHING_VERSION)
    cached = cache.get('recommendations.identity-public.v1') if not force else None
    if cached and cached['signature'] == signature:
        return CatalogMatcher(cached['rows'])
    rows = list(query.order_by('bangumi_id').values('pk', 'bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type', 'nsfw')[:50001])
    if len(rows) > 50000:
        raise IdentityCatalogUnavailable()
    cache.set('recommendations.identity-public.v1', {'signature': signature, 'rows': rows}, 60)
    return CatalogMatcher(rows)
