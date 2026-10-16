"""Read-only V1 baseline: proxies are not preference probabilities or online lift."""
from copy import deepcopy
from itertools import combinations
import hashlib
import hmac
import json
import math

from django.conf import settings

from .engine import rank_context, user_context
from .features import FEATURE_RULE_VERSION, FEATURE_SOURCES

CATEGORIES = ('all', 'familiar', 'explore')
PREFERENCE_PROBES = (('music_daily', ('音乐', '日常')), ('scifi_mystery', ('科幻', '悬疑')),
                     ('yuri_daily', ('百合', '日常')))


def jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else None


def mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 6) if values else None


def evidence_errors(item, context):
    """Independently check the actual candidate/seed intersection and source values."""
    evidence, tags = item.get('evidence'), item['tags']
    if not evidence or not item.get('reason'):
        return ['missing_evidence_or_reason']
    shared = evidence.get('shared_tags', [])
    if not shared or len(shared) != len(set(shared)) or not set(shared) <= tags:
        return ['invalid_shared_tags']
    if evidence['kind'] == 'explicit_preferences':
        if evidence.get('source') is not None or not set(shared) <= set(context['preferences']['tags']):
            return ['explicit_preference_not_confirmed']
        if not all(tag in item['reason'] for tag in shared[:2]):
            return ['explicit_reason_not_supported']
        return []
    if evidence['kind'] != 'rated_work' or context['mode'] != 'personalized':
        return ['unsupported_evidence_kind']
    source = evidence.get('source') or {}
    seed = next((seed for seed in context['seeds'] if seed['bangumi_id'] == source.get('bangumi_id')), None)
    if seed is None or source.get('title') != seed['title'] or source.get('personal_score') != f"{seed['score']:.1f}":
        return ['source_not_a_trusted_personal_sample']
    if not set(shared) <= seed['tags']:
        return ['source_tags_not_shared']
    if evidence.get('feature_rule_version') != FEATURE_RULE_VERSION or any(
        evidence.get('shared_tag_sources', {}).get(tag) != list(seed['feature_sources'].get(tag, ()))
        or not seed['feature_sources'].get(tag) or not set(seed['feature_sources'][tag]) <= set(FEATURE_SOURCES)
        for tag in shared):
        return ['source_provenance_not_supported']
    if seed['title'] not in item['reason'] or f"{seed['score']:.1f}" not in item['reason'] or not all(
            tag in item['reason'] for tag in shared[:2]):
        return ['rated_reason_not_supported']
    return []


def top_metrics(ranked, context, *, top_k):
    rows = ranked[:top_k]
    tags = [item['tags'] for item in rows]
    references = [seed['tags'] for seed in context['seeds']] if context['mode'] == 'personalized' else []
    chosen = frozenset(context['preferences']['tags'])
    if chosen:
        references.append(chosen)
    overlaps = [max((jaccard(value, ref) or 0 for ref in references), default=0) for value in tags]
    pairs = [jaccard(left, right) for left, right in combinations(tags, 2)]
    counts = {tag: sum(tag in value for value in tags) for tag in set().union(*tags)} if tags else {}
    errors = [error for row in rows for error in evidence_errors(row, context)]
    return {'result_count': len(ranked), 'evaluated_top_count': len(rows), 'top_k': top_k,
        'topic_overlap_proxy_mean': mean(overlaps), 'pairwise_genre_jaccard_mean': mean(pairs),
        'unique_genre_count': len(counts) if rows else None,
        'dominant_genre_share': round(max(counts.values()) / len(rows), 6) if counts else None,
        'duplicate_genre_set_ratio': round(1 - len(set(map(frozenset, tags))) / len(rows), 6) if rows else None,
        'evidence_error_count': len(errors), 'evidence_errors': sorted(set(errors)),
        'evidence_valid_fraction': mean([not evidence_errors(row, context) for row in rows]),
        # Only public candidate IDs; no source titles, personal scores or private record IDs.
        'top_bangumi_ids': [row['anime'].bangumi_id for row in rows]}


def category_differences(results, *, top_k):
    comparisons = {}
    for left, right in combinations(CATEGORIES, 2):
        a = [row['anime'].bangumi_id for row in results[left][:top_k]]
        b = [row['anime'].bangumi_id for row in results[right][:top_k]]
        comparisons[f'{left}_vs_{right}'] = {
            'status': 'comparable' if a and b else 'not_comparable_empty_result',
            'top_set_jaccard': jaccard(set(a), set(b)) if a and b else None,
            'identical_order': a == b if a and b else None}
    return comparisons


