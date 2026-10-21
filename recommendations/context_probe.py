"""Own-user foreground hint; NEVER a substitute for detail/cursor safety checks.

Build only the existing own-profile context, with no public feature index or
ranking. Candidate catalog changes still undergo live validation on each real
detail/continuation request. No private fields leave this endpoint.
"""
from .deployment import capabilities
from .engine import user_context
from .evidence import EvidencePresenter

CONTRACT_VERSION = 'recommendations.context.v1'
CONTEXT_KEYS = ('algorithm_version', 'ranking_policy_version', 'taxonomy_version',
                'feature_version', 'feature_rule_version', 'selection_rule_version',
                'profile_version', 'preference_version', 'feedback_version',
                'profile_policy_version', 'profile_min_score')


def context_contract(user, *, requested_scope='safe'):
    serving = capabilities()
    context = user_context(user, requested_scope=requested_scope)
    versions = EvidencePresenter(user, context, {}, serving['algorithm_version']).versions
    return {'contract_version': CONTRACT_VERSION,
            'content_scope_context': dict(context['_content_scope']),
            'rollout_revision': serving['rollout_revision'],
            'browse_enabled': serving['browse_enabled'],
            'context': {key: versions[key] for key in CONTEXT_KEYS}}
