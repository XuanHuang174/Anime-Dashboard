"""5A safe public-catalog audit. No private rows, reviews, downloads or repairs."""
from collections import Counter
from datetime import date
import hashlib
import json
import math
import re

from django.db.models import Count, Q

from .engine import MAX_CATALOG_ROWS
from .features import extract_genres, IGNORED_REASONS
from .models import RecommendationAnime

AUDIT_VERSION = 'recommendations.quality-audit.v1'
CATALOG_FIELDS = ('bangumi_id', 'title_zh', 'title_jp', 'tags', 'score', 'season', 'status_type', 'cover_url')


def score_state(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 'invalid_type'
    if not math.isfinite(value):
        return 'non_finite'
    if not 0 <= value <= 10:
        return 'out_of_range'
    return 'zero_ambiguous' if value == 0 else 'positive_public_rating'


def season_state(value, *, reference_year):
    if not isinstance(value, str) or not value.strip() or value.strip() in ('未定档', '未知', '待定'):
        return 'missing_year'
    match = re.fullmatch(r'(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?', value.strip())
    if not match:
        return 'invalid_format'
    year, month, day = match.groups()
    if not 1900 <= int(year) <= reference_year + 5:
        return 'year_needs_review'  # Flag only; future dates are not silently rewritten.
    try:
        date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return 'invalid_date'
    if not month:
        return 'year_only'
    return 'dated' if day else 'quarter' if int(month) in (1, 4, 7, 10) else 'non_quarter_month'


def audit_rows(rows, *, reference_year, example_limit=10):
    """Input must already be non-adult. Issue examples export IDs/codes, not titles."""
    counters = Counter(), Counter(), Counter(), Counter()
    issues, scores, seasons, genre_frequency = counters
    ignored = Counter({key: 0 for key in IGNORED_REASONS})
    genre_sizes, score_buckets = Counter(), Counter()
    type_counts, type_features = Counter(), {}
    seen, title_pairs = set(), Counter()
    examples = []
    fingerprint = hashlib.sha256()
    count = 0
    for row in rows:
        count += 1
        identity = row['bangumi_id']
        found = []
        if identity is None:
            found.append('missing_identity')
        elif type(identity) is not int or not 0 < identity <= 2_147_483_647:
            found.append('invalid_identity')
        elif identity in seen:
            found.append('duplicate_identity')
        if type(identity) is int:
            seen.add(identity)
        pair = (row['title_zh'].strip(), row['title_jp'].strip())
        if not any(pair):
            found.append('missing_title')
        else:
            title_pairs[pair] += 1  # Exact equality only, never infer series/sequels.
        features = extract_genres(row['tags'], titles=pair, base_source='shared_base_tags')
        ignored.update(features['ignored_counts'])
        genre_frequency.update(features['tags'])
        genre_sizes[len(features['tags'])] += 1
        work_type = row['status_type'].strip() or '(missing)'
        type_counts[work_type] += 1
        type_features.setdefault(work_type, Counter())['none' if not features['tags'] else 'one' if len(features['tags']) == 1 else 'two_or_more'] += 1
        if not features['tags']:
            found.append('no_effective_genres')
            found.append('raw_tags_missing' if not row['tags'].strip()
                         else 'unmapped_tags_present' if features['ignored_counts']['unknown']
                         else 'only_non_genre_tags')
        elif len(features['tags']) == 1:
            found.append('sparse_genres_one')
        rating, season = score_state(row['score']), season_state(row['season'], reference_year=reference_year)
        scores[rating] += 1; seasons[season] += 1
        if rating != 'positive_public_rating':
            found.append('score_' + rating)
        else:
            score_buckets[str(int(row['score']))] += 1
            if row['score'] >= 9.5:
                found.append('high_public_score_needs_vote_count_review')
        if season in ('missing_year', 'invalid_format', 'invalid_date', 'year_needs_review'):
            found.append('season_' + season)
        if not row['status_type'].strip():
            found.append('missing_type')
        if not row['cover_url']:
            found.append('missing_cover')
        issues.update(found)
        if found and len(examples) < example_limit:
            examples.append({'bangumi_id': identity if type(identity) is int else None, 'issues': found})
        # Hash only; never export raw tags, URL query strings or titles.
        safe = {key: row[key] for key in CATALOG_FIELDS}
        if rating in ('invalid_type', 'non_finite'):
            safe['score'] = rating
        fingerprint.update(json.dumps(safe, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
        fingerprint.update(b'\n')
    return {'audited_safe_rows': count, 'reference_year': reference_year, 'catalog_fingerprint': fingerprint.hexdigest(),
        'issue_counts': dict(sorted(issues.items())), 'score_states': dict(sorted(scores.items())),
        'season_states': dict(sorted(seasons.items())),
        'effective_genre_count_histogram': {str(key): value for key, value in sorted(genre_sizes.items())},
        'genre_document_frequency': dict(sorted(genre_frequency.items(), key=lambda item: (-item[1], item[0]))),
        'ignored_tag_counts': dict(ignored), 'positive_score_buckets': dict(sorted(score_buckets.items())),
        'type_distribution': dict(sorted(type_counts.items())),
        'genre_availability_by_type': {key: dict(value) for key, value in sorted(type_features.items())},
        'exact_duplicate_title_pair_groups': sum(value > 1 for value in title_pairs.values()),
        'examples': examples, 'example_limit': example_limit,
        'rating_semantics': {'public_score_is_preference_probability': False,
            'vote_count_available': False, 'confidence_correction_performed': False,
            'zero_score_means': 'unrated_or_source_status_unknown_not_a_confirmed_low_rating',
            'high_score_flag_means': 'needs_review_not_proven_bad_data'},
        'identity_semantics': 'Bangumi ID only; exact duplicate names do not establish a series relationship'}


def catalog_snapshot():
    groups = RecommendationAnime.objects.aggregate(total=Count('pk'),
        non_adult=Count('pk', filter=Q(nsfw=False)), adult=Count('pk', filter=Q(nsfw=True)),
        unknown=Count('pk', filter=Q(nsfw__isnull=True)),
        safe_missing_identity=Count('pk', filter=Q(nsfw=False, bangumi_id__isnull=True)))
    # Missing-ID non-adult works are audited too, but never enter ranking features.
    query = RecommendationAnime.objects.filter(nsfw=False).order_by('bangumi_id', 'pk')
    size = query.count()
    if size > MAX_CATALOG_ROWS:
        raise ValueError('Safe catalog exceeds the V1 bounded limit; audit not silently truncated')
    rows = list(query.values(*CATALOG_FIELDS).iterator(chunk_size=500))
    return groups, rows
