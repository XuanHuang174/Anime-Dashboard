"""5B-2 deterministic public-candidate selection; no DB writes or title inference.

Scores and penalties are internal ranking units, never preference probabilities.
Policy is deliberately versioned/fixed; online default remains content.v1 until
the same-snapshot 5B-3 evaluation approves a release.
"""
from collections import Counter, deque
import math

POLICY_VERSION = 'balanced-rerank.v2'
RELEVANCE_CORE_FRACTION = .5
REPETITION_STRENGTH = {'all': .25, 'familiar': .005, 'explore': .18}
NOVEL_DIRECTION_SHARE = .8
PUBLIC_SCAN_FETCH = 'current-sha256-compact.v1'
REQUEST_MEMO_LIMIT = 2048
DISCOVERY_WEIGHTS = (.50, .20, .15, .10, .05)  # nearest set, IDF affinity, vector, public, novelty
FAMILIAR_WEIGHTS = (.65, .25, .06, .03, .01)  # source affinity, trusted anchor, personal rating, vector, public
EXPLORE_WEIGHTS = (.45, .30, .15, .10)  # nearest set, vector, novelty, public


def policy_contract():
    """Included in the private pagination HMAC, not exposed as a match score."""
    return {'version': POLICY_VERSION, 'relevance_core_fraction': RELEVANCE_CORE_FRACTION,
            'repetition_strength': dict(REPETITION_STRENGTH),
            'novel_direction_share': NOVEL_DIRECTION_SHARE,
            'discovery_weights': list(DISCOVERY_WEIGHTS), 'familiar_weights': list(FAMILIAR_WEIGHTS),
            'explore_weights': list(EXPLORE_WEIGHTS), 'discovery_repeat': '0.7-prefix-mean+0.3-prefix-max',
            'public_scan_fetch': PUBLIC_SCAN_FETCH, 'request_memo_limit': REQUEST_MEMO_LIMIT,
            'familiar_anchor': 'strongest-trusted-genre-pattern-not-a-series',
            'pool_strata': 'preferred-genre-then-distinct-genre-pattern',
            'request_memo_version': 'public-title-collision-and-genre-pattern.v1'}


def base_score(*, category, nearest, affinity, related, novelty, source_rating, public_rating, anchor=0):
    """Supported work affinity is distinct from aggregate genre popularity.

    All inputs are bounded internal signals, NOT probability estimates. The
    source_rating is the actual personal score of the evidence source only.
    """
    quality = max(0.0, min(10.0, public_rating)) / 10
    personal = max(0.0, min(10.0, source_rating)) / 10
    if category == 'familiar':
        a, h, p, r, q = FAMILIAR_WEIGHTS
        return a * affinity + h * anchor + p * personal * affinity + r * related + q * quality
    if category == 'explore':
        if not novelty:
            return None
        n, r, x, q = EXPLORE_WEIGHTS
        return n * nearest + r * related + x * novelty + q * quality
    n, a, r, q, x = DISCOVERY_WEIGHTS
    return n * nearest + a * affinity + r * related + q * quality + x * novelty


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
        buckets.setdefault(primary, {}).setdefault(item['tags'], deque()).append(item)
    # Each genre's reserve rotates through actual feature combinations. This is
    # sampling, not deduplication/series inference: every Bangumi ID remains eligible.
    buckets = {tag: deque(sorted(patterns.values(), key=lambda group: _order(group[0])))
               for tag, patterns in buckets.items()}
    strata = sorted(buckets, key=lambda tag: (-vector[tag], tag))
    while strata and len(chosen) < limit:
        active = []
        for tag in strata:
            group = buckets[tag].popleft()
            chosen.append(group.popleft())
            if group:
                buckets[tag].append(group)
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

    Compare with the WHOLE selected prefix. Mean overlap measures accumulation,
    rather than saturating permanently after the first identical genre set.
    In explore mode emphasize repetition of NEW genre directions rather than
    penalizing the shared preference that made a recommendation relevant.
    A base gap larger than the strength can never be reversed by diversity.
    """
    strength = REPETITION_STRENGTH[category]
    ordered, preferred = _related_unique(items, vector)
    if category == 'explore':
        ordered = [item for item in ordered if item['tags'] - preferred]
    remaining = {item['bangumi_id']: item for item in ordered}
    overlaps = {identity: (0.0, 0.0, 0.0) for identity in remaining}  # full sum/max, novel sum
    chosen = []
    while remaining and len(chosen) < limit:
        def key(item):
            full_sum, full_max, novel_sum = overlaps[item['bangumi_id']]
            count = len(chosen) or 1
            if category == 'explore':
                repeat = ((1 - NOVEL_DIRECTION_SHARE) * full_sum + NOVEL_DIRECTION_SHARE * novel_sum) / count
            elif category == 'familiar':
                repeat = full_max
            else:
                repeat = .7 * full_sum / count + .3 * full_max
            return (item['value'] - strength * repeat, -item['bangumi_id'])
        winner = max(remaining.values(), key=key)
        chosen.append(winner)
        del remaining[winner['bangumi_id']]
        for identity, item in remaining.items():
            full_sum, full_max, novel_sum = overlaps[identity]
            overlap = weighted_overlap(item['tags'], winner['tags'], weights)
            overlaps[identity] = (
                full_sum + overlap, max(full_max, overlap),
                novel_sum + weighted_overlap(item['tags'] - preferred, winner['tags'] - preferred, weights)
                if category == 'explore' else 0.0,
            )
    return chosen
