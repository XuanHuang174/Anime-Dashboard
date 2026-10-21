"""9A opt-in controls. Frozen V1/default arithmetic is never changed here."""
import re

from . import engine
from .score_filters import matches_score

CONTROL_POLICY_VERSION = 'browse-controls.v1'
STYLES = ('balanced', 'close', 'open')
OPTION_KEYS = ('category', 'style', 'type', 'min_score', 'year_from', 'year_to',
               'hide_recorded', 'page_size')
# Full year, year-month or ISO date only; never search titles for four digits.
_SEASON = re.compile(r'([1-9]\d{3})(?:-(0?[1-9]|1[0-2])(?:-(0?[1-9]|[12]\d|3[01]))?)?\Z')


def season_year(value):
    match = _SEASON.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        return None
    if match[3] is not None:
        from datetime import date
        try:
            date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return None
    return int(match[1])


def canonical_options(options):
    result = {key: format(options[key], '.1f') if key == 'min_score' else options[key]
              for key in OPTION_KEYS}
    if float(options.get('max_score', 10)) < 10:
        result['max_score'] = format(float(options['max_score']), '.1f')
    return result


def valid_option_keys(options):
    return set(options) in (set(OPTION_KEYS), set(OPTION_KEYS) | {'max_score'})


def filter_index(index, rows, options):
    """Filter BEFORE V1's 400-item truncation; retain whole-catalog IDF weights."""
    allowed = set()
    for row in rows:
        if not matches_score(row['score'], options):
            continue
        if options['type'] != 'all' and row['status_type'] != options['type']:
            continue
        if options['year_from'] is not None or options['year_to'] is not None:
            year = season_year(row['season'])
            if year is None or (options['year_from'] is not None and year < options['year_from']) or (
                    options['year_to'] is not None and year > options['year_to']):
                continue
        allowed.add(row['bangumi_id'])
    return {**index, 'features': [row for row in index['features'] if row[0] in allowed]}


def styled_score(tags, score, vector, weights, category, affinity, style):
    base = engine.candidate_score(tags, score, vector, weights, category, affinity)
    if base is None:
        return None
    # Keep the category's actual eligibility, especially explore's novelty gate.
    if style == 'close':
        related = engine.candidate_score(tags, 0, vector, weights, 'all')
        coverage = sum(weights.get(tag, 1) for tag in sorted(tags & vector.keys())) / sum(
            weights.get(tag, 1) for tag in sorted(tags))
        return .90 * base + .05 * related + .05 * coverage
    return base


def rank_controlled(context, options, index):
    """Only balanced delegates to the exact frozen V1 function.

    Other styles use their own identified, bounded greedy policy. This is NOT V2
    and never changes candidate identity, invents evidence, or pads the pool.
    """
    if options['style'] == 'balanced':
        return engine.rank_context(context, options, index, algorithm_version=engine.ALGORITHM_VERSION)
    if context['mode'] == 'cold_start':
        return [], index['weights']
    weights, vector = index['weights'], engine.taste_vector(context)
    excluded = set(context.get('feedback', {}).get('hidden_ids', ()))
    excluded.update(seed['bangumi_id'] for seed in context['seeds'])
    if options['hide_recorded']:
        excluded.update(context['owned'])
    pool = []
    for identity, tags, score, _ in index['features']:
        if identity in excluded:
            continue
        evidence, reason, affinity = engine.evidence_for(tags, context, weights)
        if not evidence or (options['category'] == 'familiar' and context['mode'] == 'personalized'
                            and evidence['kind'] != 'rated_work'):
            continue
        value = styled_score(tags, score, vector, weights, options['category'], affinity, options['style'])
        if value is not None:
            pool.append({'bangumi_id': identity, 'tags': tags, 'value': value,
                         'evidence': evidence, 'reason': reason})
    pool.sort(key=lambda item: (-item['value'], item['bangumi_id']))
    eligible = len(pool)
    pool = pool[:engine.CANDIDATE_POOL]
    rows = {row.bangumi_id: row for row in engine.safe_catalog().filter(
        bangumi_id__in=[item['bangumi_id'] for item in pool]).defer('synopsis', 'source_cover_url')}
    pool = [{**item, 'anime': rows[item['bangumi_id']]} for item in pool if item['bangumi_id'] in rows]
    # Explicit category-specific strength: familiar stays concentrated; explore
    # retains V1's real novel-topic gate even when changing repetition strength.
    # Frozen V1 discovery maximizes .5*base-.5*overlap, i.e. base-1*overlap.
    # Open is modestly STRONGER than that baseline, not mistakenly weaker.
    penalty = {'close': {'all': .20, 'familiar': 0, 'explore': 0},
               'open': {'all': 1.10, 'familiar': .02, 'explore': .12}}[options['style']][options['category']]
    chosen = []
    while pool and len(chosen) < engine.MAX_RESULTS:
        def key(item):
            overlap = max((len(item['tags'] & prior['tags']) / len(item['tags'] | prior['tags'])
                           for prior in chosen[-5:]), default=0)
            return item['value'] - penalty * overlap, -item['bangumi_id']
        winner = max(pool, key=key)
        pool.remove(winner)
        chosen.append(winner)
    context['_ranking_trace'] = {'eligible_candidates': eligible,
                                'candidate_pool': min(eligible, engine.CANDIDATE_POOL)}
    return chosen, weights
