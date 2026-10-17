"""D1 versioned V2 controls; not V1 formulas, not an HTTP algorithm switch."""
import math

from . import engine, ranking_v2, rerank_runtime
from .browse_controls import season_year, OPTION_KEYS

VERSION = 'browse-controls.v2'
CLOSE_RELATED_BONUS = .03
REPETITION_MULTIPLIERS = {'balanced': 1., 'close': .70, 'open': 1.15}


def policy_contract():
    return {'version': VERSION, 'balanced': 'exact-corrected-recall-and-v2c-runtime',
            'close_related_bonus': CLOSE_RELATED_BONUS,
            'repetition_multipliers': dict(REPETITION_MULTIPLIERS),
            'categories': dict(ranking_v2.REPETITION_STRENGTH),
            'eligibility': 'unchanged-real-association-and-category-gates',
            'year_parser': 'browse_controls.season_year',
            'year_sources': 'live-season-only-never-title-or-synopsis',
            'idf_scope': 'whole-safe-catalog-not-filtered-subset'}


def validate_options(options):
    """Trusted prepared controls; HTTP still uses its existing strict serializer.

    None year defaults are legitimate internally; external explicit null/empty
    remains rejected by RecommendationQuerySerializer. No cursor/ID/version input.
    """
    from .serializers import RecommendationQuerySerializer
    from rest_framework.exceptions import ValidationError
    if not isinstance(options, dict) or set(options) - set(OPTION_KEYS):
        raise ValidationError('D1只接受类别、风格及筛选条件，不接受用户、版本或游标。')
    data = {key: value for key, value in options.items() if not (key in ('year_from', 'year_to') and value is None)}
    serializer = RecommendationQuerySerializer(data={**data, 'browse': True})
    serializer.is_valid(raise_exception=True)
    return {key: serializer.validated_data[key] for key in OPTION_KEYS}


def matches(row, options):
    if row['score'] < float(options['min_score']):
        return False
    if options['type'] != 'all' and row['status_type'] != options['type']:
        return False
    if options['year_from'] is not None or options['year_to'] is not None:
        year = season_year(row['season'])
        if year is None or (options['year_from'] is not None and year < options['year_from']) or (
                options['year_to'] is not None and year > options['year_to']):
            return False
    return True


def live_public_rows(query, options, expression, limit):
    # One LIVE bounded scan, including fields required for reliable filtering.
    # A truncated cached index is NOT used as the eligibility authority.
    raw = list(query.annotate(feature_revision=expression).order_by('bangumi_id').values_list(
        'bangumi_id', 'score', 'feature_revision', 'season', 'status_type')[:limit + 1])
    if len(raw) > limit:
        raise engine.RecommendationUnavailable()
    selected, metadata = [], {}
    for identity, score, revision, season, kind in raw:
        row = {'score': score, 'season': season, 'status_type': kind}
        if matches(row, options):
            selected.append((identity, score, revision))
            metadata[identity] = row
    return selected, metadata


class ControlledScorer(engine.V2RequestScorer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.live_metadata = {}
        self.related_cache = {}

    def accepts_row(self, row):
        metadata = self.live_metadata.get(row['bangumi_id'])
        if metadata is None:
            return False
        # Fresh reads/hydrated rows replace current season/type. A stale cache
        # or a row changing outside the filter must never re-enter via fallback.
        return matches({**metadata, **row}, self.options)

    def score(self, tags, public_rating):
        result = super().score(tags, public_rating)
        if result is None or self.options['style'] != 'close':
            return result
        if tags not in self.related_cache:
            dot = sum(self.weights.get(tag, 1) ** 2 * self.vector[tag] for tag in sorted(tags) if tag in self.vector)
            norm = math.sqrt(sum(self.weights.get(tag, 1) ** 2 for tag in sorted(tags)))
            related = dot / (norm * self.vector_norm) if norm and self.vector_norm else 0.
            if len(self.related_cache) < ranking_v2.REQUEST_MEMO_LIMIT:
                self.related_cache[tags] = related
        else:
            related = self.related_cache[tags]
        evidence, reason, base = result
        return evidence, reason, base + CLOSE_RELATED_BONUS * max(0., min(1., related))


def rerank(items, vector, weights, options):
    style = options['style']
    # Balanced calls EXACTLY the already verified C path, no float rescaling.
    strength = (None if style == 'balanced' else
                ranking_v2.REPETITION_STRENGTH[options['category']] * REPETITION_MULTIPLIERS[style])
    return rerank_runtime.rerank_candidates(items, vector, weights, category=options['category'],
                                           limit=engine.MAX_RESULTS, repetition_strength=strength)
