"""Public-catalog option ordering ONLY; never used by profiles or ranking."""
from collections import Counter
import hashlib

from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.db.models import Count, StringAgg, TextField, Value
from django.db.models.functions import Cast, Concat

from .features import feature_tags, FEATURE_RULE_VERSION
from .identity import safe_catalog
from .public_fingerprint import NativeSHA256, public_feature_expression, public_feature_fingerprint
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION

OPTION_ORDER_VERSION = 'safe-catalog-topic-frequency.v1'
MAX_ROWS = 50000
CACHE_TTL = 300


def ordered_tags(counts):
    # Do not mutate the canonical vocabulary: it also defines deterministic
    # extraction, preference serialization and existing ranking tie-breaks.
    positions = {tag: index for index, tag in enumerate(AVAILABLE_TAGS)}
    return sorted(AVAILABLE_TAGS, key=lambda tag: (-counts[tag], positions[tag]))


def public_counts():
    """Exact input revision on each read, bounded cold build, public counts only.

    The digest catches direct QuerySet.update() and safety changes even without
    updated_at changes. Warm reads transfer only one digest/count, not all tags.
    A savepoint keeps optional statistics failures from poisoning a saved PATCH.
    """
    with transaction.atomic():
        catalog = safe_catalog().filter(bangumi_id__gt=0, bangumi_id__lte=2_147_483_647)
        identity = Concat(Cast('bangumi_id', TextField()), Value(':'), public_feature_expression(),
                          output_field=TextField())
        revision = catalog.aggregate(total=Count('pk'), digest=NativeSHA256(
            StringAgg(identity, delimiter=Value('|'), order_by='bangumi_id')))
        if revision['total'] > MAX_ROWS:
            raise ValueError('Public catalog exceeds option-count budget')
        digest = revision['digest'] or hashlib.sha256(b'').hexdigest()
        key = ':'.join(('recommendations.preference-options', OPTION_ORDER_VERSION,
                        TAXONOMY_VERSION, FEATURE_RULE_VERSION, digest))
        try:
            cached = cache.get(key)
        except Exception:
            cached = None
        if (isinstance(cached, dict) and cached.get('total') == revision['total']
                and isinstance(cached.get('counts'), dict) and set(cached['counts']) == set(AVAILABLE_TAGS)
                and all(type(value) is int and 0 <= value <= revision['total'] for value in cached['counts'].values())):
            return cached['counts'], digest
        rows = list(catalog.order_by('bangumi_id').values(
            'bangumi_id', 'tags', 'title_zh', 'title_jp')[:MAX_ROWS + 1])
        actual = hashlib.sha256('|'.join(f"{row['bangumi_id']}:{public_feature_fingerprint(row)}"
                                       for row in rows).encode('utf-8')).hexdigest()
        if len(rows) != revision['total'] or actual != digest:
            raise ValueError('Public catalog changed while counting options')
        counts = Counter()
        for row in rows:
            counts.update(feature_tags(row['tags'], (row['title_zh'], row['title_jp'])))
        counts = {tag: counts[tag] for tag in AVAILABLE_TAGS}
        try:
            cache.set(key, {'total': len(rows), 'counts': counts}, timeout=CACHE_TTL)
        except Exception:
            pass  # Fresh counts remain usable; no global cache purge or DB write.
        return counts, digest


def with_ordered_options(preference):
    result = dict(preference)
    try:
        counts, revision = public_counts()
    except (DatabaseError, ValueError, TypeError):
        # Counts are optional presentation data, never reinterpret a successful
        # preference save as failure or invent zero counts when unavailable.
        return {**result, 'option_order_status': 'unavailable'}
    return {**result, 'available_tags': ordered_tags(counts), 'available_tag_counts': counts,
            'option_order_version': OPTION_ORDER_VERSION, 'option_order_status': 'ready',
            'option_catalog_revision': revision}
