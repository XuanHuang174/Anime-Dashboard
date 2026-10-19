"""One bounded offline recall experiment. No targets/owners enter selection.

Keep every protected candidate; only fill UNUSED slots with the strongest
existing IDF cosine associations. Do not blend scores, fuse ranks or infer
identity/genres. A larger pool is NOT a quality-success criterion.
"""
import hashlib
import json
import math
from pathlib import Path

from django.conf import settings

from . import ranking_v2
from .version_control import canonical_bytes

VERSION = 'append-only-recall.v1'


def policy_contract():
    body = {'version': VERSION, 'scope': 'offline-personalized-discovery-only',
        'selection': 'fill-unused-slots-by-existing-aggregate-relatedness',
        'original_candidates': 'retain-all-no-eviction', 'candidate_limit': 400, 'result_limit': 120,
        'base_scores_evidence_rerank': 'unchanged-protected-v2',
        'other_categories_and_cold_start': 'exact-protected-v2',
        'targets': 'original-consumed-pairs-no-reselection', 'independent_validation': False,
        'parameter_search': False, 'attempts': 1, 'failure_action': 'stop-current-v2-route-keep-v1',
        'success_requires': ['more-discovery-top10-hits-than-protected',
            'retain-every-v1-and-protected-top10-hit', 'no-mean-topic-proxy-regression',
            'no-mean-repetition-regression', 'zero-safety-and-evidence-errors'], 'serving_enabled': False}
    declared = json.loads((Path(settings.BASE_DIR) / 'evaluation/v2-recall-completion.v1.json').read_text(encoding='utf-8'))
    if declared != body:
        raise ValueError('Recall completion protocol changed; no post-result tuning')
    return body


def recipe_descriptor():
    from .exploration_candidate import recipe_descriptor as protected_recipe, PROTECTED_VERSION
    body = {**protected_recipe(PROTECTED_VERSION), 'recall_completion_version': VERSION,
            'recall_completion_sha256': hashlib.sha256(canonical_bytes(policy_contract())).hexdigest()}
    body.pop('recipe_revision')
    return {**body, 'recipe_revision': hashlib.sha256(canonical_bytes(body)).hexdigest()}


def relatedness(tags, vector, weights):
    # Same IDF cosine arithmetic as V2RequestScorer; no score/novelty blend.
    dot = sum(weights.get(tag, 1) ** 2 * vector[tag] for tag in sorted(tags) if tag in vector)
    norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in sorted(tags)))
    user_norm = math.sqrt(sum((weights.get(tag, 1) * vector[tag]) ** 2 for tag in sorted(vector)))
    return dot / (norm * user_norm) if norm and user_norm else 0.


def complete_pool(items, pool, vector, weights, context, options, *, limit=400):
    if type(limit) is not int or not 0 <= limit <= 400 or len(pool) > limit:
        raise ValueError('Invalid bounded completion pool')
    if context['mode'] != 'personalized' or options['category'] != 'all' or len(pool) == limit:
        return pool
    ordered, _ = ranking_v2._related_unique(items, vector)
    available = {row['bangumi_id']: row for row in ordered}
    used = {row['bangumi_id'] for row in pool}
    if len(used) != len(pool) or not used <= available.keys():
        raise ValueError('Protected pool is not a unique eligible subset')
    scores = {tags: relatedness(tags, vector, weights) for tags in {row['tags'] for row in ordered}}
    remaining = sorted((row for row in ordered if row['bangumi_id'] not in used),
                       key=lambda row: (-scores[row['tags']], ranking_v2._order(row)))
    return sorted([*pool, *remaining[:limit - len(pool)]], key=ranking_v2._order)
