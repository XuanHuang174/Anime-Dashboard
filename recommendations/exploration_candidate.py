"""Offline mechanism correction, not a serving policy or target-specific rescue.

Explore should reward association AND genuinely new genres, not primarily the
nearest existing source pattern (the latter is the familiar category's job).
Use the pre-existing V1 exploration weights as a fixed, non-fitted comparator;
retain V2's safety, evidence, balanced pool and prefix diversity machinery.
"""
import hashlib
import json
import math
from collections import deque
from pathlib import Path

from django.conf import settings

from . import engine, v2_controls, ranking_v2
from .version_control import canonical_bytes

VERSION = 'association-novelty-explore.v1'
DUAL_VERSION = 'association-novelty-explore.v2'
PROTECTED_VERSION = 'association-novelty-explore.v3'
WEIGHTS = (.5, .4, .1)


def policy_contract(version=VERSION):
    if version not in (VERSION, DUAL_VERSION, PROTECTED_VERSION):
        raise ValueError('Unknown offline exploration policy')
    body = {'version': version, 'scope': 'offline-personalized-explore-only',
        'weights': dict(zip(('related', 'novelty', 'public_quality'), WEIGHTS)),
        'weight_origin': 'existing-v1-explore-not-fitted-to-holdouts',
        'candidate_pool': ('unchanged-v2-balanced-400' if version == VERSION else
                           'dual-view-equal-core-and-pattern-reserve-400'),
        'rerank': 'unchanged-v2-prefix-repetition',
        'other_categories_and_preference_cold_start': 'exact-existing-v2',
        'cohort': 'original-d3-lock-and-original-v2a-lock-no-reselection',
        'parameter_search': False, 'serving_enabled': False}
    suffix = 'v1' if version == VERSION else 'v2'
    if version == PROTECTED_VERSION:
        from . import protected_exploration
        suffix = 'v3'
        body.update(candidate_pool='dual-view-with-incumbent-and-reference-reserve-400',
                    rerank='association-prefix-protection-and-incumbent-reserve',
                    protection=protected_exploration.policy_contract())
    declared = json.loads((Path(settings.BASE_DIR) / f'evaluation/v2d-exploration.{suffix}.json').read_text(encoding='utf-8'))
    if body != declared:
        raise ValueError('Exploration preregistration changed; no silent tuning')
    return body


def recipe_descriptor(version=VERSION):
    from .execution import recipe_descriptor as reference_recipe
    policy = policy_contract(version)
    body = {**reference_recipe(), 'exploration_policy_version': version,
            'exploration_policy_sha256': hashlib.sha256(canonical_bytes(policy)).hexdigest()}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}


class ExplorationScorer(v2_controls.ControlledScorer):
    def score(self, tags, public_rating):
        result = super().score(tags, public_rating)
        if result is None or self.context['mode'] != 'personalized' or self.options['category'] != 'explore':
            return result
        evidence, reason, _ = result
        # No title, account, target, ID, review or inferred series information.
        pattern = self.patterns.get(tags)
        if pattern is not None:
            components = pattern[2]
            related = components['related']
            value = (WEIGHTS[0] * related + WEIGHTS[1] * components['novelty']
                     + WEIGHTS[2] * max(0, min(10, public_rating)) / 10)
        else:  # Bounded memo saturated; recompute without growing private state.
            related = self._related(tags)
            value = engine.candidate_score(tags, public_rating, self.vector, self.weights,
                'explore', exploration_weights=WEIGHTS, deterministic=True)
        if value is None:
            return None
        if self.options['style'] == 'close':
            # Preserve the existing explicit control bonus, exactly once.
            value += v2_controls.CLOSE_RELATED_BONUS * max(0., min(1., related))
        return evidence, reason, value

    def _related(self, tags):
        norm = math.sqrt(sum(self.weights.get(tag, 1) ** 2 for tag in sorted(tags)))
        dot = sum(self.weights.get(tag, 1) ** 2 * self.vector[tag] for tag in sorted(tags) if tag in self.vector)
        return dot / (norm * self.vector_norm) if norm and self.vector_norm else 0.


class DualExplorationScorer(ExplorationScorer):
    def reference_value(self, tags, public_rating):
        # Exact old V2 value for recall ONLY; never replace candidate ranking or evidence.
        result = super(ExplorationScorer, self).score(tags, public_rating)
        return result[2] if result is not None else None


def dual_pool(items, vector, *, limit=400):
    """Equal core/reserve lanes in two views, ID dedup, bounded refill.

    No union of 800 hydrated records. Both proposal lists are pure in-memory
    selections over the SAME already-safe/associated rows. The four quotas are
    equal by construction, not selected using account/target outcomes.
    """
    ordered, _ = ranking_v2._related_unique(items, vector)
    if limit <= 0:
        return []
    if len(ordered) <= limit:
        return ordered
    if any(type(row.get('_recall_reference_value')) not in (float, int)
           or not math.isfinite(row['_recall_reference_value']) for row in ordered):
        raise ValueError('Dual recall needs actual reference scores, never guessed values')
    reference = [{**row, 'value': row['_recall_reference_value']} for row in ordered]
    lanes = []
    for view in (reference, ordered):
        by_score = sorted(view, key=ranking_v2._order)
        core = by_score[:max(1, math.ceil(limit * ranking_v2.RELEVANCE_CORE_FRACTION))]
        core_ids = {row['bangumi_id'] for row in core}
        pool = ranking_v2.balanced_candidate_pool(view, vector, limit=limit)
        reserve = [row for row in pool if row['bangumi_id'] not in core_ids]
        lanes.extend((deque(core), deque(reserve)))
    originals = {row['bangumi_id']: row for row in ordered}
    selected = {}

    def take(lane):
        if lane:
            identity = lane.popleft()['bangumi_id']
            selected.setdefault(identity, originals[identity])

    for lane in lanes:
        for _ in range(limit // len(lanes)):
            take(lane)
    while len(selected) < limit and any(lanes):
        for lane in lanes:
            take(lane)
            if len(selected) >= limit:
                break
    return sorted(selected.values(), key=ranking_v2._order)


def execute_candidate(user, options, index, *, context=None, observer=None):
    from .execution import execute_v2
    return execute_v2(user, options, index, context=context, observer=observer,
                      exploration_policy_version=VERSION)


def execute_dual_candidate(user, options, index, *, context=None, observer=None):
    from .execution import execute_v2
    return execute_v2(user, options, index, context=context, observer=observer,
                      exploration_policy_version=DUAL_VERSION)


def execute_protected_candidate(user, options, index, *, context=None, observer=None):
    from .execution import execute_v2
    return execute_v2(user, options, index, context=context, observer=observer,
                      exploration_policy_version=PROTECTED_VERSION)
