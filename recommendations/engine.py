"""Bounded, deterministic content ranking. Only public features are cached."""
import hashlib
import hmac
import json
import math
import threading
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Subquery
from rest_framework.exceptions import APIException

from anime.models import UserAnimeRecord
from .identity import normalized_title, safe_catalog
from .services import preference_contract, profile_contract
from .taxonomy import TAXONOMY_VERSION, canonical_tags

ALGORITHM_VERSION = 'content.v1'
FEATURE_VERSION = 'genres-idf.v1'
CONTRACT_VERSION = 'recommendations.3b.v1'
CACHE_KEY = 'recommendations.public-features.v1'
MAX_CATALOG_ROWS = 50000
CANDIDATE_POOL = 400
MAX_RESULTS = 120
_feature_lock = threading.Lock()


class RecommendationUnavailable(APIException):
    status_code = 503
    default_detail = '推荐数据暂不可用，请稍后重试。'


class RecommendationSnapshotChanged(APIException):
    status_code = 409
    default_detail = '喜好、筛选或数据已变化，请重新加载第一页。'


def feature_tags(raw, titles=()):
    values = raw.replace('，', ',').split(',') if isinstance(raw, str) else raw
    names = {normalized_title(title) for title in titles if title}
    return frozenset(tag for tag in canonical_tags(values) if normalized_title(tag) not in names)


def public_features():
    revision = safe_catalog().aggregate(count=Count('pk'), updated=Max('updated_at'))
    signature = (revision['count'], str(revision['updated']), TAXONOMY_VERSION, FEATURE_VERSION)
    if revision['count'] > MAX_CATALOG_ROWS:
        raise RecommendationUnavailable()
    value = cache.get(CACHE_KEY)
    if value and value['signature'] == signature:
        return value
    with _feature_lock:
        value = cache.get(CACHE_KEY)
        if value and value['signature'] == signature:
            return value
        rows = safe_catalog().order_by('bangumi_id').values(
            'bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type',
        )[:MAX_CATALOG_ROWS + 1]
        features, frequency = [], Counter()
        for row in rows:
            tags = feature_tags(row['tags'], (row['title_zh'], row['title_jp']))
            frequency.update(tags)
            features.append((row['bangumi_id'], tags, row['score'], row['status_type']))
        if len(features) > MAX_CATALOG_ROWS:
            raise RecommendationUnavailable()
        weights = {tag: min(3.0, 1 + math.log((1 + len(features)) / (1 + count)))
                   for tag, count in frequency.items()}
        value = {'signature': signature, 'features': features, 'weights': weights}
        cache.set(CACHE_KEY, value, timeout=60)
        return value


def user_context(user):
    """Read own seeds anew. No inferred private state or HTTP payload cache."""
    profile = profile_contract(user)
    preferences = preference_contract(user)
    rows = list(UserAnimeRecord.objects.filter(
        user=user, watch_status='completed',
        personal_score__gte=Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD)),
        anime__bgm_id__in=Subquery(safe_catalog().order_by().values('bangumi_id')),
    ).order_by('anime__bgm_id').values(
        'anime__bgm_id', 'personal_score', 'metadata_initialized',
        'private_base_tags', 'anime__base_tags',
    ))
    sources = {row['bangumi_id']: row for row in safe_catalog().filter(
        bangumi_id__in=[row['anime__bgm_id'] for row in rows],
    ).values('bangumi_id', 'title_zh', 'title_jp')}
    seeds = []
    for row in rows:
        source = sources.get(row['anime__bgm_id'])
        if not source:
            continue
        raw = row['private_base_tags'] if row['metadata_initialized'] else row['anime__base_tags']
        tags = feature_tags(raw, (source['title_zh'], source['title_jp']))
        if tags:
            seeds.append({'bangumi_id': source['bangumi_id'], 'title': source['title_zh'] or source['title_jp'],
                          'score': float(row['personal_score']), 'tags': tags})
    mode = ('personalized' if not profile['cold_start'] and seeds
            else 'preference_based' if preferences['tags'] else 'cold_start')
    owned = dict(UserAnimeRecord.objects.filter(user=user, anime__bgm_id__isnull=False)
                 .values_list('anime__bgm_id', 'watch_status'))
    return {'profile': profile, 'preferences': preferences, 'seeds': seeds,
            'mode': mode, 'owned': owned}


def taste_vector(context):
    counts = Counter()
    if context['mode'] == 'personalized':
        for seed in context['seeds']:
            for tag in seed['tags']:
                counts[tag] += max(.1, seed['score'] - float(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD) + 1.0) / len(seed['tags'])
        total = sum(counts.values()) or 1
        counts = Counter({tag: value / total for tag, value in counts.items()})
    chosen = context['preferences']['tags']
    if chosen:
        counts = Counter({tag: value * .75 for tag, value in counts.items()})
        for tag in chosen:
            counts[tag] += (.25 if context['mode'] == 'personalized' else 1) / len(chosen)
    return counts


