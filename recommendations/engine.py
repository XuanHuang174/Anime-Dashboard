"""Bounded, deterministic content ranking. Only public features are cached."""
import hashlib
import hmac
import json
import math
import threading
from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from rest_framework.exceptions import APIException

from .identity import safe_catalog
from .services import profile_data
from .taxonomy import TAXONOMY_VERSION, genre_rules
from .features import feature_tags, FEATURE_RULE_VERSION
from . import ranking_v2, taxonomy as vocabulary

ALGORITHM_VERSION = 'content.v1'
EXPERIMENTAL_ALGORITHM_VERSION = 'content.v2'
FEATURE_VERSION = 'genres-idf.v4'
CONTRACT_VERSION = 'recommendations.3b.v1'
CACHE_KEY = 'recommendations.public-features.v4'
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


def build_feature_index(rows, *, signature=None, taxonomy_version=None):
    """Pure public feature construction shared by serving and read-only evaluation."""
    features, frequency = [], Counter()
    for row in rows:
        tags = feature_tags(row['tags'], (row['title_zh'], row['title_jp']), taxonomy_version=taxonomy_version)
        frequency.update(tags)
        features.append((row['bangumi_id'], tags, row['score'], row['status_type']))
        if len(features) > MAX_CATALOG_ROWS:
            raise RecommendationUnavailable()
    weights = {tag: min(3.0, 1 + math.log((1 + len(features)) / (1 + count)))
               for tag, count in frequency.items()}
    return {'signature': signature, 'features': features, 'weights': weights,
            'taxonomy_version': taxonomy_version or vocabulary.TAXONOMY_VERSION}


def public_features(*, algorithm_version=ALGORITHM_VERSION):
    from .version_control import cache_namespace
    key = cache_namespace(algorithm_version, TAXONOMY_VERSION, FEATURE_VERSION, FEATURE_RULE_VERSION)
    revision = safe_catalog().aggregate(count=Count('pk'), updated=Max('updated_at'))
    signature = (revision['count'], str(revision['updated']), algorithm_version, TAXONOMY_VERSION, FEATURE_VERSION, FEATURE_RULE_VERSION)
    if revision['count'] > MAX_CATALOG_ROWS:
        raise RecommendationUnavailable()
    value = cache.get(key)
    if value and value['signature'] == signature:
        return value
    with _feature_lock:
        value = cache.get(key)
        if value and value['signature'] == signature:
            return value
        rows = safe_catalog().order_by('bangumi_id').values(
            'bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type',
        )[:MAX_CATALOG_ROWS + 1]
        value = build_feature_index(rows, signature=signature)
        cache.set(key, value, timeout=60)
        return value


def user_context(user):
    """Read own seeds anew. No inferred private state or HTTP payload cache."""
    profile, records, seeds = profile_data(user)
    # Reuse this request's explicit-preference read; common topics are not input.
    preferences = profile['extraction']['explicit_preferences']
    mode = ('personalized' if not profile['cold_start'] and seeds
            else 'preference_based' if preferences['tags'] else 'cold_start')
    priority = {'completed': 4, 'watching': 3, 'plan': 2, 'on_hold': 1}
    owned = {}
    for row in records:
        identity = row['resolved_bangumi_id']
        if identity and priority.get(row['watch_status'], 0) > priority.get(owned.get(identity), 0):
            owned[identity] = row['watch_status']
    return {'profile': profile, 'preferences': preferences, 'seeds': seeds, 'taxonomy_version': vocabulary.TAXONOMY_VERSION,
            'mode': mode, 'owned': owned}


def taste_vector(context, *, deterministic=False):
    counts = Counter()
    if context['mode'] == 'personalized':
        for seed in context['seeds']:
            for tag in (sorted(seed['tags']) if deterministic else seed['tags']):
                counts[tag] += max(.1, seed['score'] - float(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD) + 1.0) / len(seed['tags'])
        total = sum(counts[tag] for tag in sorted(counts)) if deterministic else sum(counts.values())
        total = total or 1
        counts = Counter({tag: value / total for tag, value in counts.items()})
    chosen = context['preferences']['tags']
    if chosen:
        counts = Counter({tag: value * .75 for tag, value in counts.items()})
        for tag in chosen:
            counts[tag] += (.25 if context['mode'] == 'personalized' else 1) / len(chosen)
    return counts


