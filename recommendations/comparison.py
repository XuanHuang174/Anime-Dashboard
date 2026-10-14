"""Two independent, read-only ablations on one explicitly authorized snapshot.

No all-user discovery, score edits, review queries, title/series inference, shared
private cache or external calls. Real reports require >=4 usable current seeds.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import math
from time import perf_counter

from django.conf import settings
from django.db import connection

from .engine import build_feature_index, rank_context, snapshot_token
from .extraction import extract_profile
from .features import feature_tags
from .quality_evaluation import (CATEGORIES, category_differences, evidence_errors,
                                 input_fingerprint, mean, top_metrics, without_seed)
from .taxonomy import LEGACY_TAXONOMY_VERSION, TAXONOMY_VERSION, genre_rules
from .version_control import GATE_NAMES, canonical_bytes, release_descriptor

ARMS = {'legacy_v1': (LEGACY_TAXONOMY_VERSION, 'content.v1'),
        'current_v1': (TAXONOMY_VERSION, 'content.v1'), 'current_v2': (TAXONOMY_VERSION, 'content.v2')}
DEFAULT_LIMITS = {'maximum_relevance_drop': 0.0, 'maximum_holdout_drop': 0.0,
                  'minimum_repeat_improvement': .001, 'maximum_compute_ms': 5000,
                  'maximum_sql_queries': 6, 'minimum_novel_directions': 2}


def context_snapshot(records, preference, owner_id, rule):
    extracted, seeds = extract_profile(records, threshold=Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD)),
                                      minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES, taxonomy_version=rule)
    available, _ = genre_rules(rule)
    tags = [tag for tag in preference['tags'] if tag in available]
    priority = {'completed': 4, 'watching': 3, 'plan': 2, 'on_hold': 1}
    owned = {}
    for row in records:
        identity = row['resolved_bangumi_id']
        if identity and priority.get(row['watch_status'], 0) > priority.get(owned.get(identity), 0):
            owned[identity] = row['watch_status']
    return {'_owner_id': owner_id, 'taxonomy_version': rule, 'profile': extracted,
            'preferences': {'version': preference['version'], 'tags': tags}, 'seeds': seeds, 'owned': owned,
            'mode': 'personalized' if not extracted['cold_start'] else 'preference_based' if tags else 'cold_start'}


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)  # Never retain SQL or parameters.


def coverage(rows, rule):
    counts = Counter(len(feature_tags(row['tags'], (row['title_zh'], row['title_jp']), taxonomy_version=rule)) for row in rows)
    covered = len(rows) - counts[0]
    return {'rule': rule, 'works': len(rows), 'with_genres': covered,
            'coverage_fraction': covered / len(rows) if rows else None,
            'genre_size_histogram': {str(key): value for key, value in sorted(counts.items())}}


def measured_rank(context, options, index, algorithm):
    clone, counter = deepcopy(context), QueryCounter()
    started = perf_counter()
    with connection.execute_wrapper(counter):
        ranked, _ = rank_context(clone, options, index, algorithm_version=algorithm)
    elapsed = (perf_counter() - started) * 1000
    trace = clone.get('_ranking_trace', dict.fromkeys(('eligible_candidates', 'candidate_pool', 'validated_candidates'), 0))
    return ranked, {'compute_ms': round(elapsed, 3), 'sql_queries': counter.count, **trace}


def comparable_metrics(ranked, context, reference, current_features, top_k):
    # Old/new rule results judged in ONE fixed current vocabulary, so a changed
    # metric definition cannot be mistaken for algorithm improvement.
    evaluated = [{**row, 'tags': current_features[row['anime'].bangumi_id]} for row in ranked]
    metrics = top_metrics(evaluated, reference, top_k=top_k)
    errors = [error for row in ranked for error in evidence_errors(row, context)]
    metrics.update(evidence_error_count=len(errors), evidence_errors=sorted(set(errors)),
                   evidence_checked_rows=len(ranked), evidence_valid_fraction=1.0 if ranked and not errors else
                   0.0 if errors else None)
    reference_tags = set().union(*(seed['tags'] for seed in reference['seeds']), reference['preferences']['tags'])
    rows = evaluated[:top_k]
    directions = [row['tags'] - reference_tags for row in rows]
    metrics['novel_genre_ratio_mean'] = mean([len(novel) / len(row['tags']) if row['tags'] else 0
                                            for novel, row in zip(directions, rows)])
    metrics['novel_direction_count'] = len(set().union(*directions)) if rows else None
    metrics['associated_rows'] = sum(bool(row['tags'] & reference_tags) for row in rows)
    return metrics


def evaluate_snapshot(records, preference, owner_id, catalog, *, top_k=10, max_holdouts=4, repeats=2):
    contexts = {rule: context_snapshot(records, preference, owner_id, rule)
                for rule in (LEGACY_TAXONOMY_VERSION, TAXONOMY_VERSION)}
    reference = contexts[TAXONOMY_VERSION]
    required = max(4, settings.RECOMMENDATION_PROFILE_MIN_SAMPLES + 1)
    if not getattr(records, 'schema_available', True) or len(reference['seeds']) < required:
        raise ValueError('Selected authorized account lacks naturally usable minimum-plus-one samples; no rating repair allowed')
    indices = {rule: build_feature_index(catalog, taxonomy_version=rule) for rule in contexts}
    features = {identity: tags for identity, tags, _, _ in indices[TAXONOMY_VERSION]['features']}
    safe_ids = set(features)
    options = {'type': 'all', 'min_score': 0, 'hide_recorded': True, 'page_size': top_k}
    arms, outputs = {}, {}
    safety_errors = holdout_evidence_errors = holdout_evidence_checked_rows = 0
    for name, (rule, algorithm) in ARMS.items():
        context, index = contexts[rule], indices[rule]
        metrics, performance, results = {}, {}, {}
        for category in CATEGORIES:
            sequences, measurements = [], []
            for _ in range(repeats):
                ranked, measured = measured_rank(context, {**options, 'category': category}, index, algorithm)
                safety_errors += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in safe_ids
                                     or (row['evidence'].get('source') or {}).get('bangumi_id', next(iter(safe_ids), None)) not in safe_ids
                                     for row in ranked)
                sequences.append(hashlib.sha256(canonical_bytes([
                    (row['anime'].bangumi_id, row['evidence'], row['reason']) for row in ranked])).hexdigest())
                measurements.append(measured)
            results[category] = ranked
            metrics[category] = comparable_metrics(ranked, context, reference, features, top_k)
            performance[category] = {'runs': measurements, 'stable_order_and_evidence': len(set(sequences)) == 1,
                                      'maximum_compute_ms': max(row['compute_ms'] for row in measurements),
                                      'maximum_sql_queries': max(row['sql_queries'] for row in measurements)}
        outputs[name] = results
        arms[name] = {'algorithm': algorithm, 'rule': rule, 'metric_rule': TAXONOMY_VERSION,
                      'mode': context['mode'], 'usable_samples': len(context['seeds']),
                      'explicit_preference_count': len(context['preferences']['tags']),
                      'categories': metrics, 'performance': performance,
                      'category_differences': category_differences(results, top_k=top_k)}

    # Identical target IDs/training snapshots within each paired comparison.
    targets = sorted(seed['bangumi_id'] for seed in reference['seeds'])[:max_holdouts]
    passes = {}
    for pass_name, left, right in (('data_rules', 'legacy_v1', 'current_v1'), ('ranking', 'current_v1', 'current_v2')):
        pairs = {name: {category: [] for category in CATEGORIES} for name in (left, right)}
        fold_performance = {name: [] for name in (left, right)}
        skipped = 0
        for target in targets:
            clones = {name: without_seed(contexts[ARMS[name][0]], target,
                      minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES) for name in (left, right)}
            if any(clone is None or contexts[ARMS[name][0]]['mode'] != 'personalized' for name, clone in clones.items()):
                skipped += 1
                continue
            for name, clone in clones.items():
                rule, algorithm = ARMS[name]
                for category in CATEGORIES:
                    ranked, measurement = measured_rank(clone, {**options, 'category': category}, indices[rule], algorithm)
                    fold_performance[name].append(measurement)
                    safety_errors += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in safe_ids
                                         for row in ranked)
                    holdout_evidence_errors += sum(len(evidence_errors(row, clone)) for row in ranked)
                    holdout_evidence_checked_rows += len(ranked)
                    row_ids = [row['anime'].bangumi_id for row in ranked[:top_k]]
                    rank = row_ids.index(target) + 1 if target in row_ids else None
                    pairs[name][category].append({'hit_at_k': int(rank is not None), 'mrr_at_k': 1 / rank if rank else 0,
                        'binary_ndcg_at_k': 1 / math.log2(rank + 1) if rank else 0})
        holdout = {name: {category: {key: mean([row[key] for row in values]) for key in (
                      'hit_at_k', 'mrr_at_k', 'binary_ndcg_at_k')} for category, values in categories.items()}
                   for name, categories in pairs.items()}
        passes[pass_name] = {'left': left, 'right': right, 'paired_targets': len(targets) - skipped,
                             'skipped_unpaired_targets': skipped, 'holdout': holdout,
                             'fold_performance': fold_performance,
                             'explicit_preferences_retained': True}

    ctx, ranked = reference, outputs['current_v2']['all']
    token = snapshot_token(owner_id, ctx, {**options, 'category': 'all'}, ranked, 'content.v2')
    version_token = snapshot_token(owner_id, ctx, {**options, 'category': 'all'}, ranked, 'content.v1')
    other_owner_token = snapshot_token(-owner_id, ctx, {**options, 'category': 'all'}, ranked, 'content.v2')
    # Only hashes/counts leave this function; no source identity/title/rating/review.
    return {'sample_kind': 'explicitly_authorized_natural_account', 'input_fingerprint': input_fingerprint(reference),
            'usable_samples': len(reference['seeds']), 'arms': arms, 'passes': passes, 'safety_errors': safety_errors,
            'holdout_evidence_errors': holdout_evidence_errors,
            'holdout_evidence_checked_rows': holdout_evidence_checked_rows,
            'snapshot_isolation': token != version_token and token != other_owner_token,
            'changed_rule_sample_counts': {rule: len(ctx['seeds']) for rule, ctx in contexts.items()}}


def aggregate_arm(samples, arm, category, metric):
    return mean([sample['arms'][arm]['categories'][category][metric] for sample in samples])


def gate_report(samples, attestation, limits):
    gates = {key: {'status': 'insufficient_evidence', 'details': []} for key in GATE_NAMES}
    if not samples:
        return gates
    tested = bool(attestation and attestation.get('status') == 'pass')
    gates['safety']['status'] = 'pass' if tested and not sum(row['safety_errors'] for row in samples) else 'fail' if sum(row['safety_errors'] for row in samples) else 'insufficient_evidence'
    errors = sum(metrics['evidence_error_count'] for sample in samples for arm in sample['arms'].values()
                 for metrics in arm['categories'].values()) + sum(sample['holdout_evidence_errors'] for sample in samples)
    checked = sum(metrics['evidence_checked_rows'] for sample in samples for arm in sample['arms'].values() for metrics in arm['categories'].values())
    checked += sum(sample.get('holdout_evidence_checked_rows', 0) for sample in samples)
    gates['evidence'] = {'status': 'fail' if errors else 'pass' if tested and checked else 'insufficient_evidence',
                         'details': [{'checked_rows': checked, 'errors': errors}]}
    relevant = []
    for category in CATEGORIES:
        left = aggregate_arm(samples, 'current_v1', category, 'topic_overlap_proxy_mean')
        right = aggregate_arm(samples, 'current_v2', category, 'topic_overlap_proxy_mean')
        relevant.append(None if left is None or right is None else right - left >= -limits['maximum_relevance_drop'])
        gates['relevance']['details'].append({'category': category, 'v1': left, 'v2': right})
        for sample_index, sample in enumerate(samples, 1):
            paired = sample['passes']['ranking']
            for metric in ('hit_at_k', 'mrr_at_k', 'binary_ndcg_at_k'):
                a, b = (paired['holdout'][arm][category][metric] for arm in ('current_v1', 'current_v2'))
                relevant.append(None if a is None or b is None else b - a >= -limits['maximum_holdout_drop'])
                gates['relevance']['details'].append({'sample': sample_index,
                    'category': category, 'metric': metric, 'v1': a, 'v2': b})
    repeated = [aggregate_arm(samples, arm, 'all', 'pairwise_genre_jaccard_mean') for arm in ('current_v1', 'current_v2')]
    relevant.append(None if None in repeated else repeated[0] - repeated[1] >= limits['minimum_repeat_improvement'])
    gates['relevance']['details'].append({'discovery_repetition_v1_v2': repeated})
    gates['relevance']['status'] = 'fail' if False in relevant else 'insufficient_evidence' if None in relevant else 'pass'
    differences = []
    for sample in samples:
        rows = sample['arms']['current_v2']['categories']
        familiar, discovery, explore = (rows[key] for key in ('familiar', 'all', 'explore'))
        metrics = [row[key] for row, key in ((familiar, 'pairwise_genre_jaccard_mean'), (discovery, 'pairwise_genre_jaccard_mean'),
                   (familiar, 'topic_overlap_proxy_mean'), (explore, 'topic_overlap_proxy_mean'), (explore, 'novel_direction_count'))]
        if None in metrics or explore['evaluated_top_count'] == 0:
            differences.append(None)
        else:
            differences.append(familiar['pairwise_genre_jaccard_mean'] >= discovery['pairwise_genre_jaccard_mean']
                and familiar['topic_overlap_proxy_mean'] >= explore['topic_overlap_proxy_mean']
                and explore['novel_direction_count'] >= limits['minimum_novel_directions']
                and explore['associated_rows'] == explore['evaluated_top_count']
                and explore['novel_genre_ratio_mean'] > 0)
    gates['categories']['status'] = 'fail' if False in differences else 'insufficient_evidence' if None in differences else 'pass'
    gates['categories']['details'] = [{'sample': index + 1, 'positioning_check': value} for index, value in enumerate(differences)]
    runs = [value for sample in samples for arm in sample['arms'].values() for value in arm['performance'].values()]
    performance_ok = all(run['stable_order_and_evidence'] and run['maximum_compute_ms'] <= limits['maximum_compute_ms']
                          and run['maximum_sql_queries'] <= limits['maximum_sql_queries']
                          and all(row['candidate_pool'] <= 400 for row in run['runs']) for run in runs)
    folds = [row for sample in samples for pair in sample['passes'].values()
             for rows in pair.get('fold_performance', {}).values() for row in rows]
    performance_ok = performance_ok and all(row['compute_ms'] <= limits['maximum_compute_ms']
        and row['sql_queries'] <= limits['maximum_sql_queries'] and row['candidate_pool'] <= 400 for row in folds)
    gates['performance']['status'] = 'pass' if performance_ok else 'fail'
    gates['performance']['details'] = [{'maximum_compute_ms': max([run['maximum_compute_ms'] for run in runs] +
                                                                 [row['compute_ms'] for row in folds]),
        'maximum_sql_queries': max([run['maximum_sql_queries'] for run in runs] + [row['sql_queries'] for row in folds])}]
    gates['rollback']['status'] = 'pass' if tested and all(row['snapshot_isolation'] for row in samples) else 'insufficient_evidence'
    return gates


def build_comparison_report(scope, catalog, samples, attestation, *, top_k, max_holdouts, repeats):
    limits = dict(DEFAULT_LIMITS)  # Fixed predeclared gate policy; not tuned after seeing results.
    gates = gate_report(samples, attestation, limits)
    return {'contract_version': 'recommendations.comparison.v1', 'release': release_descriptor(),
            'scope': scope, 'catalog_fingerprint': hashlib.sha256(canonical_bytes(catalog)).hexdigest(),
            'coverage': {rule: coverage(catalog, rule) for rule in (LEGACY_TAXONOMY_VERSION, TAXONOMY_VERSION)},
            'configuration': {'top_k': top_k, 'max_holdouts': max_holdouts, 'repeats': repeats, 'gates': limits},
            'samples': samples, 'gates': gates, 'eligible_for_manual_switch': all(row['status'] == 'pass' for row in gates.values()),
            'test_attestation_verified': bool(attestation),
            'privacy': {'read_only': True, 'no_review_reads': True, 'no_external_calls': True,
                        'explicit_account_selection_only': True, 'no_rating_repair': True},
            'limitations': ['Genre overlap is an internal proxy, not liking probability or user satisfaction.',
                'No rating-vote count; no rating-confidence correction.',
                'Bounded deterministic retrospective holdout is not unbiased temporal prediction.',
                'Explicit preferences retained in holdout may contain prior knowledge.',
                'Same accounts can become cold under legacy rules; unpaired folds are reported, never fabricated.',
                'Small authorized cohort cannot establish statistical significance or general online lift.',
                'Measured ranking excludes initial catalog/profile fetch and client/network latency.',
                'Passing gates permits an operator decision, never an automatic online switch.']}
