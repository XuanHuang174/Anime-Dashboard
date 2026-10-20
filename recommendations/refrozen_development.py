"""Rebind CURRENT inputs to ORIGINAL revealed pairs, never reselect targets."""
import hashlib

from . import engine, unused_validation as validation
from .evidence import _token
from .quality_evaluation import input_fingerprint
from .version_control import canonical_bytes

CONTRACT = 'discovery-refrozen-inputs.v1'


def bind_original(users, original, *, progress=lambda text: None):
    selected = [entry for entry in original['entries'] if entry['status'] == 'selected']
    handles = {_token('recommendations.deployment.account.v1', user.pk): user for user in users}
    entries, samples = [], []
    if not selected or len(selected) > validation.configuration()['maximum_evaluation_accounts']:
        raise ValueError('No bounded original cohort')
    for number, entry in enumerate(selected, 1):
        progress(f'fixed input {number}/{len(selected)}')
        user = handles.get(entry['account'])
        if user is None:
            raise ValueError('Original account unavailable; no replacement')
        context = engine.user_context(user, evaluation_baseline=True)
        by_pair = {validation.pair_handle(user.pk, seed['bangumi_id']): seed['bangumi_id'] for seed in context['seeds']}
        if any(pair not in by_pair for pair in entry['targets']):
            raise ValueError('Original target no longer a valid natural seed; no replacement')
        targets = [by_pair[pair] for pair in entry['targets']]
        # Includes minimum natural training checks; never write ratings to pass.
        validation.joint_training(context, targets)
        if context['mode'] != 'personalized':
            raise ValueError('Original account lost personalized eligibility')
        entries.append({'account': entry['account'], 'input': input_fingerprint(context),
                        'targets': list(entry['targets'])})
        samples.append((user, context, targets))
    return entries, samples


def input_lock(original, previous, entries, revision):
    return {'contract_version': CONTRACT, 'original_cohort_sha256': hashlib.sha256(canonical_bytes(original)).hexdigest(),
        'previous_result_sha256': hashlib.sha256(canonical_bytes(previous)).hexdigest(),
        'entries': entries, 'catalog_revision': revision, 'ranking_performed': False,
        'target_selection': 'original-consumed-pairs-exact-no-reselection',
        'independent_validation': False, 'approval_issued': False}