def evidence_for(tags, context, weights, *, deterministic=False):
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
            shared_order = sorted(shared) if deterministic else shared
            union = tags | seed['tags']
            union_order = sorted(union) if deterministic else union
            overlap = sum(weights.get(tag, 1) for tag in shared_order) / sum(weights.get(tag, 1) for tag in union_order)
            key = (overlap, seed['score'], -seed['bangumi_id'])
            if best is None or key > best[0]:
                best = (key, seed, shared)
    if best:
        seed, shared = best[1], best[2]
        shared = sorted(shared, key=lambda tag: (-weights.get(tag, 1), tag))
        shown = '、'.join(f'“{tag}”' for tag in shared[:2])
        return {'kind': 'rated_work', 'source': {'bangumi_id': seed['bangumi_id'], 'title': seed['title'],
                                               'personal_score': f'{seed["score"]:.1f}'},
                'shared_tags': shared, 'feature_rule_version': FEATURE_RULE_VERSION,
                'shared_tag_sources': {tag: list(seed['feature_sources'][tag]) for tag in shared}}, f'因为你给《{seed["title"]}》打了 {seed["score"]:.1f} 分，这部作品同样包含{shown}标签。', best[0][0]
    shared = tags & set(context['preferences']['tags'])
    if shared:
        shared = sorted(shared)
        return {'kind': 'explicit_preferences', 'source': None, 'shared_tags': shared}, '根据你明确选择的' + '、'.join(shared[:2]) + '题材，为你探索这部作品。', len(shared) / len(tags)
    return None, '', 0


def candidate_score(tags, score, vector, weights, category, affinity=0, *, exploration_weights=(.5, .4, .1), deterministic=False):
    if not tags or not tags & vector.keys():
        return None
    tag_order = sorted(tags) if deterministic else tags
    vector_order = sorted(vector.items()) if deterministic else vector.items()
    dot = sum(weights.get(tag, 1) ** 2 * vector[tag] for tag in tag_order if tag in vector)
    norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in tag_order))
    user_norm = math.sqrt(sum((weights.get(tag, 1) * value) ** 2 for tag, value in vector_order))
    related = dot / (norm * user_norm) if user_norm else 0
    novel = tags - vector.keys()
    novelty = sum(weights.get(tag, 1) for tag in (sorted(novel) if deterministic else novel)) / sum(weights.get(tag, 1) for tag in tag_order)
    quality = max(0, min(10, score)) / 10
    if category == 'explore':
        related_weight, novelty_weight, quality_weight = exploration_weights
        return related_weight * related + novelty_weight * novelty + quality_weight * quality if novelty else None
    if category == 'familiar':
        return .65 * affinity + .3 * related + .05 * quality
    return .85 * related + .1 * quality + .05 * novelty


def _rank_context_v1(context, options, index):
    """Same V1 ranking, supplied request-local context/index; not an API input."""
    if context['mode'] == 'cold_start':
        return [], []
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
    context['_ranking_trace'] = {'eligible_candidates': len(shortlist), 'candidate_pool': len(pool_ids)}
    # Fetch CURRENT safe rows before evidence, ranking, pagination and serialization.
    query = safe_catalog().filter(bangumi_id__in=pool_ids, score__gte=options['min_score'])
    if options['type'] != 'all':
        query = query.filter(status_type=options['type'])
    rows = list(query.order_by('bangumi_id').defer('synopsis', 'source_cover_url'))
    ranked = []
    for anime in rows:
        tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp), taxonomy_version=index.get('taxonomy_version'))
        evidence, reason, affinity = evidence_for(tags, context, weights)
        if not evidence or (options['category'] == 'familiar' and context['mode'] == 'personalized' and evidence['kind'] != 'rated_work'):
            continue
        value = candidate_score(tags, anime.score, vector, weights, options['category'], affinity)
        if value is not None:
            ranked.append({'anime': anime, 'tags': tags, 'value': value, 'evidence': evidence, 'reason': reason})
    ranked.sort(key=lambda item: (-item['value'], item['anime'].bangumi_id))
    context['_ranking_trace']['validated_candidates'] = len(ranked)
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
    return ranked, weights


def configured_algorithm():
    version = getattr(settings, 'RECOMMENDATION_ALGORITHM_VERSION', ALGORITHM_VERSION)
    if version not in (ALGORITHM_VERSION, EXPERIMENTAL_ALGORITHM_VERSION):
        raise RecommendationUnavailable('推荐算法配置暂不可用，请联系管理员。')
    if version == EXPERIMENTAL_ALGORITHM_VERSION:
        from .version_control import require_v2_approval
        try:
            require_v2_approval()
        except ValueError:
            raise RecommendationUnavailable('V2 尚未获得当前版本的验收凭据，请回滚 V1 或完成验收。') from None
    return version


def _v2_item(row, context, options, weights, vector, seed_ids, taxonomy_version=None):
    identity = row['bangumi_id']
    if identity in seed_ids or (
            options['hide_recorded'] and identity in context['owned']):
        return None
    tags = feature_tags(row['tags'], (row['title_zh'], row['title_jp']), taxonomy_version=taxonomy_version)
    if not tags & vector.keys():
        return None
    evidence, reason, affinity = evidence_for(tags, context, weights, deterministic=True)
    if not evidence or (options['category'] == 'familiar' and context['mode'] == 'personalized'
                        and evidence['kind'] != 'rated_work'):
        return None
    value = candidate_score(tags, row['score'], vector, weights, options['category'], affinity,
                            exploration_weights=ranking_v2.EXPLORATION_WEIGHTS, deterministic=True)
    if value is None or not math.isfinite(value):
        return None
    return {'bangumi_id': identity, 'tags': tags, 'value': value, 'evidence': evidence, 'reason': reason}


