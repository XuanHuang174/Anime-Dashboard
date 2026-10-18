"""Preregistered unused-pair validation, NOT a new-user/temporal blind test.

No algorithm changes. All four positives are removed jointly, including cached
explanations and full-profile summaries. Explicit preferences/feedback stay as
real inputs and their possible prior knowledge is disclosed, not silently fixed.
"""
from copy import deepcopy
import hashlib
import hmac
import json
import math
from pathlib import Path

from django.conf import settings
from django.db import connection

from . import engine, browsing
from .comparison import QueryCounter, comparable_metrics, DEFAULT_LIMITS, gate_report
from .deployment_evaluation import cohort_entry
from .evidence import _token
from .execution import execute_v2
from .exploration_candidate import execute_protected_candidate, recipe_descriptor, PROTECTED_VERSION
from .quality_evaluation import input_fingerprint, CATEGORIES
from .recall_diagnostics import COHORT_CONTRACT, load_config, output_digest
from .version_control import canonical_bytes

CONTRACT = 'recommendations.unused-holdout.v1'
ARMS = ('current_v1', 'reference_v2', 'candidate_v2')


def configuration():
    declared = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2-unused-holdout.v1.json').read_text(encoding='utf-8'))
    expected = {'contract_version': CONTRACT, 'selection': 'unused-pair-hmac-order.v1',
        'targets_per_account': 4, 'minimum_training_samples': 3, 'conservative_legacy_prefix': 5,
        'exclude_historical_accounts': True, 'split': 'joint-target-removal', 'top_k': 10, 'repeats': 2,
        'maximum_accounts': 200, 'maximum_evaluation_accounts': 100, 'minimum_evaluation_accounts': 2,
        'minimum_total_targets': 8, 'maximum_wall_seconds': 1200, 'candidate_policy': PROTECTED_VERSION,
        'arms': list(ARMS), 'single_attempt': True, 'strict_unseen_user_blind_test': False,
        'zero_hits_is_success': False, 'serving_enabled': False}
    if declared != expected or settings.RECOMMENDATION_PROFILE_MIN_SAMPLES != 3:
        raise ValueError('Validation protocol changed; no silent target/threshold tuning')
    return declared


def private_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to((Path(settings.BASE_DIR) / '.tools').resolve()):
        raise ValueError('Validation inputs and reports must remain under private .tools')
    return path


def legacy_inventory():
    """Read configuration headers, not outcomes, from completed archived runs.

    Exclude the historical account entirely because old ascending targets may
    have shifted after journal changes. Apply max-five prefix conservatively
    to everyone as an additional exclusion. Unknown higher budgets stop us.
    """
    reports = []
    for folder, filename, key in (('recommendation-quality', 'baseline.json', 'max_holdouts_per_user'),
            ('recommendation-comparison', 'comparison.json', 'max_holdouts'),
            ('v2-diagnostics', 'diagnostic.json', 'max_holdouts')):
        root = private_path(Path(settings.BASE_DIR) / '.tools' / folder)
        paths = sorted(root.glob('*/' + filename))
        if not paths or len(paths) > 64:
            raise ValueError('Missing/oversized legacy inventory; cannot claim unused targets')
        for path in paths:
            raw = private_path(path).read_bytes()
            manifest = json.loads(private_path(path.parent / 'manifest.json').read_text(encoding='utf-8'))
            if (len(raw) > 10 * 1024 * 1024 or manifest.get('complete') is not True
                    or manifest.get('sha256', {}).get(filename) != hashlib.sha256(raw).hexdigest()):
                raise ValueError('Incomplete/corrupt legacy evaluation archive')
            limit = json.loads(raw)['configuration'][key]
            if type(limit) is not int or not 1 <= limit <= 5:
                raise ValueError('Unaccounted legacy target budget; stop before selection')
            reports.append({'source': str(path.relative_to(Path(settings.BASE_DIR))),
                            'sha256': hashlib.sha256(raw).hexdigest(), 'maximum_prefix': limit})
    return {'reports': reports, 'conservative_prefix': 5,
            'unknown_unarchived_runs_cannot_be_proven_absent': True}


def read_history(path):
    raw = private_path(path).read_bytes()
    if len(raw) > 65536:
        raise ValueError('History lock too large')
    value = json.loads(raw)
    body = {k: v for k, v in value.items() if k != 'signature'}
    _, config_sha = load_config(Path(settings.BASE_DIR) / 'evaluation/v2a-recall.v1.json')
    if (value.get('contract_version') != COHORT_CONTRACT or value.get('config_sha256') != config_sha
            or value.get('ranking_performed') is not False or not value.get('samples')
            or not hmac.compare_digest(value.get('signature', ''), _token(COHORT_CONTRACT, body))):
        raise ValueError('Original historical lock cannot be authenticated')
    return value


