"""Offline, deterministic constraints on association and incumbent coverage.

No holdout, account, title or evaluation-result input. Association is a genre
proxy, NOT liking probability. Protect a complete existing 24-work batch, not
only the evaluator's top ten. The remaining four batches retain incumbents.
"""
from fractions import Fraction

from . import ranking_v2, v2_controls

VERSION = 'association-novelty-explore.v3'
HEAD_SIZE = 24
RESULT_LIMIT = 120


def policy_contract():
    return {'head_size': HEAD_SIZE, 'result_limit': RESULT_LIMIT,
            'incumbent_reserve': RESULT_LIMIT - HEAD_SIZE,
            'association': 'exact-max-source-or-explicit-genre-jaccard',
            'floor': 'each-prefix-of-current-base-relevance-order-through-one-batch',
            'repair': 'latest-prefix-position-then-smallest-sufficient-gain-then-proposal-rank-id',
            'arithmetic': 'exact-rational-no-tolerance',
            'missing_after_hydration': 'drop-never-resurrect',
            'private_state': 'request-local-plan-count-only-trace',
            'selection_uses_holdout_ids': False}


def prepare_pool(items, vector, weights, options, *, proposal_pool, limit=400):
    """Reserve <=120 IDs in the same <=400 hydration query, not another query."""
    if type(limit) is not int or not RESULT_LIMIT <= limit <= 400:
        raise ValueError('Protected pool must fit the declared result budget')
    ordered, _ = ranking_v2._related_unique(items, vector)
    by_id = {row['bangumi_id']: row for row in ordered}
    reference = [{**row, 'value': row['_recall_reference_value']} for row in ordered]
    incumbent_pool = ranking_v2.balanced_candidate_pool(reference, vector, limit=limit)
    incumbents, _ = v2_controls.rerank(incumbent_pool, vector, weights, options)
    reference_ids = [row['bangumi_id'] for row in ordered[:HEAD_SIZE]]
    incumbent_ids = [row['bangumi_id'] for row in incumbents[:RESULT_LIMIT - HEAD_SIZE]]
    selected = {}
    for identity in (*incumbent_ids, *reference_ids, *(row['bangumi_id'] for row in proposal_pool),
                     *(row['bangumi_id'] for row in ordered)):
        if len(selected) >= limit:
            break
        selected.setdefault(identity, by_id[identity])
    return sorted(selected.values(), key=ranking_v2._order), {
        'reference_ids': reference_ids, 'incumbent_ids': incumbent_ids}


def _overlap(tags, references):
    return max((Fraction(len(tags & ref), len(tags | ref)) for ref in references if tags | ref),
               default=Fraction())


def protect_prefix(proposal, reference, context, *, head_size=HEAD_SIZE):
    """Exchange repairs only increase already-protected earlier prefixes.

    Full reference batch membership in proposal makes the constraints feasible:
    the top-k association sum cannot be less than any reference k-set's sum.
    Minimal sufficient gains avoid turning every repair into max-similarity.
    """
    if head_size < 0:
        raise ValueError('Invalid association head size')
    rows = list(proposal)
    ids = [row['bangumi_id'] for row in rows]
    if len(ids) != len(set(ids)) or len(ids) > 400:
        raise ValueError('Protection requires unique bounded validated candidates')
    if not {row['bangumi_id'] for row in reference} <= set(ids):
        raise ValueError('Reference contains unvalidated candidates')
    refs = [seed['tags'] for seed in context['seeds']]
    if context['preferences']['tags']:
        refs.append(frozenset(context['preferences']['tags']))
    cache = {tags: _overlap(tags, refs) for tags in {row['tags'] for row in rows}}
    priority = {identity: index for index, identity in enumerate(ids)}
    floors, cumulative = [], Fraction()
    for row in reference[:head_size]:
        cumulative += cache[row['tags']]
        floors.append(cumulative)
    swaps = 0
    for end, floor in enumerate(floors, 1):
        current = sum((cache[row['tags']] for row in rows[:end]), Fraction())
        while current < floor:
            needed = floor - current
            pair = None
            # Prefer the latest existing position: protect the earlier head.
            for position in reversed(range(end)):
                eligible = [index for index in range(end, len(rows))
                    if cache[rows[index]['tags']] - cache[rows[position]['tags']] >= needed]
                if eligible:
                    other = min(eligible, key=lambda index: (
                        cache[rows[index]['tags']] - cache[rows[position]['tags']],
                        priority[rows[index]['bangumi_id']], rows[index]['bangumi_id']))
                    pair = (position, other)
                    break
            if pair is None:
                # Several smaller gains may be necessary. Strictly increasing
                # prefix sums terminate; do not relax or silently skip a floor.
                choices = [(cache[rows[j]['tags']] - cache[rows[i]['tags']], i, j)
                           for i in range(end) for j in range(end, len(rows))
                           if cache[rows[j]['tags']] > cache[rows[i]['tags']]]
                if not choices:
                    raise ValueError('Association constraint infeasible; no unvalidated filler')
                _, position, other = max(choices, key=lambda value: (
                    value[0], value[1], -priority[rows[value[2]]['bangumi_id']]))
                pair = (position, other)
            position, other = pair
            gain = cache[rows[other]['tags']] - cache[rows[position]['tags']]
            rows[position], rows[other] = rows[other], rows[position]
            current += gain
            swaps += 1
    if any(sum((cache[row['tags']] for row in rows[:end]), Fraction()) < floor
           for end, floor in enumerate(floors, 1)):
        raise ValueError('Association prefix verification failed')
    return rows, {'protected_prefixes': len(floors), 'repair_swaps': swaps, 'prefix_floor_passed': True}


def rerank(current, proposal, plan, context):
    """Use current safe rows only. Scores and evidence objects remain intact."""
    by_id = {row['bangumi_id']: row for row in current}
    if len(by_id) != len(current) or any(row['bangumi_id'] not in by_id
            or row != by_id[row['bangumi_id']] for row in proposal):
        raise ValueError('Proposal must consist of the same validated rows')
    # Standard reranker returns only 120. Include its unreturned validated tail
    # so a repair can use a safe high-association work without another DB scan.
    proposal_ids = {row['bangumi_id'] for row in proposal}
    complete = list(proposal) + sorted((row for row in current if row['bangumi_id'] not in proposal_ids),
                                       key=ranking_v2._order)
    reference = [by_id[identity] for identity in plan['reference_ids'] if identity in by_id]
    protected, stats = protect_prefix(complete, reference, context)
    head = protected[:HEAD_SIZE]
    chosen = {row['bangumi_id']: row for row in head}
    retained = [identity for identity in plan['incumbent_ids'] if identity in by_id]
    for identity in (*retained, *(row['bangumi_id'] for row in protected)):
        if len(chosen) >= RESULT_LIMIT:
            break
        chosen.setdefault(identity, by_id[identity])
    if not set(retained) <= set(chosen):
        raise ValueError('Incumbent reserve overflow; no silent coverage loss')
    stats.update(version=VERSION, head_count=len(head), incumbent_planned=len(plan['incumbent_ids']),
                 incumbent_validated=len(retained), incumbent_retained=len(set(retained) & set(chosen)),
                 reference_dropped=len(plan['reference_ids']) - len(reference))
    return list(chosen.values()), stats
