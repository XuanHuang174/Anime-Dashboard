"""5B-2 deterministic public-candidate selection; no DB writes or title inference.

Scores and penalties are internal ranking units, never preference probabilities.
Policy is deliberately versioned/fixed; online default remains content.v1 until
the same-snapshot 5B-3 evaluation approves a release.
"""
from collections import Counter, deque
import math

POLICY_VERSION = 'balanced-rerank.v1'
RELEVANCE_CORE_FRACTION = .5
EXPLORATION_WEIGHTS = (.65, .25, .10)  # relatedness, new topics, public rating
REPETITION_STRENGTH = {'all': .10, 'familiar': .025, 'explore': .12}
NOVEL_DIRECTION_SHARE = .8


def policy_contract():
    """Included in the private pagination HMAC, not exposed as a match score."""
    return {'version': POLICY_VERSION, 'relevance_core_fraction': RELEVANCE_CORE_FRACTION,
            'exploration_weights': list(EXPLORATION_WEIGHTS),
            'repetition_strength': dict(REPETITION_STRENGTH),
            'novel_direction_share': NOVEL_DIRECTION_SHARE}


def _order(item):
    return (-item['value'], item['bangumi_id'], tuple(sorted(item['tags'])))


def _related_unique(items, vector):
    preferred = {tag for tag, value in vector.items() if value > 0}
    unique = {}
    for item in items:
        identity = item['bangumi_id']
        if (type(identity) is not int or identity <= 0 or not math.isfinite(item['value'])
                or not item['tags'] & preferred or not item.get('evidence') or not item.get('reason')):
            continue
        # Never combine two identities or union conflicting tag snapshots. Keep
        # one deterministic row for an identical canonical Bangumi ID only.
        if identity not in unique or _order(item) < _order(unique[identity]):
            unique[identity] = item
    return sorted(unique.values(), key=_order), preferred


def balanced_candidate_pool(items, vector, *, limit=400):
    """Half relevance head, half equal-turn genre strata with unused-slot refill.

    Assign multi-genre works to their rarest shared preferred genre, so popular
    tags cannot consume every stratum. No randomness, title heuristics or filler.
    """
    if limit <= 0:
        return []
    ordered, preferred = _related_unique(items, vector)
    if len(ordered) <= limit:
        return ordered
    frequency = Counter(tag for item in ordered for tag in item['tags'] & preferred)
    head_count = max(1, math.ceil(limit * RELEVANCE_CORE_FRACTION))
    chosen = ordered[:head_count]
    buckets = {}
    for item in ordered[head_count:]:
        primary = min(item['tags'] & preferred, key=lambda tag: (frequency[tag], -vector[tag], tag))
        buckets.setdefault(primary, deque()).append(item)
    strata = sorted(buckets, key=lambda tag: (-vector[tag], tag))
    while strata and len(chosen) < limit:
        active = []
        for tag in strata:
            chosen.append(buckets[tag].popleft())
            if buckets[tag]:
                active.append(tag)
            if len(chosen) == limit:
                break
        strata = active
    return sorted(chosen, key=_order)


def weighted_overlap(left, right, weights):
    union = left | right
    if not union:
        return 0.0
    return sum(weights.get(tag, 1) for tag in sorted(left & right)) / sum(weights.get(tag, 1) for tag in sorted(union))


def rerank_candidates(items, vector, weights, *, category, limit=120):
    """Base relevance minus a bounded, category-specific repetition penalty.

    Compare with the WHOLE selected prefix (incremental maxima, not last five).
    In explore mode emphasize repetition of NEW genre directions rather than
    penalizing the shared preference that made a recommendation relevant.
    A base gap larger than the strength can never be reversed by diversity.
    """
    strength = REPETITION_STRENGTH[category]
    ordered, preferred = _related_unique(items, vector)
    if category == 'explore':
        ordered = [item for item in ordered if item['tags'] - preferred]
    remaining = {item['bangumi_id']: item for item in ordered}
    overlaps = {identity: (0.0, 0.0) for identity in remaining}
    chosen = []
    while remaining and len(chosen) < limit:
        def key(item):
            full, novel = overlaps[item['bangumi_id']]
            repeat = ((1 - NOVEL_DIRECTION_SHARE) * full + NOVEL_DIRECTION_SHARE * novel
                      if category == 'explore' else full)
            return (item['value'] - strength * repeat, -item['bangumi_id'])
        winner = max(remaining.values(), key=key)
        chosen.append(winner)
        del remaining[winner['bangumi_id']]
        for identity, item in remaining.items():
            full, novel = overlaps[identity]
            overlaps[identity] = (
                max(full, weighted_overlap(item['tags'], winner['tags'], weights)),
                max(novel, weighted_overlap(item['tags'] - preferred, winner['tags'] - preferred, weights))
                if category == 'explore' else 0.0,
            )
    return chosen