def pair_handle(owner, identity):
    return _token(CONTRACT + '.pair', [owner, identity])


def attempt_path(cohort):
    # Target-set identity, not filename, timestamp, recipe or source fingerprint:
    # copying/re-signing the same pairs cannot silently restore their blindness.
    targets = sorted(token for entry in cohort['entries'] for token in entry['targets'])
    identity = hashlib.sha256(canonical_bytes({'protocol': CONTRACT, 'targets': targets})).hexdigest()
    return private_path(Path(settings.BASE_DIR) / '.tools' / 'unused-holdout-attempts' / (identity + '.json'))


def select(context, site_lock, history_lock):
    config = configuration()
    owner = context['_owner_id']
    identities = sorted(seed['bangumi_id'] for seed in context['seeds'])
    if len(identities) != len(set(identities)):
        raise ValueError('Canonical seed identities are not unique')
    historical = {token for sample in history_lock['samples'] for token in sample['sets']['expanded']}
    legacy_account = any(_token(COHORT_CONTRACT + '.target', [owner, identity]) in historical for identity in identities)
    used = {token for entry in site_lock['entries'] for token in entry['targets']}
    excluded = {identity for identity in identities if _token('recommendations.deployment.target.v1', [owner, identity]) in used
                or _token(COHORT_CONTRACT + '.target', [owner, identity]) in historical}
    excluded.update(identities[:config['conservative_legacy_prefix']])
    unused = [identity for identity in identities if identity not in excluded]
    if legacy_account:
        status = 'legacy_account_excluded'
    elif context['mode'] != 'personalized' or len(identities) - config['targets_per_account'] < config['minimum_training_samples']:
        status = 'insufficient_natural_training'
    elif len(unused) < config['targets_per_account']:
        status = 'insufficient_unused_targets'
    else:
        status = 'selected'
    targets = sorted(unused, key=lambda identity: _token(config['selection'], [owner, identity]))[:4] if status == 'selected' else []
    return targets, {'account': _token('recommendations.deployment.account.v1', owner),
        'input': input_fingerprint(context), 'status': status, 'natural_samples': len(identities),
        'excluded_pair_count': len(excluded), 'unused_pair_count': len(unused),
        'training_samples': len(identities) - len(targets), 'targets': [pair_handle(owner, identity) for identity in targets]}


def joint_training(context, targets):
    if len(targets) != 4 or len(set(targets)) != 4 or not set(targets) <= {s['bangumi_id'] for s in context['seeds']}:
        raise ValueError('Exactly four distinct frozen positives required')
    clone = deepcopy({key: context[key] for key in ('_owner_id', 'taxonomy_version', 'preferences', 'feedback')})
    clone.update(mode='personalized', seeds=deepcopy([s for s in context['seeds'] if s['bangumi_id'] not in targets]),
                 owned={key: value for key, value in context['owned'].items() if key not in targets})
    if len(clone['seeds']) < configuration()['minimum_training_samples']:
        raise ValueError('Joint holdout lacks natural training samples')
    # Deliberately do not copy profile/extraction summaries, explanation_seeds,
    # diagnostics, or any caller cache containing the target labels/features.
    return clone


class Observer:
    def __init__(self, targets):
        self.targets, self.stages = tuple(targets), {}

    def stage(self, name, identities):
        if name in self.stages:
            raise ValueError('Repeated validation stage')
        ids = list(identities)
        self.stages[name] = {'count': len(ids), 'positions': [ids.index(t) + 1 if t in ids else None for t in self.targets]}


def run_arm(user, context, index, category, arm, observer=None):
    from .v2_controls import validate_options
    from time import perf_counter, process_time
    from rest_framework.exceptions import PermissionDenied
    if not user.is_authenticated or context.get('_owner_id') != user.pk:
        raise PermissionDenied('Validation context must belong to the current sample')
    opts = validate_options({'category': category})
    counter, prepared = QueryCounter(), deepcopy(context)
    started, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(counter):
        if arm == 'current_v1':
            rows, _ = engine.rank_context(prepared, opts, index, pipeline_observer=observer)
            trace = prepared.get('_ranking_trace', {})
        elif arm in ('reference_v2', 'candidate_v2'):
            executor = execute_v2 if arm == 'reference_v2' else execute_protected_candidate
            rows, _, metadata = executor(user, opts, index, context=prepared, observer=observer)
            trace = metadata['trace']
        else:
            raise ValueError('Unsupported validation arm')
    return rows, {'compute_ms': round((perf_counter() - started) * 1000, 3),
        'process_cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_queries': counter.count,
        'sql_execute_ms': round(counter.seconds * 1000, 3), 'candidate_pool': trace.get('candidate_pool', 0)}


