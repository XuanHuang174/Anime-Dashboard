"""Bounded local catalog selection, independent from private journal/profile."""
import hashlib
import json

from django.db.models import Case, IntegerField, Q, Value, When

from .browse_controls import season_year
from .content_scope import catalog_for_scope
from .engine import MAX_CATALOG_ROWS, RecommendationUnavailable
from .features import FEATURE_RULE_VERSION, feature_tags
from .identity import public_cover_url
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION
from .exploration_contract import CATALOG_VERSION, SEARCH_LIMIT


def catalog_item(anime, scope):
    genres = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
    tags = [tag for tag in AVAILABLE_TAGS if tag in genres]
    feature_revision = hashlib.sha256(json.dumps(
        [anime.bangumi_id, anime.title_zh, anime.title_jp, tags, TAXONOMY_VERSION,
         FEATURE_RULE_VERSION], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'bangumi_id': anime.bangumi_id, 'title_zh': anime.title_zh, 'title_jp': anime.title_jp,
            'year': season_year(anime.season), 'season': anime.season, 'type': anime.status_type,
            'score': anime.score, 'tags': tags,
            'content_rating': 'adult' if anime.nsfw else 'non_adult',
            'cover_url': '' if anime.nsfw else public_cover_url(anime, scope),
            'can_seed': bool(tags), 'unavailable_reason': None if tags else 'no_valid_topics',
            'feature_revision': feature_revision}


def search_catalog(term, scope):
    catalog = catalog_for_scope(scope)
    # Refuse an unbounded catalog instead of silently searching a truncated subset.
    if catalog.order_by().values('pk')[:MAX_CATALOG_ROWS + 1].count() > MAX_CATALOG_ROWS:
        raise RecommendationUnavailable('探索目录超出当前处理上限。')
    if term['kind'] == 'bangumi_id':
        matches = catalog.filter(bangumi_id=int(term['value'])).order_by('bangumi_id')
    else:
        q = term['value']
        matches = catalog.filter(Q(title_zh__icontains=q) | Q(title_jp__icontains=q)).annotate(
            search_rank=Case(
                When(Q(title_zh__iexact=q) | Q(title_jp__iexact=q), then=Value(0)),
                When(Q(title_zh__istartswith=q) | Q(title_jp__istartswith=q), then=Value(1)),
                default=Value(2), output_field=IntegerField())).order_by('search_rank', '-score', 'bangumi_id')
    rows = list(matches.only('bangumi_id', 'title_zh', 'title_jp', 'tags', 'score',
                             'season', 'status_type', 'cover_url', 'nsfw')[:SEARCH_LIMIT + 1])
    return {'contract_version': CATALOG_VERSION, 'identity_namespace': 'bangumi',
            'taxonomy_version': TAXONOMY_VERSION, 'feature_rule_version': FEATURE_RULE_VERSION,
            'query': term, 'limit': SEARCH_LIMIT, 'has_more': len(rows) > SEARCH_LIMIT,
            'results': [catalog_item(row, scope) for row in rows[:SEARCH_LIMIT]]}
