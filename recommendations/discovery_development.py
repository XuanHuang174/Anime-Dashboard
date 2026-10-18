"""Read-only development comparison; revealed targets never regain blindness."""
from copy import deepcopy
import hashlib
import hmac
import json
from time import perf_counter, process_time

from django.conf import settings
from django.db import connection

from . import engine, unused_validation as validation
from .comparison import QueryCounter, comparable_metrics
from .discovery_candidate import execute_candidate
from .recall_diagnostics import output_digest
from .version_control import ARTIFACT_VERSION, canonical_bytes
from .query_phases import QueryPhases

ARMS = ('current_v1', 'reference_v2', 'protected_v2', 'candidate_v2')
DUAL_ARMS = ('current_v1', 'reference_v2', 'protected_v2', 'aggregate_v2', 'candidate_v2')


def frozen_input_status(lock, entries, catalog_revision, active_account_count):
    """Explain a stop using counts only; do not export account handles/inputs."""
    previous = {entry['account']: entry for entry in lock['entries']}
    current = {entry['account']: entry for entry in entries}
    shared = previous.keys() & current.keys()
    return {'catalog_unchanged': lock['catalog_revision'] == catalog_revision,
        'expected_active_accounts': lock['active_account_count'], 'current_active_accounts': active_account_count,
        'expected_eligible_accounts': len(previous), 'current_eligible_accounts': len(current),
        'changed_original_inputs': sum(previous[key] != current[key] for key in shared),
        'missing_original_eligible_accounts': len(previous.keys() - current.keys()),
        'new_eligible_accounts': len(current.keys() - previous.keys())}


def read_archive(path, kind):
    """Authenticate old INPUT/history only, never treat old code as a release.

    Source fingerprints necessarily differ in a development experiment. Each
    live input is checked separately; no resigning/reselection of old targets.
    """
    raw = validation.private_path(path).read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Oversized development archive')
    data = json.loads(raw)
    body = {key: value for key, value in data.items() if key != 'signature'}
    signature = hmac.new(settings.SECRET_KEY.encode(), canonical_bytes(body), hashlib.sha256).hexdigest()
    if (data.get('kind') != kind or data.get('artifact_version') != ARTIFACT_VERSION
            or not hmac.compare_digest(signature, data.get('signature', ''))):
        raise ValueError('Cannot authenticate original revealed archive')
    return data


def consumed_inputs(lock_path, result_path):
    lock = read_archive(lock_path, 'unused-holdout-cohort')
    result = read_archive(result_path, 'unused-holdout-evaluation')
    if (lock.get('configuration') != validation.configuration() or lock.get('ranking_performed') is not False
            or result.get('configuration') != lock['configuration']
            or result.get('cohort_sha256') != hashlib.sha256(canonical_bytes(lock)).hexdigest()
            or result.get('release_fingerprint') != lock.get('release_fingerprint')
            or result.get('approval_issued') is not False
            or not validation.attempt_path(lock).exists()):
        raise ValueError('Completed consumed target set required; cannot relabel fresh targets as development')
    return lock, result


class DevelopmentObserver(validation.Observer):
    def __init__(self, targets, context):
        super().__init__(targets)
        self.references = tuple({s['tags'] for s in context['seeds']})
        self.summaries = {}

    def audit_items(self, name, items):
        # Counts and means only; no source titles/IDs/ratings or private tags.
        rows = sorted(items, key=lambda row: (-row['value'], row['bangumi_id'] if 'bangumi_id' in row
                                             else row['anime'].bangumi_id))[:24]
        patterns = [row['tags'] for row in rows]
        self.summaries[name] = {'base_head_size': len(rows), 'base_head_patterns': len(set(patterns)),
            'base_head_exact_source_sets': sum(tags in self.references for tags in patterns),
            'base_head_score_mean': sum(row['value'] for row in rows) / len(rows) if rows else None}


