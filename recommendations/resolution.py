"""Own-record read contracts; never attaches identities during GET."""
from anime.models import UserAnimeRecord
from django.db import DatabaseError, transaction
from .matching import catalog_fingerprint, effective_metadata, record_fingerprint
from .models import RecommendationAnime

RECORD_FIELDS = ('pk', 'user_id', 'anime_id', 'watch_status', 'personal_score', 'custom_tags', 'metadata_initialized',
                 'private_chinese_title', 'private_japanese_title', 'private_airing_period', 'private_base_tags',
                 'anime__chinese_title', 'anime__japanese_title', 'anime__airing_period', 'anime__base_tags', 'anime__bgm_id')
LINK_FIELDS = ('recommendation_identity__catalog_anime__bangumi_id', 'recommendation_identity__record_fingerprint',
               'recommendation_identity__catalog_fingerprint', 'recommendation_identity__active', 'recommendation_identity__version')


class ResolvedRecords(list):
    schema_available = True


def record_row(record):
    return UserAnimeRecord.objects.filter(pk=record.pk).values(*RECORD_FIELDS).get()


def resolved_records(user, *, allowed_adult_ids=(), include_profile=True, max_rows=None):
    query = UserAnimeRecord.objects.filter(user=user).order_by('pk')
    if max_rows is not None:
        query = query[:max_rows + 1]
    fields = RECORD_FIELDS if include_profile else tuple(field for field in RECORD_FIELDS if field not in (
        'personal_score', 'custom_tags', 'private_base_tags', 'anime__base_tags'))
    try:
        with transaction.atomic():
            rows = ResolvedRecords(query.values(*fields, *LINK_FIELDS))
    except DatabaseError as exc:
        # Rolling deploy bridge ONLY for the new alias table. Other DB faults stay 503.
        cause = exc.__cause__
        missing = (getattr(cause, 'sqlstate', None) == '42P01' or getattr(cause, 'pgcode', None) == '42P01'
                   or 'no such table:' in str(exc))
        if not missing or 'recommendations_recommendationrecordidentity' not in str(exc):
            raise
        rows = ResolvedRecords(query.values(*fields))
        rows.schema_available = False
        for row in rows:
            row.update({field: None for field in LINK_FIELDS})
    if max_rows is not None and len(rows) > max_rows:
        from .engine import RecommendationUnavailable
        raise RecommendationUnavailable('手账身份数量超出处理上限。')
    if not include_profile:
        for row in rows:
            # Identity fingerprints use titles/quarter only, never these tags.
            row.update(private_base_tags=[], anime__base_tags=[])
    ids = {value for row in rows for value in (row['anime__bgm_id'], row[LINK_FIELDS[0]]) if value}
    catalog = {row['bangumi_id']: row for row in RecommendationAnime.objects.filter(bangumi_id__in=ids)
               .values('pk', 'bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type', 'nsfw')}
    for row in rows:
        direct, alias = row['anime__bgm_id'], row[LINK_FIELDS[0]]
        active = row['recommendation_identity__active']
        row['metadata'] = effective_metadata(row)
        row['identity_version'] = row['recommendation_identity__version'] or 0
        row['identity_state'], row['catalog'], row['resolved_bangumi_id'] = 'unlinked', None, None
        target_id = direct or alias
        if direct and alias and active and direct != alias:
            row['identity_state'] = 'identity_conflict'
            continue
        if not direct and alias:
            if (not active or row['recommendation_identity__record_fingerprint'] != record_fingerprint(row)
                or alias not in catalog or row['recommendation_identity__catalog_fingerprint'] != catalog_fingerprint(catalog[alias])):
                row['identity_state'] = 'stale_link'
                continue
        if not target_id:
            continue
        target = catalog.get(target_id)
        if target is None:
            row['identity_state'] = 'not_in_catalog'
        elif target['nsfw'] is not False and not (target['nsfw'] is True and target_id in allowed_adult_ids):
            row['identity_state'] = 'unsafe_or_unknown'
        else:
            row.update(identity_state='linked', catalog=target, resolved_bangumi_id=target_id)
    return rows
