"""Consumed-pair diagnostic, ONE preregistered trial. Never release approval."""
from copy import deepcopy
from statistics import mean
from time import perf_counter, process_time

from django.db import connection

from . import engine, recall_completion as candidate, recall_v2, unused_validation as validation
from .comparison import QueryCounter, comparable_metrics
from .discovery_development import run as baseline_run
from .execution import execute_v2
from .exploration_candidate import PROTECTED_VERSION
from .recall_diagnostics import output_digest

ARMS = ('current_v1', 'protected_v2', 'candidate_v2')
CATEGORIES = ('all', 'familiar', 'explore')


class CompletionObserver(validation.Observer):
    def __init__(self, targets, context, weights):
        super().__init__(targets)
        self.vector = engine.taste_vector(context, deterministic=True)
        self.weights, self.cuts = weights, []

    def audit_items(self, name, items):
        if name != 'eligible':
            return
        ordered = sorted(items, key=lambda row: (-row['value'], row['bangumi_id']))
        patterns = {row['tags'] for row in ordered}
        values = {tags: candidate.relatedness(tags, self.vector, self.weights) for tags in patterns}
        aggregate = sorted(ordered, key=lambda row: (-values[row['tags']], -row['value'], row['bangumi_id']))
        ranks = {row['bangumi_id']: number for number, row in enumerate(aggregate, 1)}
        lookup = {row['bangumi_id']: (number, row) for number, row in enumerate(ordered, 1)}
        floor = ordered[0]['value'] * recall_v2.CONTRACT['relative_reserve_score_floor'] if ordered else 0
        # Ordinals only; neither target IDs nor user-specific genres/scores leave.
        self.cuts = [{'ordinal': number, 'eligible_base_rank': lookup[t][0] if t in lookup else None,
            'below_reserve_floor': lookup[t][1]['value'] < floor if t in lookup else None,
            'aggregate_relatedness_rank': ranks.get(t)} for number, t in enumerate(self.targets, 1)]


def run(user, context, index, category, arm, observer=None, *, reader):
    if arm != 'candidate_v2':
        return baseline_run(user, context, index, category, arm, observer, public_reader=reader)
    counter = QueryCounter(); started, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(counter):
        rows, _, metadata = execute_v2(user, {'category': category}, index, context=context, observer=observer,
            exploration_policy_version=PROTECTED_VERSION, recall_completion_version=candidate.VERSION,
            public_reader=reader)
    return rows, {'compute_ms': round((perf_counter() - started) * 1000, 3),
        'process_cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_queries': counter.count,
        'sql_execute_ms': round(counter.seconds * 1000, 3),
        'candidate_pool': metadata['trace'].get('candidate_pool', 0)}


def evaluate(user, context, index, targets, reader):
    original = deepcopy(context); training = validation.joint_training(context, targets)
    features = {identity: tags for identity, tags, _, _ in index['features']}
    result = {'paired_targets': 4, 'training_samples': len(training['seeds']), 'joint_holdout': True,
        'arms': {}, 'holdout': {}, 'destinations': {}, 'cuts': {}, 'safety_errors': 0, 'evidence_errors': 0,
        'evidence_checked_rows': 0, 'strict_independent_validation': False}
    digests = {}
    for arm in ARMS:
        metrics, performance, result['holdout'][arm], result['destinations'][arm], result['cuts'][arm] = {}, {}, {}, {}, {}
        for category in CATEGORIES:
            observer = CompletionObserver(targets, training, index['weights'])
            first, timing1 = run(user, training, index, category, arm, reader=reader)
            rows, timing2 = run(user, training, index, category, arm, observer, reader=reader)
            digest = output_digest(rows); digests[arm, category] = digest
            if digest != output_digest(first) or timing1['sql_queries'] != timing2['sql_queries']:
                raise ValueError('Diagnostic observation changed order/scores/evidence/SQL')
            if arm == 'candidate_v2' and category != 'all' and digest != digests['protected_v2', category]:
                raise ValueError('Recall completion changed out-of-scope category')
            metrics[category] = comparable_metrics(rows, training, training, features, 10)
            metrics[category].pop('top_bangumi_ids', None)
            result['safety_errors'] += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in features for row in rows)
            result['evidence_errors'] += metrics[category]['evidence_error_count']
            result['evidence_checked_rows'] += len(rows)
            if any((row.get('evidence', {}).get('source') or {}).get('bangumi_id') in targets for row in rows):
                raise ValueError('Heldout source leaked into evidence')
            result['holdout'][arm][category] = validation.holdout_metrics(rows, targets)
            result['destinations'][arm][category] = observer.stages
            result['cuts'][arm][category] = observer.cuts
            performance[category] = {'runs': [timing1, timing2], 'stable_order_and_evidence': True,
                'maximum_compute_ms': max(timing1['compute_ms'], timing2['compute_ms']),
                'maximum_sql_queries': max(timing1['sql_queries'], timing2['sql_queries'])}
        result['arms'][arm] = {'categories': metrics, 'performance': performance}
    if context != original:
        raise ValueError('Read-only comparison mutated input context')
    return result


def summary(samples):
    if not samples:
        raise ValueError('No comparable samples')
    hits = {arm: {cat: round(sum(s['holdout'][arm][cat]['hit_at_k'] * 4 for s in samples))
                  for cat in CATEGORIES} for arm in ARMS}
    lost = {arm: sum(old is not None and old <= 10 and (new is None or new > 10)
        for s in samples for old, new in zip(s['destinations'][arm]['all']['returned']['positions'],
            s['destinations']['candidate_v2']['all']['returned']['positions'])) for arm in ARMS[:-1]}
    metrics = {}  # An empty metric is insufficient evidence, never a zero.
    for arm in ARMS:
        metrics[arm] = {}
        for key in ('topic_overlap_proxy_mean', 'pairwise_genre_jaccard_mean', 'duplicate_genre_set_ratio'):
            values = [s['arms'][arm]['categories']['all'][key] for s in samples]
            metrics[arm][key] = mean(values) if all(value is not None for value in values) else None
    comparable = all(value is not None for arm in ARMS for value in metrics[arm].values())
    gates = {'more_hits': hits['candidate_v2']['all'] > hits['protected_v2']['all'],
        'retain_all_old_hits': not any(lost.values()),
        'topic_proxy': comparable and metrics['candidate_v2']['topic_overlap_proxy_mean'] >= metrics['protected_v2']['topic_overlap_proxy_mean'] - 1e-9,
        'repetition': comparable and all(metrics['candidate_v2'][key] <= metrics['protected_v2'][key] + 1e-9
            for key in ('pairwise_genre_jaccard_mean', 'duplicate_genre_set_ratio')),
        'safety_and_evidence': not any(s['safety_errors'] or s['evidence_errors'] for s in samples)}
    return {'hits_top10': hits, 'lost_target_hits': lost, 'discovery_metrics': metrics, 'development_gates': gates,
        'decision': 'independent-validation-still-required' if all(gates.values()) else 'stop-current-v2-route-keep-v1',
        'quality_gain_demonstrated': all(gates.values()), 'serving_approval': False,
        'independent_validation': False, 'target_count': sum(s['paired_targets'] for s in samples),
        'deployment_performance': 'not_measured'}
