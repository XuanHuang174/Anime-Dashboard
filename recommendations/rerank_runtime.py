"""V2-C equivalent execution, NOT a new ranking policy or private result cache.

Identical genre sets have identical prefix overlap accumulators. Update those
once per pattern, while still comparing EVERY work's original float score and
ID. This preserves even subtraction-rounding ties between nearby base scores.
Pair memo lives for one call, is bounded, and is never shared across users.
"""
from collections import Counter
import math

from . import ranking_v2

VERSION = 'pattern-prefix-runtime.v1'
PAIR_CACHE_LIMIT = 8192
MICRO_REPEATS = 5


def runtime_contract():
    return {'version': VERSION, 'ranking_policy': ranking_v2.POLICY_VERSION,
            'pair_cache_limit': PAIR_CACHE_LIMIT, 'micro_repeats': MICRO_REPEATS,
            'state': 'exact-prefix-accumulators-per-genre-set',
            'winner': 'every-original-float-score-and-id', 'memo_scope': 'one-call-only',
            'float_overlap': 'frozen-sorted-weighted-overlap',
            'online_enabled': False}


def rerank_candidates(items, vector, weights, *, category, limit=120, repetition_strength=None):
    """Return identical row objects/order/values/evidence plus count-only stats."""
    strength = ranking_v2.REPETITION_STRENGTH[category] if repetition_strength is None else repetition_strength
    if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Invalid internal repetition strength')
    ordered, preferred = ranking_v2._related_unique(items, vector)
    if category == 'explore':
        ordered = [item for item in ordered if item['tags'] - preferred]
    remaining = {item['bangumi_id']: item for item in ordered}
    pattern_count = Counter(item['tags'] for item in ordered)
    patterns = sorted(pattern_count, key=lambda tags: tuple(sorted(tags)))
    pattern_ids = {tags: n for n, tags in enumerate(patterns)}
    novel = {tags: tags - preferred for tags in patterns}
    overlaps = {tags: (0.0, 0.0, 0.0) for tags in patterns}
    memo, chosen = {}, []
    stats = {'version': VERSION, 'candidate_count': len(ordered), 'pattern_count': len(patterns),
             'pair_cache_hits': 0, 'pair_cache_misses': 0, 'weighted_overlap_calls': 0,
             'prefix_state_updates': 0, 'pair_cache_entries': 0, 'pair_cache_limit': PAIR_CACHE_LIMIT}

    def pair(left, right):
        a, b = pattern_ids[left], pattern_ids[right]
        key = (a, b) if a <= b else (b, a)
        if key in memo:
            stats['pair_cache_hits'] += 1
            return memo[key]
        stats['pair_cache_misses'] += 1
        full = ranking_v2.weighted_overlap(left, right, weights)
        extra = ranking_v2.weighted_overlap(novel[left], novel[right], weights) if category == 'explore' else 0.0
        stats['weighted_overlap_calls'] += 2 if category == 'explore' else 1
        if len(memo) < PAIR_CACHE_LIMIT:
            memo[key] = (full, extra)
        return full, extra

    while remaining and len(chosen) < limit:
        def key(item):
            full_sum, full_max, novel_sum = overlaps[item['tags']]
            count = len(chosen) or 1
            if category == 'explore':
                repeat = ((1 - ranking_v2.NOVEL_DIRECTION_SHARE) * full_sum
                          + ranking_v2.NOVEL_DIRECTION_SHARE * novel_sum) / count
            elif category == 'familiar':
                repeat = full_max
            else:
                repeat = .7 * full_sum / count + .3 * full_max
            return (item['value'] - strength * repeat, -item['bangumi_id'])
        winner = max(remaining.values(), key=key)
        chosen.append(winner)
        del remaining[winner['bangumi_id']]
        pattern_count[winner['tags']] -= 1
        if not pattern_count[winner['tags']]:
            del overlaps[winner['tags']]
        # Each accumulator follows exactly the ORIGINAL winner sequence and
        # addition order; only identical copies of the state are eliminated.
        for tags, (full_sum, full_max, novel_sum) in overlaps.items():
            full, extra = pair(tags, winner['tags'])
            overlaps[tags] = (full_sum + full, max(full_max, full),
                              novel_sum + extra if category == 'explore' else 0.0)
            stats['prefix_state_updates'] += 1
    stats['pair_cache_entries'] = len(memo)
    return chosen, stats
