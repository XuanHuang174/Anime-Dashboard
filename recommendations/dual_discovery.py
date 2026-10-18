"""Offline equal-rank fusion of aggregate taste and source-work relevance.

Canonical IDs break ties only. Neither target identities nor account identities
are inputs. Scores are internal ordering signals, never probabilities.
"""
from collections import deque
import hashlib
import json
import math
from pathlib import Path

from django.conf import settings

from . import recall_v2, v2_controls
from .discovery_candidate import DiscoveryScorer
from .exploration_candidate import DualExplorationScorer, PROTECTED_VERSION
from .version_control import canonical_bytes

VERSION = 'dual-view-discovery.v1'
RANK_SMOOTHING = 24


def policy_contract():
    body = {'version': VERSION, 'scope': 'offline-personalized-all-only',
        'views': ['existing-v2-source-score', 'existing-v1-aggregate-score'],
        'pool': 'equal-round-robin-existing-recall-per-view-deduplicate-400',
        'fusion': 'equal-normalized-reciprocal-ranks-after-current-hydration',
        'rank_smoothing': RANK_SMOOTHING, 'smoothing_origin': 'existing-batch-size-not-fitted',
        'rerank': 'unchanged-v2-prefix-repetition', 'exploration': PROTECTED_VERSION,
        'other_categories_and_cold_start': 'exact-protected-v2',
        'evaluation_scope': 'previously-revealed-development-targets-not-independent-validation',
        'parameter_search': False, 'serving_enabled': False}
    declared = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2-dual-discovery.v1.json').read_text(encoding='utf-8'))
    if declared != body:
        raise ValueError('Dual discovery protocol changed; no silent tuning')
    return body


def recipe_descriptor():
    from .exploration_candidate import recipe_descriptor as protected_recipe
    body = {**protected_recipe(PROTECTED_VERSION), 'discovery_policy_version': VERSION,
            'discovery_policy_sha256': hashlib.sha256(canonical_bytes(policy_contract())).hexdigest()}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}


def active(context, options):
    return context['mode'] == 'personalized' and options['category'] == 'all'


class DualDiscoveryScorer(DiscoveryScorer):
    def score(self, tags, public_rating):
        return DualExplorationScorer.score(self, tags, public_rating)

    def aggregate_value(self, tags, public_rating):
        # Reuse the existing memo and arithmetic; no duplicate private cache.
        result = DiscoveryScorer.score(self, tags, public_rating)
        return None if result is None else result[2]


def candidate_pool(items, vector, context, weights, *, limit=400):
    if type(limit) is not int or not 0 <= limit <= 400:
        raise ValueError('Dual pool limit must be an integer in 0..400')
    originals = {row['bangumi_id']: row for row in items}
    aggregate = [{**row, 'value': row['_aggregate_view_value']} for row in items]
    queues = deque(deque(recall_v2.candidate_pool(view, vector, context, weights,
        limit=limit, category='all')) for view in (items, aggregate))
    chosen, used = [], set()
    while queues and len(chosen) < limit:
        queue = queues.popleft()
        while queue and queue[0]['bangumi_id'] in used:
            queue.popleft()
        if queue:
            identity = queue.popleft()['bangumi_id']
            chosen.append(originals[identity]); used.add(identity)
        if queue:
            queues.append(queue)
    return chosen


def fuse(items):
    """Recompute both ranks over CURRENT safe/evidenced rows, not stale pool.

    Equal reciprocal ranks avoid treating the two incompatible raw score scales
    as equivalent. A copied row carries the internal fused score; evidence and
    source/public scores remain unchanged. No phantom ranks for removed works.
    """
    for row in items:
        if not all(math.isfinite(row[key]) for key in ('value', '_aggregate_view_value')):
            raise ValueError('Invalid dual score')
    ranks = [{row['bangumi_id']: rank for rank, row in enumerate(sorted(items,
        key=lambda row: (-row[key], row['bangumi_id'])), 1)}
        for key in ('value', '_aggregate_view_value')]
    return [{**row, '_source_view_value': row['value'], 'value': (RANK_SMOOTHING + 1) / 2 * sum(
        1 / (RANK_SMOOTHING + rank[row['bangumi_id']]) for rank in ranks)} for row in items]


def execute_candidate(user, options, index, *, context=None, observer=None):
    from .execution import execute_v2
    return execute_v2(user, options, index, context=context, observer=observer,
        exploration_policy_version=PROTECTED_VERSION, discovery_policy_version=VERSION)
