"""Bounded, deterministic content ranking. Only public features are cached."""
import hashlib
import hmac
import json
import math
import threading
from time import perf_counter
from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from rest_framework.exceptions import APIException

from .identity import safe_catalog, normalized_title
from .services import profile_data
from .taxonomy import TAXONOMY_VERSION, genre_rules
from .features import feature_tags, FEATURE_RULE_VERSION
from .public_fingerprint import public_feature_fingerprint, public_feature_expression
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
    features, frequency, fingerprints = [], Counter(), {}
    for row in rows:
        tags = feature_tags(row['tags'], (row['title_zh'], row['title_jp']), taxonomy_version=taxonomy_version)
        frequency.update(tags)
        features.append((row['bangumi_id'], tags, row['score'], row['status_type']))
        fingerprints[row['bangumi_id']] = public_feature_fingerprint(row)
        if len(features) > MAX_CATALOG_ROWS:
            raise RecommendationUnavailable()
    weights = {tag: min(3.0, 1 + math.log((1 + len(features)) / (1 + count)))
               for tag, count in frequency.items()}
    return {'signature': signature, 'features': features, 'weights': weights,
            'source_fingerprints': fingerprints,
            'taxonomy_version': taxonomy_version or vocabulary.TAXONOMY_VERSION}


def public_features(*, algorithm_version=ALGORITHM_VERSION):
    from .version_control import cache_namespace
    key = cache_namespace(algorithm_version, TAXONOMY_VERSION, FEATURE_VERSION, FEATURE_RULE_VERSION)
    revision = safe_catalog().aggregate(count=Count('pk'), updated=Max('updated_at'))
    signature = (revision['count'], str(revision['updated']), algorithm_version,
                 ranking_v2.POLICY_VERSION if algorithm_version == EXPERIMENTAL_ALGORITHM_VERSION else 'frozen',
                 TAXONOMY_VERSION, FEATURE_VERSION, FEATURE_RULE_VERSION)
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
    from .feedback import feedback_snapshot
    feedback = feedback_snapshot(user)
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
    return {'_owner_id': user.pk, 'profile': profile, 'preferences': preferences, 'seeds': seeds, 'taxonomy_version': vocabulary.TAXONOMY_VERSION,
            'mode': mode, 'owned': owned, 'feedback': feedback}


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


def _diagnostic_target_trace(context, *, eligible=None, pool=None, validated=None):
    """Offline target ordinal only; never adds work IDs or scores to responses.

    Normal user_context never supplies this marker. Observes existing decisions;
    it cannot insert a target, change ties or enlarge a candidate pool.
    """
    target = context.get('_diagnostic_target_id')
    if target is None:
        return
    trace = context['_ranking_trace'].setdefault('target', {})
    for label, identities in (('eligible_rank', eligible), ('pool_rank', pool), ('validated_position', validated)):
        if identities is not None:
            trace[label] = next((position for position, identity in enumerate(identities, 1) if identity == target), None)


def _rank_context_v1(context, options, index, *, observer=None):
    """Same V1 ranking, supplied request-local context/index; not an API input."""
    if context['mode'] == 'cold_start':
        return [], []
    weights, vector = index['weights'], taste_vector(context)
    seed_ids = {seed['bangumi_id'] for seed in context['seeds']}
    hidden_ids = context.get('feedback', {}).get('hidden_ids', ())
    shortlist = []
    for identity, tags, score, status_type in index['features']:
        if identity in hidden_ids or identity in seed_ids or (options['hide_recorded'] and identity in context['owned']):
            continue
        if score < float(options['min_score']) or (options['type'] != 'all' and status_type != options['type']):
            continue
        affinity = evidence_for(tags, context, weights)[2] if options['category'] == 'familiar' else 0
        value = candidate_score(tags, score, vector, weights, options['category'], affinity)
        if value is not None:
            shortlist.append((value, identity))
    shortlist.sort(key=lambda pair: (-pair[0], pair[1]))
    pool_ids = [identity for _, identity in shortlist[:CANDIDATE_POOL]]
    if observer is not None:
        observer.stage('eligible', (identity for _, identity in shortlist))
        observer.stage('pool', pool_ids)
    context['_ranking_trace'] = {'eligible_candidates': len(shortlist), 'candidate_pool': len(pool_ids)}
    _diagnostic_target_trace(context, eligible=(identity for _, identity in shortlist), pool=pool_ids)
    # Fetch CURRENT safe rows before evidence, ranking, pagination and serialization.
    query = safe_catalog().filter(bangumi_id__in=pool_ids, score__gte=options['min_score'])
    if options['type'] != 'all':
        query = query.filter(status_type=options['type'])
    rows = list(query.order_by('bangumi_id').defer('synopsis', 'source_cover_url'))
    if observer is not None:
        observer.stage('hydrated', (anime.bangumi_id for anime in rows))
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
    if observer is not None:
        observer.stage('validated', (item['anime'].bangumi_id for item in ranked))
    context['_ranking_trace']['validated_candidates'] = len(ranked)
    _diagnostic_target_trace(context, validated=(item['anime'].bangumi_id for item in ranked))
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
    if observer is not None:
        observer.stage('returned', (item['anime'].bangumi_id for item in ranked))
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