def evidence_for(tags, context, weights):
    # Cap explanation search while retaining genre coverage. Profile uses ALL seeds.
    if 'explanation_seeds' not in context:
        ordered = sorted(context['seeds'], key=lambda seed: (-seed['score'], seed['bangumi_id']))
        selected, covered = [], set()
        for seed in ordered:
            if seed['tags'] - covered:
                selected.append(seed)
                covered.update(seed['tags'])
        selected += [seed for seed in ordered[:64] if seed not in selected]
        context['explanation_seeds'] = selected  # Request-local only, never placed in shared cache.
    selected = context['explanation_seeds']
    best = None
    if context['mode'] == 'personalized':
        for seed in selected:
            shared = tags & seed['tags']
            if not shared:
                continue
            overlap = sum(weights.get(tag, 1) for tag in shared) / sum(weights.get(tag, 1) for tag in tags | seed['tags'])
            key = (overlap, seed['score'], -seed['bangumi_id'])
            if best is None or key > best[0]:
                best = (key, seed, shared)
    if best:
        seed, shared = best[1], best[2]
        shared = sorted(shared, key=lambda tag: (-weights.get(tag, 1), tag))
        shown = '、'.join(f'“{tag}”' for tag in shared[:2])
        return {'kind': 'rated_work', 'source': {'bangumi_id': seed['bangumi_id'], 'title': seed['title'],
                                               'personal_score': f'{seed["score"]:.1f}'},
                'shared_tags': shared}, f'因为你给《{seed["title"]}》打了 {seed["score"]:.1f} 分，这部作品同样包含{shown}标签。', best[0][0]
    shared = tags & set(context['preferences']['tags'])
    if shared:
        shared = sorted(shared)
        return {'kind': 'explicit_preferences', 'source': None, 'shared_tags': shared}, '根据你明确选择的' + '、'.join(shared[:2]) + '题材，为你探索这部作品。', len(shared) / len(tags)
    return None, '', 0


def candidate_score(tags, score, vector, weights, category, affinity=0):
    if not tags or not tags & vector.keys():
        return None
    dot = sum(weights.get(tag, 1) ** 2 * vector[tag] for tag in tags if tag in vector)
    norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in tags))
    user_norm = math.sqrt(sum((weights.get(tag, 1) * value) ** 2 for tag, value in vector.items()))
    related = dot / (norm * user_norm) if user_norm else 0
    novelty = sum(weights.get(tag, 1) for tag in tags - vector.keys()) / sum(weights.get(tag, 1) for tag in tags)
    quality = max(0, min(10, score)) / 10
    if category == 'explore':
        return .5 * related + .4 * novelty + .1 * quality if novelty else None
    if category == 'familiar':
        return .65 * affinity + .3 * related + .05 * quality
    return .85 * related + .1 * quality + .05 * novelty


def rank_candidates(user, options):
    context = user_context(user)
    if context['mode'] == 'cold_start':
        return context, [], 'cold', []
    index = public_features()
    weights, vector = index['weights'], taste_vector(context)
    seed_ids = {seed['bangumi_id'] for seed in context['seeds']}
    shortlist = []
    for identity, tags, score, status_type in index['features']:
        if identity in seed_ids or (options['hide_recorded'] and identity in context['owned']):
            continue
        if score < float(options['min_score']) or (options['type'] != 'all' and status_type != options['type']):
            continue
        affinity = evidence_for(tags, context, weights)[2] if options['category'] == 'familiar' else 0
        value = candidate_score(tags, score, vector, weights, options['category'], affinity)
        if value is not None:
            shortlist.append((value, identity))
    shortlist.sort(key=lambda pair: (-pair[0], pair[1]))
    pool_ids = [identity for _, identity in shortlist[:CANDIDATE_POOL]]
    # Fetch CURRENT safe rows before evidence, ranking, pagination and serialization.
    query = safe_catalog().filter(bangumi_id__in=pool_ids, score__gte=options['min_score'])
    if options['type'] != 'all':
        query = query.filter(status_type=options['type'])
    rows = list(query.order_by('bangumi_id').defer('synopsis', 'source_cover_url'))
    ranked = []
    for anime in rows:
        tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
        evidence, reason, affinity = evidence_for(tags, context, weights)
        if not evidence or (options['category'] == 'familiar' and context['mode'] == 'personalized' and evidence['kind'] != 'rated_work'):
            continue
        value = candidate_score(tags, anime.score, vector, weights, options['category'], affinity)
        if value is not None:
            ranked.append({'anime': anime, 'tags': tags, 'value': value, 'evidence': evidence, 'reason': reason})
    ranked.sort(key=lambda item: (-item['value'], item['anime'].bangumi_id))
    if options['category'] == 'all':
        chosen = []
        while ranked and len(chosen) < MAX_RESULTS:
            def diverse(item):
                overlap = max((len(item['tags'] & prior['tags']) / len(item['tags'] | prior['tags']) for prior in chosen[-5:]), default=0)
                return (.5 * item['value'] - .5 * overlap, -item['anime'].bangumi_id)
            winner = max(ranked, key=diverse)
            ranked.remove(winner)
            chosen.append(winner)
        ranked = chosen
    else:
        ranked = ranked[:MAX_RESULTS]
    # Opaque, user-bound state token; never exposes ratings or user IDs themselves.
    state = {'user': user.pk, 'algorithm': ALGORITHM_VERSION, 'features': FEATURE_VERSION,
             'preferences': (context['preferences']['version'], context['preferences']['tags']),
             'seeds': [(seed['bangumi_id'], seed['score'], sorted(seed['tags'])) for seed in context['seeds']],
             'owned': sorted(context['owned'].items()),
             'query': {key: str(options[key]) for key in ('category', 'type', 'min_score', 'hide_recorded', 'page_size')},
             'items': [(item['anime'].bangumi_id, item['anime'].score, sorted(item['tags']), item['reason'],
                        item['anime'].cover_url, item['anime'].title_zh, item['anime'].title_jp,
                        item['anime'].status_type, item['anime'].season) for item in ranked]}
    token = hmac.new(settings.SECRET_KEY.encode(), json.dumps(state, ensure_ascii=False, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    return context, ranked, token, weights
