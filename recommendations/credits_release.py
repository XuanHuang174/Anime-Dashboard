"""13F fixed release policy. Operator-only reads never change rollout or grant access."""
import hashlib
from collections import Counter
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from .credits_coverage import coverage_audit
from .credits_api_contract import KINDS
from .credits_read import MAX_FACTS, MAX_WORKS
from .credits_source import digest
from .browsing import catalog_revision

VERSION = 'recommendations.credits.release.v1'
# Frozen before production import, based on 13B's 31,104-work public preflight.
# Actual relations have their own floor so empty observations cannot pass alone.
POLICY = {'version': 'credits.release-policy.13f.v1', 'source_max_age_days': 45,
    'studio': {'min_fresh_complete_percent': 90, 'min_safe_associated_percent': 35, 'min_adult_associated_percent': 20},
    'voice_actor': {'min_fresh_complete_percent': 98, 'min_safe_associated_percent': 25, 'min_adult_associated_percent': 25}}


def code_revision():
    root = Path(settings.BASE_DIR)
    paths = sorted([*root.joinpath('recommendations').rglob('*.py'),
        *root.joinpath('frontend/src').rglob('*.js'), *root.joinpath('frontend/src').rglob('*.jsx'),
        *root.joinpath('frontend/src').rglob('*.css'), root / 'config/settings.py',
        root / 'scripts/verify_credits_deployment.py'])
    value = hashlib.sha256()
    for path in paths:
        if path.is_file():
            value.update(path.relative_to(root).as_posix().encode()); value.update(b'\0'); value.update(path.read_bytes())
    return value.hexdigest()


def kinds_argument(value):
    if value == 'both': return KINDS
    if value in KINDS: return (value,)
    raise ValueError('Unknown credits release kind')


def rollout(stage, kinds):
    if stage not in ('closed', 'canary', 'full') or not kinds or any(k not in KINDS for k in kinds):
        raise ValueError('Invalid release stage/kinds')
    users = getattr(settings, 'RECOMMENDATION_CREDITS_CANARY_USERS', [])
    valid = isinstance(users, (list, tuple)) and all(isinstance(u, str) and u and u == u.strip() for u in users)
    valid = valid and len(set(users)) == len(users)
    flags = {k: getattr(settings, 'RECOMMENDATION_CREDITS_' + k.upper() + '_ENABLED', False) is True for k in KINDS}
    issues = []
    if not valid: issues.append('invalid_credits_canary_config')
    if stage == 'closed':
        if any(flags.values()): issues.append('credits_gates_not_closed')
    else:
        for kind in KINDS:
            if flags[kind] != (kind in kinds): issues.append(kind + '_gate_mismatch')
        if stage == 'canary' and not users: issues.append('credits_canary_accounts_required')
        if stage == 'full' and users: issues.append('credits_canary_not_cleared')
    # Never restrict the already released three modes to make this audit pass.
    if getattr(settings, 'RECOMMENDATION_EXPLORATION_ENABLED', False) is not True:
        issues.append('existing_exploration_disabled')
    if getattr(settings, 'RECOMMENDATION_EXPLORATION_CANARY_USERS', []) != []:
        issues.append('existing_exploration_not_full')
    return {'expected_stage': stage, 'selected_kinds': list(kinds), 'flags': flags,
        'canary_count': len(users) if valid else None, 'issues': issues,
        'environment_origin': 'calling_django_process_not_render_confirmation', 'account_existence_verified': False}


def evaluate(data, kinds):
    issues, decisions = [], {}
    if not data.get('schema_ready'):
        return ['required_migration_missing'], {}
    integrity = data.get('integrity_issues', {})
    buckets = data.get('integrity_by_kind', {})
    classified = Counter()
    for kind in KINDS: classified.update(buckets.get(kind, {}))
    unclassified = dict(classified) != integrity
    if unclassified: issues.append('credits_integrity_issues')
    for kind in kinds:
        failures, policy = [], POLICY[kind]
        if buckets.get(kind): failures.append('integrity_issues')
        for group in ('safe', 'adult'):
            row = data['segments'][kind][group]
            total, fresh, associated = row['catalog_count'], row['fresh_complete_count'], row['works_with_initial_source_relations']
            if not total:
                if group == 'safe': failures.append('safe_catalog_empty')
                continue
            if not 0 <= associated <= fresh <= total:
                failures.append(group + '_coverage_inconsistent')
            if 100 * fresh < total * policy['min_fresh_complete_percent']:
                failures.append(group + '_fresh_complete_below_policy')
            if not associated or 100 * associated < total * policy['min_' + group + '_associated_percent']:
                failures.append(group + '_associated_below_policy')
        capacity = data.get('read_capacity', {}).get(kind)
        if not capacity: failures.append('read_capacity_not_audited')
        elif capacity['maximum_entity_works'] > MAX_WORKS or capacity['maximum_possible_page_facts'] > MAX_FACTS:
            failures.append('read_capacity_exceeded')
        decisions[kind] = {'ready': not failures and not unclassified, 'issues': failures, 'policy': dict(policy)}
        issues.extend(kind + ':' + failure for failure in failures)
    return issues, decisions


def audit(stage='closed', kinds=KINDS):
    config = rollout(stage, kinds)
    code = code_revision()
    catalog = catalog_revision()
    data = coverage_audit()
    issues, decisions = evaluate(data, kinds)
    issues.extend(config['issues'])
    # Two independently enforced snapshots catch publication/direct updates.
    following = coverage_audit()
    if data != following or catalog != catalog_revision(): issues.append('credits_or_catalog_changed_during_audit')
    if config != rollout(stage, kinds): issues.append('credits_rollout_changed_during_audit')
    if code != code_revision(): raise ValueError('Code changed during audit')
    return {'contract_version': VERSION, 'generated_at': timezone.now().isoformat(), 'code_revision': code,
        'policy': POLICY, 'policy_fingerprint': digest(POLICY), 'catalog_revision': catalog,
        'status': 'blocked' if issues else 'ready_for_operator_verification', 'issues': issues,
        'components': decisions, 'coverage': data, 'rollout': config,
        'production_acceptance': 'not_performed', 'performance_acceptance': 'not_performed',
        'switches_changed': False, 'production_writes': False, 'private_records_read': False, 'network_requests': 0}