class V2RequestScorer:
    """Memoize equivalent PUBLIC feature patterns within ONE private request.

    No cross-user cache or stale row-ID memo. A fresh row supplies raw tags and
    title collisions every time, including the final safety/hydration query.
    """
    def __init__(self, context, options, weights, vector, taxonomy_version, *, policy_version=None):
        self.policy_version = policy_version or ranking_v2.POLICY_VERSION
        if self.policy_version not in (ranking_v2.POLICY_VERSION, ranking_v2.PREVIOUS_POLICY_VERSION):
            raise RecommendationUnavailable('不支持的 V2 评测策略。')
        self.preference_discovery = (context['mode'] == 'preference_based' and options['category'] == 'all'
                                     and self.policy_version == ranking_v2.POLICY_VERSION)
        self.context, self.options, self.weights, self.vector = context, options, weights, vector
        self.taxonomy_version = taxonomy_version
        available, aliases = genre_rules(taxonomy_version)
        self.genre_names = {normalized_title(value) for value in (*available, *aliases)}
        self.tags_cache, self.patterns = {}, {}
        self.vector_norm = math.sqrt(sum((weights.get(tag, 1) * vector[tag]) ** 2 for tag in sorted(vector)))
        references = [seed['tags'] for seed in context['seeds']] if context['mode'] == 'personalized' else []
        if context['preferences']['tags']:
            references.append(frozenset(context['preferences']['tags']))
        self.references = tuple(set(references))  # Max over sets; no order-dependent arithmetic.
        anchor_weights = Counter()
        for seed in context['seeds'] if context['mode'] == 'personalized' else ():
            anchor_weights[seed['tags']] += max(.1, seed['score'] - float(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD) + 1)
        self.familiar_anchor = max(anchor_weights, key=lambda tags: (anchor_weights[tags], tuple(sorted(tags)))) if anchor_weights else (
            frozenset(context['preferences']['tags']))

    def tags_for(self, row):
        # An unknown title cannot collide with an accepted canonical tag/alias.
        # Keep ALL recognized collisions, including raw alias vs canonical title;
        # the existing extractor remains the authority, including duplicate aliases.
        names = tuple(sorted({normalized_title(row[key]) for key in ('title_zh', 'title_jp')} & self.genre_names))
        key = (row['tags'], names)
        if key in self.tags_cache:
            return self.tags_cache[key]
        tags = feature_tags(row['tags'], names, taxonomy_version=self.taxonomy_version)
        if len(self.tags_cache) < ranking_v2.REQUEST_MEMO_LIMIT:
            self.tags_cache[key] = tags
        return tags

    def score(self, tags, public_rating):
        if tags in self.patterns:
            pattern = self.patterns[tags]
        else:
            evidence, reason, affinity = evidence_for(tags, self.context, self.weights, deterministic=True)
            if not evidence or (self.options['category'] == 'familiar' and self.context['mode'] == 'personalized'
                                and evidence['kind'] != 'rated_work'):
                pattern = None
            else:
                ordered = sorted(tags)
                weight = lambda tag: self.weights.get(tag, 1)
                dot = sum(weight(tag) ** 2 * self.vector[tag] for tag in ordered if tag in self.vector)
                norm = math.sqrt(sum(weight(tag) ** 2 for tag in ordered))
                related = dot / (norm * self.vector_norm) if self.vector_norm else 0
                novelty = sum(weight(tag) for tag in sorted(tags - self.vector.keys())) / sum(weight(tag) for tag in ordered)
                nearest = max((len(tags & ref) / len(tags | ref) for ref in self.references), default=0)
                source_rating = float((evidence.get('source') or {}).get('personal_score', 0))
                pattern = (evidence, reason, dict(category=self.options['category'], nearest=nearest,
                    affinity=affinity, related=related, novelty=novelty, source_rating=source_rating,
                    anchor=ranking_v2.weighted_overlap(tags, self.familiar_anchor, self.weights),
                    preference_discovery=self.preference_discovery,
                    preference_coverage=(sum(weight(tag) for tag in sorted(tags & self.vector.keys()))
                        / sum(weight(tag) for tag in sorted(self.vector)) if self.preference_discovery and self.vector else 0)))
            if len(self.patterns) < ranking_v2.REQUEST_MEMO_LIMIT:
                self.patterns[tags] = pattern
        if pattern is None:
            return None
        evidence, reason, components = pattern
        value = ranking_v2.base_score(**components, public_rating=public_rating)
        return (evidence, reason, value) if value is not None and math.isfinite(value) else None