def without_seed(context, identity, *, minimum):
    clone = deepcopy({key: value for key, value in context.items() if key != 'explanation_seeds'})
    clone['seeds'] = [seed for seed in clone['seeds'] if seed['bangumi_id'] != identity]
    clone['owned'].pop(identity, None)  # Remove all aliases of this canonical ID from recorded exclusion.
    if len(clone['seeds']) < minimum:
        return None  # Never lower the threshold or fall back to preferences to inflate evaluation.
    clone['mode'] = 'personalized'
    return clone


def input_fingerprint(context):
    private = {'owner': context.get('_owner_id'), 'mode': context['mode'],
        'preferences': (context['preferences']['version'], context['preferences']['tags']),
        'seeds': [(seed['bangumi_id'], seed['score'], sorted(seed['tags']), seed['feature_sources']) for seed in context['seeds']],
        'owned': sorted(context['owned'].items()),
        'feedback': (context.get('feedback', {}).get('version', 0),
                     sorted(context.get('feedback', {}).get('hidden_ids', ())))}
    return hmac.new(settings.SECRET_KEY.encode(), b'quality-baseline.v1\0' + json.dumps(
        private, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()


def evaluate_user(user, index, *, top_k=10, max_holdouts=5):
    context = user_context(user)
    context['_owner_id'] = user.pk  # Internal only, not exported; fingerprint is keyed.
    options = {'type': 'all', 'min_score': 0, 'hide_recorded': True}
    results = {category: rank_context(context, {**options, 'category': category}, index)[0] for category in CATEGORIES}
    metrics = {category: top_metrics(rows, context, top_k=top_k) for category, rows in results.items()}
    fingerprint = input_fingerprint(context)
    minimum = settings.RECOMMENDATION_PROFILE_MIN_SAMPLES
    holdouts = {category: [] for category in CATEGORIES}
    selected = sorted(context['seeds'], key=lambda seed: seed['bangumi_id'])[:max_holdouts]
    data_blocked = 0
    features = {identity: tags for identity, tags, _, _ in index['features']}
    if context['mode'] != 'personalized' or len(context['seeds']) < minimum + 1:
        holdout_status = 'skipped_requires_minimum_plus_one_usable_samples'
    else:
        holdout_status = 'evaluated'
        for seed in selected:
            clone = without_seed(context, seed['bangumi_id'], minimum=minimum)
            data_blocked += int(not features.get(seed['bangumi_id']))
            for category in CATEGORIES:
                ranked = rank_context(clone, {**options, 'category': category}, index)[0]
                ids = [item['anime'].bangumi_id for item in ranked[:top_k]]
                position = ids.index(seed['bangumi_id']) + 1 if seed['bangumi_id'] in ids else None
                holdouts[category].append({'hit': int(position is not None),
                    'reciprocal_rank': 1 / position if position else 0,
                    'ndcg': 1 / math.log2(position + 1) if position else 0})
    # Report aggregates only, not the held-out IDs, ratings or reasons.
    return {'sample_kind': 'authorized_user', 'input_fingerprint': fingerprint, 'mode': context['mode'],
        'cold_code': context['profile']['cold_start_state']['code'], 'usable_samples': len(context['seeds']),
        'explicit_preference_count': len(context['preferences']['tags']),
        'categories': metrics, 'category_differences': category_differences(results, top_k=top_k),
        'holdout': {'status': holdout_status, 'selection': 'ascending_bangumi_id_bounded_not_random_or_temporal',
            'evaluated_targets': len(holdouts['all']), 'targets_missing_catalog_genres': data_blocked,
            'data_blocked_targets_stay_in_denominator': True,
            'explicit_preferences_retained': True,
            'categories': {category: {'hit_at_k': mean([value['hit'] for value in values]),
                'mrr_at_k': mean([value['reciprocal_rank'] for value in values]),
                'binary_ndcg_at_k': mean([value['ndcg'] for value in values])} for category, values in holdouts.items()}}}


def evaluate_probe(index, *, name, tags, top_k):
    """Known fixed genre queries against REAL catalog; not a user's measured taste."""
    context = {'mode': 'preference_based', 'seeds': [], 'owned': {},
               'preferences': {'version': 0, 'tags': list(tags)}}
    options = {'type': 'all', 'min_score': 0, 'hide_recorded': True}
    results = {category: rank_context(context, {**options, 'category': category}, index)[0] for category in CATEGORIES}
    return {'sample_kind': 'synthetic_preference_probe', 'probe_name': name, 'probe_tags': list(tags),
            'input_fingerprint': input_fingerprint(context), 'mode': 'preference_based', 'cold_code': None,
            'usable_samples': 0, 'explicit_preference_count': len(tags),
            'categories': {category: top_metrics(rows, context, top_k=top_k) for category, rows in results.items()},
            'category_differences': category_differences(results, top_k=top_k),
            'holdout': {'status': 'not_applicable_synthetic_probe', 'evaluated_targets': 0,
                        'metrics': None, 'interpretation': 'No human preference accuracy or personalized quality measured'}}