def _rank_context_v2(context, options, index):
    if context['mode'] == 'cold_start':
        return [], []
    weights, vector = index['weights'], taste_vector(context, deterministic=True)
    seed_ids = {seed['bangumi_id'] for seed in context['seeds']}
    # A stale cached feature row is NEVER authority for safety or evidence. Read
    # bounded CURRENT public metadata before balancing, so invalid high-frequency
    # rows cannot steal the rare-genre quota. No synopsis, images or private reviews.
    query = safe_catalog().filter(score__gte=options['min_score'])
    if options['type'] != 'all':
        query = query.filter(status_type=options['type'])
    fields = ('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type')
    eligible = []
    for position, row in enumerate(query.order_by('bangumi_id').values(*fields)[:MAX_CATALOG_ROWS + 1].iterator(chunk_size=500), 1):
        if position > MAX_CATALOG_ROWS:
            raise RecommendationUnavailable()
        item = _v2_item(row, context, options, weights, vector, seed_ids, index.get('taxonomy_version'))
        if item:
            eligible.append(item)
    pool = ranking_v2.balanced_candidate_pool(eligible, vector, limit=CANDIDATE_POOL)
    context['_ranking_trace'] = {'eligible_candidates': len(eligible), 'candidate_pool': len(pool)}
    # Recheck immediately before ranking/serialization; a safety change during
    # pool construction can only remove rows, never bypass the backend filter.
    rows = query.filter(bangumi_id__in=[item['bangumi_id'] for item in pool]).order_by('bangumi_id').defer('synopsis', 'source_cover_url')
    current = []
    for anime in rows:
        item = _v2_item({field: getattr(anime, field) for field in fields}, context, options, weights, vector, seed_ids, index.get('taxonomy_version'))
        if item:
            current.append({**item, 'anime': anime})
    # Source safety is just as important as candidate safety. A source changing
    # classification during a long pool scan must trigger a fresh profile read,
    # not leak its title/rating through an otherwise safe candidate's reason.
    if context['mode'] == 'personalized' and seed_ids != set(safe_catalog().filter(
            bangumi_id__in=seed_ids).values_list('bangumi_id', flat=True)):
        raise RecommendationSnapshotChanged()
    context['_ranking_trace']['validated_candidates'] = len(current)
    return ranking_v2.rerank_candidates(current, vector, weights, category=options['category'], limit=MAX_RESULTS), weights


def rank_context(context, options, index, *, algorithm_version=ALGORITHM_VERSION):
    """Explicit offline version; V1 baseline never follows a deployment flag."""
    genre_rules(index.get('taxonomy_version'))
    if context.get('taxonomy_version', vocabulary.TAXONOMY_VERSION) != index.get('taxonomy_version', vocabulary.TAXONOMY_VERSION):
        raise RecommendationUnavailable('画像与候选题材版本不一致。')
    if algorithm_version == ALGORITHM_VERSION:
        return _rank_context_v1(context, options, index)
    if algorithm_version == EXPERIMENTAL_ALGORITHM_VERSION:
        return _rank_context_v2(context, options, index)
    raise RecommendationUnavailable('推荐算法配置暂不可用，请联系管理员。')


def rank_candidates(user, options):
    algorithm = configured_algorithm()
    context = user_context(user)
    context['algorithm_version'] = algorithm
    if context['mode'] == 'cold_start':
        return context, [], 'cold', []
    ranked, weights = rank_context(context, options, public_features(algorithm_version=algorithm), algorithm_version=algorithm)
    return context, ranked, snapshot_token(user.pk, context, options, ranked, algorithm), weights


def snapshot_token(user_id, context, options, ranked, algorithm):
    # Opaque, user-bound state token; never exposes ratings or user IDs themselves.
    state = {'user': user_id, 'algorithm': algorithm, 'features': FEATURE_VERSION,
             'context_rule': context.get('taxonomy_version'),
             'rerank_policy': ranking_v2.policy_contract() if algorithm == EXPERIMENTAL_ALGORITHM_VERSION else None,
             'profile': context['profile'],
             'taxonomy': TAXONOMY_VERSION,
             'extraction_rule': FEATURE_RULE_VERSION,
             'preferences': (context['preferences']['version'], context['preferences']['tags']),
             'seeds': [(seed['bangumi_id'], seed['score'], sorted(seed['tags']), seed['feature_sources'])
                       for seed in context['seeds']],
             'owned': sorted(context['owned'].items()),
             'query': {key: str(options[key]) for key in ('category', 'type', 'min_score', 'hide_recorded', 'page_size')},
             'items': [(item['anime'].bangumi_id, item['anime'].score, sorted(item['tags']), item['reason'],
                        item['anime'].cover_url, item['anime'].title_zh, item['anime'].title_jp,
                        item['anime'].status_type, item['anime'].season) for item in ranked]}
    token = hmac.new(settings.SECRET_KEY.encode(), json.dumps(state, ensure_ascii=False, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()
    return token