def _v2_item(row, context, options, weights, vector, seed_ids, scorer, *, verified_tags=None):
    identity = row['bangumi_id']
    if identity in context.get('feedback', {}).get('hidden_ids', ()) or identity in seed_ids or (
            options['hide_recorded'] and identity in context['owned']):
        return None
    tags = scorer.tags_for(row) if verified_tags is None else verified_tags
    if not tags & vector.keys():
        return None
    scored = scorer.score(tags, row['score'])
    if scored is None:
        return None
    evidence, reason, value = scored
    return {'bangumi_id': identity, 'tags': tags, 'value': value, 'evidence': evidence, 'reason': reason}


def _rank_context_v2(context, options, index, *, policy_version=None, observer=None, recall_policy_version=None):
    if context['mode'] == 'cold_start':
        return [], []
    weights, vector = index['weights'], taste_vector(context, deterministic=True)
    seed_ids = {seed['bangumi_id'] for seed in context['seeds']}
    scorer = V2RequestScorer(context, options, weights, vector, index.get('taxonomy_version'), policy_version=policy_version)
    # Cached tags are usable ONLY after an exact CURRENT SHA256 input match.
    # Safety and scores are live even when the feature fingerprint is unchanged.
    query = safe_catalog().filter(score__gte=options['min_score'])
    if options['type'] != 'all':
        query = query.filter(status_type=options['type'])
    fields = ('bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type')
    started = perf_counter()
    # Compact live revisions avoid retransmitting full titles/tags on every
    # request. Bounded client-side fetch, not per-row or server-cursor round trips.
    public_rows = list(query.annotate(feature_revision=public_feature_expression()).order_by('bangumi_id').values_list(
        'bangumi_id', 'score', 'feature_revision')[:MAX_CATALOG_ROWS + 1])
    fetched = perf_counter()
    if len(public_rows) > MAX_CATALOG_ROWS:
        raise RecommendationUnavailable()
    cached = {identity: tags for identity, tags, _, _ in index['features']}
    fingerprints = index.get('source_fingerprints', {})
    changed = [identity for identity, _, revision in public_rows
               if identity not in cached or fingerprints.get(identity) != revision]
    # Missing/old cache entries and direct QuerySet.update (which may not touch
    # updated_at) take this path. Never guess features or ignore new safe rows.
    fresh = {row['bangumi_id']: row for row in query.filter(bangumi_id__in=changed).values(*fields)} if changed else {}
    eligible = []
    for identity, score, revision in public_rows:
        if identity in fresh:
            item = _v2_item(fresh[identity], context, options, weights, vector, seed_ids, scorer)
        elif identity in cached and fingerprints.get(identity) == revision:
            item = _v2_item({'bangumi_id': identity, 'score': score}, context, options,
                            weights, vector, seed_ids, scorer, verified_tags=cached[identity])
        else:  # Changed row removed/reclassified between reads; fail closed.
            item = None
        if item:
            eligible.append(item)
    extracted = perf_counter()
    if recall_policy_version is not None:
        from . import recall_v2
        if recall_policy_version not in (recall_v2.VERSION, recall_v2.PREVIOUS_VERSION) or policy_version not in (None, ranking_v2.POLICY_VERSION):
            raise RecommendationUnavailable('不支持的离线召回策略。')
        pool = recall_v2.candidate_pool(eligible, vector, context, weights, limit=CANDIDATE_POOL,
                                      category=options['category'], policy_version=recall_policy_version)
    else:
        pool = ranking_v2.balanced_candidate_pool(eligible, vector, limit=CANDIDATE_POOL)
    if observer is not None:
        observer.stage('eligible', (item['bangumi_id'] for item in sorted(
            eligible, key=lambda item: (-item['value'], item['bangumi_id']))))
        observer.stage('pool', (item['bangumi_id'] for item in pool))
        if hasattr(observer, 'audit_items'):
            observer.audit_items('eligible', eligible)
            observer.audit_items('pool', pool)
    balanced = perf_counter()
    context['_ranking_trace'] = {'eligible_candidates': len(eligible), 'candidate_pool': len(pool)}
    if '_diagnostic_target_id' in context:
        _diagnostic_target_trace(context,
            eligible=(item['bangumi_id'] for item in sorted(eligible, key=lambda item: (-item['value'], item['bangumi_id']))),
            pool=(item['bangumi_id'] for item in pool))
    # Recheck immediately before ranking/serialization; a safety change during
    # pool construction can only remove rows, never bypass the backend filter.
    rows = query.filter(bangumi_id__in=[item['bangumi_id'] for item in pool]).order_by('bangumi_id').defer('synopsis', 'source_cover_url')
    if observer is not None:
        # Materialization is diagnostic-only; normal QuerySet iteration already
        # materializes the same bounded query. It does not add/filter any rows.
        rows = list(rows)
        observer.stage('hydrated', (anime.bangumi_id for anime in rows))
    current = []
    for anime in rows:
        item = _v2_item({field: getattr(anime, field) for field in fields}, context, options, weights, vector, seed_ids, scorer)
        if item:
            current.append({**item, 'anime': anime})
    # Source safety is just as important as candidate safety. A source changing
    # classification during a long pool scan must trigger a fresh profile read,
    # not leak its title/rating through an otherwise safe candidate's reason.
    if context['mode'] == 'personalized' and seed_ids != set(safe_catalog().filter(
            bangumi_id__in=seed_ids).values_list('bangumi_id', flat=True)):
        raise RecommendationSnapshotChanged()
    context['_ranking_trace']['validated_candidates'] = len(current)
    if observer is not None:
        observer.stage('validated', (item['anime'].bangumi_id for item in current))
    _diagnostic_target_trace(context, validated=(item['bangumi_id'] for item in current))
    context['_ranking_trace']['public_tag_patterns'] = len(scorer.tags_cache)
    context['_ranking_trace']['evidence_patterns'] = len(scorer.patterns)
    context['_ranking_trace']['changed_public_rows'] = len(changed)
    hydrated = perf_counter()
    ranked = ranking_v2.rerank_candidates(current, vector, weights, category=options['category'], limit=MAX_RESULTS)
    if observer is not None:
        observer.stage('returned', (item['anime'].bangumi_id for item in ranked))
    context['_ranking_trace']['stage_ms'] = dict(zip(
        ('public_fetch', 'feature_scoring', 'pool_balance', 'hydrate_and_safety', 'rerank'),
        (round((end - begin) * 1000, 3) for begin, end in zip(
            (started, fetched, extracted, balanced, hydrated),
            (fetched, extracted, balanced, hydrated, perf_counter())))))
    return ranked, weights