def holdout_metrics(rows, targets):
    ids = [r['anime'].bangumi_id for r in rows[:10]]
    ranks = [ids.index(t) + 1 if t in ids else None for t in targets]
    return {'hit_at_k': sum(r is not None for r in ranks) / len(targets),
        'mrr_at_k': sum(1 / r if r else 0 for r in ranks) / len(targets),
        'binary_ndcg_at_k': sum(1 / math.log2(r + 1) if r else 0 for r in ranks) / len(targets)}


def evaluate(user, context, index, targets):
    training = joint_training(context, targets)
    original = deepcopy(context)
    features = {identity: tags for identity, tags, _, _ in index['features']}
    arms, holdout, outputs, destinations = {}, {}, {}, {}
    safe_errors = evidence_errors = evidence_rows = 0
    for arm in ARMS:
        metrics, performance, holdout[arm], destinations[arm] = {}, {}, {}, {}
        for category in CATEGORIES:
            observer = Observer(targets)
            first, timing1 = run_arm(user, training, index, category, arm)
            rows, timing2 = run_arm(user, training, index, category, arm, observer)
            if output_digest(first) != output_digest(rows) or timing1['sql_queries'] != timing2['sql_queries']:
                raise ValueError('Repeated/observed validation changed output or SQL')
            outputs[arm, category] = output_digest(rows)
            if arm == 'candidate_v2' and category in ('all', 'familiar') and outputs[arm, category] != outputs['reference_v2', category]:
                raise ValueError('Candidate changed an out-of-scope category')
            metrics[category] = comparable_metrics(rows, training, training, features, 10)
            metrics[category].pop('top_bangumi_ids', None)
            evidence_errors += metrics[category]['evidence_error_count']; evidence_rows += len(rows)
            safe_errors += sum(r['anime'].nsfw is not False or r['anime'].bangumi_id not in features for r in rows)
            if any((r.get('evidence', {}).get('source') or {}).get('bangumi_id') in targets for r in rows):
                raise ValueError('Heldout source leaked into training evidence')
            holdout[arm][category] = holdout_metrics(rows, targets)
            destinations[arm][category] = observer.stages
            performance[category] = {'runs': [timing1, timing2], 'stable_order_and_evidence': True,
                'maximum_compute_ms': max(timing1['compute_ms'], timing2['compute_ms']),
                'maximum_sql_queries': max(timing1['sql_queries'], timing2['sql_queries'])}
        arms[arm] = {'categories': metrics, 'performance': performance}
    if context != original:
        raise ValueError('Validation mutated original context')
    return {'natural_samples': len(context['seeds']), 'training_samples': len(training['seeds']),
        'paired_targets': 4, 'joint_holdout': True, 'arms': arms, 'holdout': holdout, 'destinations': destinations,
        'safety_errors': safe_errors, 'evidence_errors': evidence_errors, 'evidence_checked_rows': evidence_rows,
        'target_identity_exported': False, 'explicit_preferences_retained': True}


def verdict(samples, tests_passed):
    projected = []
    for sample in samples:
        projected.append({'arms': {'current_v1': sample['arms']['current_v1'], 'current_v2': sample['arms']['candidate_v2'],
                                  'reference_v2': sample['arms']['reference_v2']},
            'safety_errors': sample['safety_errors'], 'holdout_evidence_errors': 0, 'holdout_evidence_checked_rows': 0,
            'snapshot_isolation': False,  # This command does not test served cursors; do not invent evidence.
            'passes': {'ranking': {'holdout': {'current_v1': sample['holdout']['current_v1'],
                                               'current_v2': sample['holdout']['candidate_v2']}}}})
    gates = gate_report(projected, {'status': 'pass' if tests_passed else 'unverified'}, DEFAULT_LIMITS)
    hits = {arm: {cat: sum(s['holdout'][arm][cat]['hit_at_k'] * 4 for s in samples) for cat in CATEGORIES} for arm in ARMS}
    reasons = []
    if len(samples) < 2 or sum(s['paired_targets'] for s in samples) < 8:
        reasons.append('too_few_eligible_accounts_or_targets')
    if sum(hits['current_v1'].values()) + sum(hits['candidate_v2'].values()) == 0:
        reasons.append('zero_holdout_hits_is_not_quality_success')
    status = ('fail' if any(gates[k]['status'] == 'fail' for k in ('safety', 'evidence', 'relevance', 'categories', 'performance'))
              else 'insufficient_evidence' if reasons or not tests_passed or any(
                  gates[k]['status'] != 'pass' for k in ('safety', 'evidence', 'relevance', 'categories', 'performance'))
              else 'pass_on_target_disjoint_supplement')
    return {'status': status, 'quality_gates': gates, 'hits': hits, 'insufficient_reasons': reasons,
        'strict_independent_blind_validation': False, 'serving_approval': False,
        'deployment_performance': 'not_measured', 'served_cursor_rollback': 'not_tested_here'}
