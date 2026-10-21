"""Aggregate public fields only. Never grants access, publishes media or writes DB."""
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection

from .content_scope_contract import contract_descriptor
from .features import feature_tags, FEATURE_RULE_VERSION
from .models import RecommendationAnime
from .quality_readonly import readonly_snapshot
from .taxonomy import TAXONOMY_VERSION


def summarize_public_rows(rows, hosted_domain):
    buckets = {key: Counter() for key in ('non_adult', 'adult_flagged', 'unknown')}
    hosted_domain = hosted_domain.strip().lower()
    for row in rows:
        key = 'non_adult' if row['nsfw'] is False else 'adult_flagged' if row['nsfw'] is True else 'unknown'
        counts = buckets[key]
        counts['total'] += 1
        identity = bool(row['bangumi_id'])
        genres = feature_tags(row['tags'], (row['title_zh'], row['title_jp']))
        counts['with_identity'] += identity
        counts['with_raw_tags'] += bool(row['tags'].strip())
        counts['with_valid_genres'] += bool(genres)
        counts['with_identity_and_genres'] += identity and bool(genres)
        counts['with_source_cover_reference'] += bool(row['source_cover_url'])
        counts['title_over_journal_limit'] += len(row['title_zh'] or row['title_jp']) > 200
        if not row['cover_url']:
            counts['cover_empty'] += 1
        else:
            try:
                url = urlsplit(row['cover_url'])
                trusted = (bool(hosted_domain) and url.scheme == 'https'
                           and url.netloc.lower() == hosted_domain and not url.username and not url.password
                           and url.path == f"/covers/{row['bangumi_id']}.jpg")
            except ValueError:
                trusted = False
            counts['cover_configured_host_reference' if trusted else 'cover_other_reference'] += 1
    fields = ('total', 'with_identity', 'with_raw_tags', 'with_valid_genres', 'with_identity_and_genres',
              'with_source_cover_reference', 'title_over_journal_limit', 'cover_empty',
              'cover_configured_host_reference', 'cover_other_reference')
    return {key: {field: bucket[field] for field in fields} for key, bucket in buckets.items()}


def audit_public_content_scope():
    if connection.vendor != 'postgresql':
        raise ValueError('N1 public audit requires enforced read-only PostgreSQL; no fallback')
    fields = ('nsfw', 'bangumi_id', 'tags', 'title_zh', 'title_jp', 'cover_url', 'source_cover_url')
    with readonly_snapshot(timeout_ms=60000):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('transaction_read_only')")
            if cursor.fetchone()[0] != 'on':
                raise ValueError('Read-only transaction required')
        counts = summarize_public_rows(RecommendationAnime.objects.order_by('pk').values(*fields).iterator(
            chunk_size=2000), getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', ''))
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'audit_version': 'content-scope-audit.n1.v1',
        'public_only': True, 'database_read_only': True,
        'private_rows_read': False, 'network_media_requests': 0,
        'feature_rule_version': FEATURE_RULE_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        'policy': contract_descriptor(), 'catalog_counts': counts,
        'interpretation': {
            'classification_source': 'imported_nsfw_field_not_age_verification',
            'row_level_source_proof': 'not_stored_in_current_schema',
            'adult_flag_is_release_approval': False,
            'cover_reference_is_accessibility_check': False,
            'counts_are_personal_recommendation_counts': False,
        },
        'release_blockers': ['eligibility_provider_unconfigured', 'content_release_review_required',
                             'restricted_asset_delivery_not_implemented', 'N2_serving_integration_not_implemented'],
    }