def rank_context(context, options, index, *, algorithm_version=ALGORITHM_VERSION, v2_policy_version=None,
                 pipeline_observer=None):
    """Explicit offline version; V1 baseline never follows a deployment flag."""
    genre_rules(index.get('taxonomy_version'))
    if context.get('taxonomy_version', vocabulary.TAXONOMY_VERSION) != index.get('taxonomy_version', vocabulary.TAXONOMY_VERSION):
        raise RecommendationUnavailable('画像与候选题材版本不一致。')
    if algorithm_version == ALGORITHM_VERSION:
        if v2_policy_version is not None:
            raise RecommendationUnavailable('V1 不接受 V2 策略覆盖。')
        return _rank_context_v1(context, options, index, observer=pipeline_observer)
    if algorithm_version == EXPERIMENTAL_ALGORITHM_VERSION:
        if v2_policy_version not in (None, ranking_v2.POLICY_VERSION, ranking_v2.PREVIOUS_POLICY_VERSION):
            raise RecommendationUnavailable('不支持的 V2 评测策略。')
        return _rank_context_v2(context, options, index, policy_version=v2_policy_version, observer=pipeline_observer)
    raise RecommendationUnavailable('推荐算法配置暂不可用，请联系管理员。')


def rank_recall_candidate(context, options, index, *, pipeline_observer=None, recall_policy_version=None):
    """Trusted offline V2-B entry ONLY; no setting/HTTP parameter selects this.

    The original rank_context/content.v2 path keeps its frozen pool. No private
    result cache, browse cursor or production evidence token is issued here.
    """
    from .recall_v2 import VERSION
    genre_rules(index.get('taxonomy_version'))
    if context.get('taxonomy_version', vocabulary.TAXONOMY_VERSION) != index.get('taxonomy_version', vocabulary.TAXONOMY_VERSION):
        raise RecommendationUnavailable('画像与候选题材版本不一致。')
    return _rank_context_v2(context, options, index, recall_policy_version=recall_policy_version or VERSION, observer=pipeline_observer)


