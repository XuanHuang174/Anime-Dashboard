"""D3 preregistered natural cohort; no identities/private content in reports."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter, process_time

from django.conf import settings
from django.db import connection

from . import engine
from .comparison import QueryCounter, comparable_metrics, gate_report, DEFAULT_LIMITS
from .execution import execute_v2, recipe_descriptor
from .quality_evaluation import CATEGORIES, mean, without_seed, input_fingerprint, category_differences
from .recall_diagnostics import output_digest
from .version_control import canonical_bytes

REQUIRED_CASES = ('test_dispatch_gate_and_rollback', 'test_old_approval_cannot_enable_new_recipe',
                  'test_receipt_chain_rejects_stale_or_changed_code', 'test_served_v2_http_and_details_are_bound')


def configuration():
    data = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2d-deployment.v1.json').read_text(encoding='utf-8'))
    if (data['quality_limits'] != DEFAULT_LIMITS or data['maximum_holdouts'] != 4
            or data['top_k'] != 10 or data['repeats'] != 2 or data['maximum_accounts'] != 200
            or data['maximum_eligible_accounts'] != 100 or data['minimum_evaluation_accounts'] != 2
            or data['minimum_total_top10_hits'] != 1
            or data['cohort'] != 'all-active-personalized-minimum-plus-one'
            or data['target_selection'] != 'ascending-bangumi-id-first-four-no-outcome-selection'
            or data['maximum_wall_seconds'] != 1200
            or data['service_limits'] != {'maximum_wall_ms': 8000, 'maximum_sql_queries': 32}
            or data['performance_scope'] != 'render-service-process-prepared-adapter-not-http-latency'):
        raise ValueError('Deployment preregistration changed; no silent gate tuning')
    return data


def validate_tests(value):
    if (value.get('status') != 'pass' or value.get('database') != 'local_isolated_postgresql'
            or value.get('skipped') != 0 or value.get('backend_tests', 0) < 347
            or value.get('frontend_tests', 0) < 158 or value.get('browser_checks', 0) < 13
            or value.get('build') != 'pass' or value.get('locks') != [6, 4, 6]
            or value.get('required_cases') != list(REQUIRED_CASES)):
        raise ValueError('Complete PostgreSQL/frontend/browser attestation required')


def cohort_entry(user, context):
    from .evidence import _token
    return {'account': _token('recommendations.deployment.account.v1', user.pk),
            'input': input_fingerprint(context),
            'targets': [_token('recommendations.deployment.target.v1', [user.pk, value])
                        for value in sorted(seed['bangumi_id'] for seed in context['seeds'])[:4]]}


def measure(user, context, index, options, algorithm):
    clone, queries = deepcopy(context), QueryCounter()
    wall, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(queries):
        if algorithm == 'current_v2':
            rows, _, metadata = execute_v2(user, options, index, context=clone)
            clone['_ranking_trace'] = metadata['trace']
        else:
            rows, _ = engine.rank_context(clone, options, index)
    trace = clone.get('_ranking_trace', {})
    return rows, {'compute_ms': round((perf_counter() - wall) * 1000, 3),
        'process_cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_queries': queries.count,
        'sql_execute_ms': round(queries.seconds * 1000, 3), 'candidate_pool': trace.get('candidate_pool', 0)}


def evaluate_sample(user, context, index, *, progress=lambda message: None):
    from .v2_controls import validate_options
    features = {identity: tags for identity, tags, _, _ in index['features']}
    safe = set(features)
    options = validate_options({})
    arms, baseline = {}, {}
    safety_errors = evidence_errors = evidence_rows = 0
    for arm in ('current_v1', 'current_v2'):
        metrics, performance, output = {}, {}, {}
        for category in CATEGORIES:
            runs, digests = [], []
            for _ in range(2):
                rows, timing = measure(user, context, index, {**options, 'category': category}, arm)
                runs.append(timing); digests.append(output_digest(rows))
            safety_errors += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in safe for row in rows)
            output[category] = rows
            metrics[category] = comparable_metrics(rows, context, context, features, 10)
            metrics[category].pop('top_bangumi_ids', None)
            performance[category] = {'runs': runs, 'stable_order_and_evidence': len(set(digests)) == 1,
                'maximum_compute_ms': max(r['compute_ms'] for r in runs), 'maximum_sql_queries': max(r['sql_queries'] for r in runs)}
        baseline[arm] = output
        arms[arm] = {'categories': metrics, 'performance': performance,
                     'category_differences': category_differences(output, top_k=10)}
    holdout = {arm: {category: [] for category in CATEGORIES} for arm in arms}
    fold_performance = {arm: [] for arm in arms}
    targets = sorted(seed['bangumi_id'] for seed in context['seeds'])[:4]
    for number, target in enumerate(targets, 1):
        clone = without_seed(context, target, minimum=settings.RECOMMENDATION_PROFILE_MIN_SAMPLES)
        if clone is None:
            raise ValueError('Frozen natural target lost minimum training samples')
        for arm in arms:
            for category in CATEGORIES:
                rows, timing = measure(user, clone, index, {**options, 'category': category}, arm)
                fold_performance[arm].append(timing)
                metrics = comparable_metrics(rows, clone, clone, features, 10)
                evidence_errors += metrics['evidence_error_count']; evidence_rows += len(rows)
                safety_errors += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in safe for row in rows)
                ids = [row['anime'].bangumi_id for row in rows[:10]]
                rank = ids.index(target) + 1 if target in ids else None
                holdout[arm][category].append({'hit_at_k': int(rank is not None), 'mrr_at_k': 1 / rank if rank else 0.,
                                             'binary_ndcg_at_k': 1 / math.log2(rank + 1) if rank else 0.})
        progress(f'fixed target {number}/{len(targets)} (no identity exported)')
    tokens = [engine.snapshot_token(owner, context, options, baseline['current_v2']['all'], algorithm, weights=index['weights'])
              for owner, algorithm in ((user.pk, 'content.v2'), (user.pk, 'content.v1'), (-user.pk, 'content.v2'))]
    return {'sample_kind': 'site_owner_authorized_natural_account', 'usable_samples': len(context['seeds']),
        'arms': arms, 'safety_errors': safety_errors, 'holdout_evidence_errors': evidence_errors,
        'holdout_evidence_checked_rows': evidence_rows, 'snapshot_isolation': len(set(tokens)) == 3,
        'passes': {'ranking': {'paired_targets': len(targets), 'skipped_unpaired_targets': 0,
            'holdout': {arm: {category: {key: mean([r[key] for r in values]) for key in
                       ('hit_at_k', 'mrr_at_k', 'binary_ndcg_at_k')} for category, values in categories.items()}
                        for arm, categories in holdout.items()}, 'fold_performance': fold_performance}}}


def deployment_gates(evaluation, tests, performance=None):
    config = configuration()
    validate_tests(tests)
    if evaluation.get('recipe') != recipe_descriptor() or evaluation.get('configuration') != config:
        raise ValueError('Evaluation recipe/configuration mismatch')
    samples = evaluation.get('samples', [])
    gates = gate_report(samples, {'status': 'pass'}, config['quality_limits'])
    if len(samples) < config['minimum_evaluation_accounts'] or not evaluation.get('frozen_before_ranking'):
        gates['relevance']['status'] = 'insufficient_evidence'
    if any(row.get('sample_kind') != 'site_owner_authorized_natural_account' or type(row.get('usable_samples')) is not int
           or row['usable_samples'] < 4 or row['passes']['ranking'].get('paired_targets') != 4
           or row['passes']['ranking'].get('skipped_unpaired_targets') != 0 for row in samples):
        gates['relevance']['status'] = 'insufficient_evidence'
    total_hits = sum(row['passes']['ranking']['holdout'][arm]['all']['hit_at_k'] or 0 for row in samples
                     for arm in ('current_v1', 'current_v2'))
    if total_hits <= 0 and gates['relevance']['status'] == 'pass':
        gates['relevance']['status'] = 'insufficient_evidence'
        gates['relevance']['details'].append({'missing': 'both arms have zero discovery holdout hits; not proof of quality'})
    # A local Neon client benchmark is NOT deployed-process or HTTP evidence.
    valid_performance = bool(performance and performance.get('execution_context') == config['performance_scope']
        and performance.get('recipe') == recipe_descriptor() and performance.get('configuration') == config
        and performance.get('service_identity_hash') and performance.get('git_commit')
        and performance.get('cases') and performance.get('sample_count', 0) >= 2)
    if not valid_performance:
        if gates['performance']['status'] != 'fail':
            gates['performance']['status'] = 'insufficient_evidence'
        gates['performance']['details'].append({'missing': 'signed deployed service process performance; local data not substituted'})
    elif any(row['wall_ms'] > config['service_limits']['maximum_wall_ms'] or row['sql_count'] >
             config['service_limits']['maximum_sql_queries'] for row in performance['cases']):
        gates['performance']['status'] = 'fail'
    if evaluation.get('historical_baseline_checked') is not True:
        gates['relevance']['status'] = 'insufficient_evidence'
    return gates
