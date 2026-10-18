"""Offline discovery ablation: aggregate taste instead of nearest-set dominance.

Reuse V1's existing aggregate coefficients, not fitted weights or target IDs.
Only personalized/all changes; cold start, familiar and protected exploration
keep their existing paths. This is not a serving recipe or a release approval.
"""
import hashlib
import json
from pathlib import Path

from django.conf import settings

from . import engine, v2_controls
from .exploration_candidate import DualExplorationScorer, PROTECTED_VERSION
from .version_control import canonical_bytes

VERSION = 'aggregate-discovery.v1'
WEIGHTS = (.85, .10, .05)


def policy_contract():
    body = {'version': VERSION, 'scope': 'offline-personalized-all-only',
        'weights': dict(zip(('aggregate_idf_cosine', 'public_quality', 'novelty'), WEIGHTS)),
        'weight_origin': 'existing-v1-discovery-not-fitted',
        'ablation': 'base-score-only', 'candidate_pool': 'unchanged-source-pattern-recall.v2',
        'rerank': 'unchanged-v2-prefix-repetition',
        'exploration': PROTECTED_VERSION, 'other_categories_and_cold_start': 'exact-protected-v2',
        'evaluation_scope': 'previously-revealed-development-targets-not-independent-validation',
        'parameter_search': False, 'serving_enabled': False}
    declared = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2-discovery.v1.json').read_text(encoding='utf-8'))
    if body != declared:
        raise ValueError('Discovery development protocol changed; no silent tuning')
    return body


def recipe_descriptor():
    from .exploration_candidate import recipe_descriptor as protected_recipe
    body = {**protected_recipe(PROTECTED_VERSION), 'discovery_policy_version': VERSION,
            'discovery_policy_sha256': hashlib.sha256(canonical_bytes(policy_contract())).hexdigest()}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}


class DiscoveryScorer(DualExplorationScorer):
    def score(self, tags, public_rating):
        result = super().score(tags, public_rating)
        if result is None or self.context['mode'] != 'personalized' or self.options['category'] != 'all':
            return result
        evidence, reason, _ = result
        # Same bounded memo as existing V2. Recompute when saturated; never
        # retain per-work/account/holdout information or grow another cache.
        pattern = self.patterns.get(tags)
        if pattern is not None:
            parts = pattern[2]
            value = (WEIGHTS[0] * parts['related'] + WEIGHTS[1] * max(0., min(10., public_rating)) / 10
                     + WEIGHTS[2] * parts['novelty'])
            related = parts['related']
        else:
            value = engine.candidate_score(tags, public_rating, self.vector, self.weights, 'all', deterministic=True)
            related = self._related(tags)
        if self.options['style'] == 'close':
            value += v2_controls.CLOSE_RELATED_BONUS * max(0., min(1., related))
        return evidence, reason, value


def execute_candidate(user, options, index, *, context=None, observer=None):
    from .execution import execute_v2
    return execute_v2(user, options, index, context=context, observer=observer,
        exploration_policy_version=PROTECTED_VERSION, discovery_policy_version=VERSION)