def rank_candidates(user, options):
    algorithm = configured_algorithm()
    context = user_context(user)
    context['algorithm_version'] = algorithm
    if context['mode'] == 'cold_start':
        return context, [], 'cold', []
    ranked, weights = rank_context(context, options, public_features(algorithm_version=algorithm), algorithm_version=algorithm)
    return context, ranked, snapshot_token(user.pk, context, options, ranked, algorithm, weights=weights), weights


def snapshot_token(user_id, context, options, ranked, algorithm, *, weights=None):
    from .evidence import EVIDENCE_CONTRACT_VERSION, SELECTION_RULE_VERSION
    # Opaque, user-bound state token; never exposes ratings or user IDs themselves.
    state = {'user': user_id, 'algorithm': algorithm, 'features': FEATURE_VERSION,
             'evidence_contract': EVIDENCE_CONTRACT_VERSION, 'evidence_selection': SELECTION_RULE_VERSION,
             'evidence_catalog_version': hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()
                 if weights is not None else None,
             'context_rule': context.get('taxonomy_version'),
             'rerank_policy': ranking_v2.policy_contract() if algorithm == EXPERIMENTAL_ALGORITHM_VERSION else None,
             'profile': context['profile'],
             'taxonomy': TAXONOMY_VERSION,
             'extraction_rule': FEATURE_RULE_VERSION,
             'preferences': (context['preferences']['version'], context['preferences']['tags']),
             'feedback_version': context.get('feedback', {}).get('version', 0),
             'seeds': [(seed['bangumi_id'], seed['score'], sorted(seed['tags']), seed['feature_sources'])
                       for seed in context['seeds']],
             'owned': sorted(context['owned'].items()),
             'query': {key: str(options[key]) for key in ('category', 'type', 'min_score', 'hide_recorded', 'page_size')},
             'items': [(item['anime'].bangumi_id, item['anime'].score, sorted(item['tags']), item['reason'],
                        item['anime'].cover_url, item['anime'].title_zh, item['anime'].title_jp,
                        item['anime'].status_type, item['anime'].season) for item in ranked]}
    token = hmac.new(settings.SECRET_KEY.encode(), json.dumps(state, ensure_ascii=False, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()
    return token