def run(user, context, index, category, arm, observer=None, *, dual=False, public_reader=None):
    phases = QueryPhases()
    if arm not in ('candidate_v2', 'aggregate_v2') and (public_reader is None or arm == 'current_v1'):
        with connection.execute_wrapper(phases):
            rows, timing = validation.run_arm(user, context, index, category,
                'candidate_v2' if arm == 'protected_v2' else arm, observer)
        return rows, {**timing, 'sql_phases': phases.summary()}
    executor = execute_candidate
    if dual and arm == 'candidate_v2':
        from .dual_discovery import execute_candidate as executor
    counter = QueryCounter()
    started, cpu = perf_counter(), process_time()
    with connection.execute_wrapper(counter), connection.execute_wrapper(phases):
        if public_reader is None:
            rows, _, metadata = executor(user, {'category': category}, index, context=context, observer=observer)
        else:
            from .execution import execute_v2
            from .exploration_candidate import PROTECTED_VERSION
            from . import dual_discovery, discovery_candidate
            version = ((dual_discovery.VERSION if dual else discovery_candidate.VERSION) if arm == 'candidate_v2'
                       else discovery_candidate.VERSION if arm == 'aggregate_v2' else None)
            rows, _, metadata = execute_v2(user, {'category': category}, index, context=context, observer=observer,
                exploration_policy_version=None if arm == 'reference_v2' else PROTECTED_VERSION,
                discovery_policy_version=version, public_reader=public_reader)
    return rows, {'compute_ms': round((perf_counter() - started) * 1000, 3),
        'process_cpu_ms': round((process_time() - cpu) * 1000, 3), 'sql_queries': counter.count,
        'sql_execute_ms': round(counter.seconds * 1000, 3), 'candidate_pool': metadata['trace'].get('candidate_pool', 0),
        'sql_phases': phases.summary(), 'public_read': metadata['trace'].get('public_read')}


def evaluate(user, context, index, targets, *, dual=False, public_reader=None):
    training = validation.joint_training(context, targets)
    original = deepcopy(context)
    features = {identity: tags for identity, tags, _, _ in index['features']}
    samples, holdout, digests, destinations, summaries = {}, {}, {}, {}, {}
    safe_errors = evidence_errors = checked = 0
    for arm in DUAL_ARMS if dual else ARMS:
        metrics, performance = {}, {}
        holdout[arm], destinations[arm], summaries[arm] = {}, {}, {}
        for category in ('all', 'familiar', 'explore'):
            observer = DevelopmentObserver(targets, training)
            first, timing1 = run(user, training, index, category, arm, dual=dual, public_reader=public_reader)
            rows, timing2 = run(user, training, index, category, arm, observer, dual=dual, public_reader=public_reader)
            digest = output_digest(rows)
            if digest != output_digest(first) or (timing1['sql_queries'] != timing2['sql_queries']
                    and not (public_reader is not None and timing1['sql_queries'] == timing2['sql_queries'] + 1
                        and timing1.get('public_read', {}).get('cache_hit') is False
                        and timing2.get('public_read', {}).get('cache_hit') is True)):
                raise ValueError('Development instrumentation changed results or SQL')
            digests[arm, category] = digest
            if arm == 'candidate_v2' and category != 'all' and digest != digests['protected_v2', category]:
                raise ValueError('Discovery experiment changed an out-of-scope category')
            metrics[category] = comparable_metrics(rows, training, training, features, 10)
            metrics[category].pop('top_bangumi_ids', None)
            safe_errors += sum(row['anime'].nsfw is not False or row['anime'].bangumi_id not in features for row in rows)
            evidence_errors += metrics[category]['evidence_error_count']; checked += len(rows)
            if any((row.get('evidence', {}).get('source') or {}).get('bangumi_id') in targets for row in rows):
                raise ValueError('Jointly heldout source leaked into evidence')
            holdout[arm][category] = validation.holdout_metrics(rows, targets)
            destinations[arm][category] = observer.stages
            observer.audit_items('returned_base_head', rows)
            summaries[arm][category] = observer.summaries
            performance[category] = {'runs': [timing1, timing2], 'stable_order_and_evidence': True,
                'maximum_compute_ms': max(timing1['compute_ms'], timing2['compute_ms']),
                'maximum_sql_queries': max(timing1['sql_queries'], timing2['sql_queries'])}
        samples[arm] = {'categories': metrics, 'performance': performance}
    if context != original:
        raise ValueError('Development comparison mutated original inputs')
    return {'natural_samples': len(context['seeds']), 'training_samples': len(training['seeds']),
        'paired_targets': 4, 'joint_holdout': True, 'arms': samples, 'holdout': holdout,
        'destinations': destinations, 'base_head_audit': summaries, 'safety_errors': safe_errors,
        'evidence_errors': evidence_errors, 'evidence_checked_rows': checked,
        'target_identity_exported': False, 'explicit_preferences_retained': True,
        'strict_independent_validation': False}


def report_verdict(samples):
    result = validation.verdict(samples, True)
    result['development_only'] = True
    result['independent_validation_required'] = True
    result['serving_approval'] = False
    result['hits']['protected_v2'] = {category: sum(s['holdout']['protected_v2'][category]['hit_at_k'] * 4
        for s in samples) for category in ('all', 'familiar', 'explore')}
    if samples and 'aggregate_v2' in samples[0]['holdout']:
        result['hits']['aggregate_v2'] = {category: sum(s['holdout']['aggregate_v2'][category]['hit_at_k'] * 4
            for s in samples) for category in ('all', 'familiar', 'explore')}
    if result['status'] == 'pass_on_target_disjoint_supplement':
        result['status'] = 'development_gates_passed_not_release_approval'
    return result
