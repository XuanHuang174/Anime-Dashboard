"""V2-B offline candidate recall ONLY; scoring/reranking remain frozen.

All inputs have already passed current safety, public filters, private exclusions
and evidence generation. Routes are real feature sets, never titles/series IDs.
No user/candidate/holdout ID is a policy input. No random fill or shared cache.
"""
from collections import deque
import math

from . import ranking_v2

VERSION = 'source-pattern-recall.v1'
CONTRACT = {
    'version': VERSION,
    'relevance_head_fraction': .50,
    'source_route_fraction': .25,
    'relative_reserve_score_floor': .75,
    'relative_source_overlap_floor': .75,
    'route_order': 'canonical-feature-set-round-robin',
    'within_source_route': 'weighted-overlap-then-frozen-base-score-then-id',
    'within_direction_route': 'frozen-base-score-then-id',
    'pattern_rotation': True,
    'unused_slots': 'eligible-above-reserve-floor-only',
    'ranking_policy': ranking_v2.POLICY_VERSION,
    'candidate_limit': 400, 'result_limit': 120, 'evaluation_k': 10,
}


def policy_contract():
    return dict(CONTRACT)


def _rotating_patterns(rows):
    """Preserve all identities; patterns are scheduling buckets, NOT series."""
    buckets = {}
    for row in rows:
        buckets.setdefault(row['tags'], deque()).append(row)
    return deque(sorted(buckets.values(), key=lambda queue: ranking_v2._order(queue[0])))


def _take_routes(routes, chosen, used, quota):
    """Skip identities selected by another route; unused quota is refillable."""
    added = 0
    active = deque(routes)
    while active and added < quota:
        patterns = active.popleft()
        while patterns:
            queue = patterns.popleft()
            while queue and queue[0]['bangumi_id'] in used:
                queue.popleft()
            if not queue:
                continue
            row = queue.popleft()
            chosen.append(row); used.add(row['bangumi_id']); added += 1
            if queue:
                patterns.append(queue)
            break
        if patterns:
            active.append(patterns)
    return added


def candidate_pool(items, vector, context, weights, *, limit=400):
    """50% relevance head; 25% source patterns; remaining topic directions.

    Reserves must score >=75% of the best frozen base score in this category.
    A source route additionally requires >=75% of that route's best weighted
    overlap. These are internal gates, not confidence/probability claims.
    Short eligible pools are allowed. No scores/evidence/items are rewritten.
    """
    if type(limit) is not int or not 0 <= limit <= CONTRACT['candidate_limit']:
        raise ValueError('Recall candidate limit must be an integer in 0..400')
    ordered, preferred = ranking_v2._related_unique(items, vector)
    if not limit or not ordered:
        return []
    if len(ordered) <= limit:
        return ordered
    head_count = max(1, math.ceil(limit * CONTRACT['relevance_head_fraction']))
    chosen = ordered[:head_count]
    used = {row['bangumi_id'] for row in chosen}
    reserve = [row for row in ordered[head_count:]
               if row['value'] >= ordered[0]['value'] * CONTRACT['relative_reserve_score_floor']]
    references = {seed['tags'] for seed in context['seeds']} if context['mode'] == 'personalized' else set()
    explicit = frozenset(context['preferences']['tags'])
    if explicit:
        references.add(explicit)
    references = sorted((tags for tags in references if tags & preferred), key=lambda tags: tuple(sorted(tags)))
    # Memoize per PUBLIC feature set, not per work × seed; bounded by the
    # current catalog vocabulary. This cache lives for this function call only.
    patterns = {row['tags'] for row in reserve}
    routes = []
    for reference in references:
        overlap = {tags: ranking_v2.weighted_overlap(tags, reference, weights) for tags in patterns}
        best = max(overlap.values(), default=0)
        if not best:
            continue
        rows = [row for row in reserve if overlap[row['tags']] > 0
                and overlap[row['tags']] >= best * CONTRACT['relative_source_overlap_floor']]
        # Order patterns by route affinity before visiting each pattern once.
        queues = _rotating_patterns(rows)
        routes.append(deque(sorted(queues, key=lambda queue: (
            -overlap[queue[0]['tags']], ranking_v2._order(queue[0])))))
    _take_routes(routes, chosen, used, min(limit - len(chosen), math.floor(limit * CONTRACT['source_route_fraction'])))
    # One route per actual shared preferred topic + new-direction pattern.
    # Explicit-only / sparse accounts get no fabricated trusted seed routes.
    directions = {}
    for row in reserve:
        if row['bangumi_id'] in used:
            continue
        for tag in sorted(row['tags'] & preferred):
            directions.setdefault((tag, tuple(sorted(row['tags'] - preferred))), []).append(row)
    direction_routes = [_rotating_patterns(directions[key]) for key in sorted(directions)]
    _take_routes(direction_routes, chosen, used, limit - len(chosen))
    # All remaining slots can be filled only by truly associated reserve rows
    # above the fixed relevance floor. Never sample the unrelated safe catalog.
    for row in reserve:
        if len(chosen) == limit:
            break
        if row['bangumi_id'] not in used:
            chosen.append(row); used.add(row['bangumi_id'])
    return sorted(chosen, key=ranking_v2._order)
