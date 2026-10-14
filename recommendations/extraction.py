"""4A pure extraction from resolved OWN rows. Observation is never preference."""
from collections import Counter

from anime.models import UserAnimeRecord
from .features import extract_genres, FEATURE_RULE_VERSION, FEATURE_SOURCES, IGNORED_REASONS
from .taxonomy import AVAILABLE_TAGS

EXTRACTION_CONTRACT_VERSION = 'recommendations.extraction.v1'


def _safe(row):
    catalog, identity = row.get('catalog'), row.get('resolved_bangumi_id')
    return (row.get('identity_state') == 'linked' and catalog is not None
            and catalog.get('nsfw') is False and type(identity) is int
            and 0 < identity <= 2_147_483_647 and catalog.get('bangumi_id') == identity)


def _genres(row, taxonomy_version=None):
    meta, catalog = row['metadata'], row['catalog']
    return extract_genres(meta['tags'], row.get('custom_tags', ()),
        titles=(meta['title_zh'], meta['title_jp'], catalog['title_zh'], catalog['title_jp']),
        base_source='private_base_tags' if row['metadata_initialized'] else 'shared_base_tags', taxonomy_version=taxonomy_version)


def _summary(features):
    counts, sources = Counter(), {}
    for value in features:
        for tag in value['tags']:
            counts[tag] += 1
            origins = sources.setdefault(tag, Counter())
            origins.update(value['sources'][tag])
    return [{'tag': tag, 'sample_count': counts[tag],
             'source_work_counts': {source: sources[tag][source] for source in FEATURE_SOURCES}}
            for tag in sorted(counts, key=lambda tag: (-counts[tag], AVAILABLE_TAGS.index(tag)))]


def extract_profile(records, *, threshold, minimum, taxonomy_version=None):
    """Return bounded aggregate contract plus request-local trusted evidence seeds.

    Do not attach identities, mutate rows, parse reviews, fetch catalog genres,
    copy observations into preferences or cache private state. Highest eligible
    PERSONAL score wins duplicates (lowest PK breaks ties), retaining 3A policy.
    """
    records = tuple(records)  # Stable collection; never mutate individual row/tag objects.
    high = [row for row in records if row['watch_status'] == UserAnimeRecord.WatchStatus.COMPLETED
            and row['personal_score'] is not None and row['personal_score'] >= threshold]
    diagnostics = {'high_score_record_count': len(high), 'linked_high_score_record_count': 0,
                   'pending_identity_count': 0, 'unavailable_identity_count': 0,
                   'stale_identity_count': 0, 'conflict_identity_count': 0}
    selected = {}
    for row in high:
        state = row['identity_state']
        if _safe(row):
            diagnostics['linked_high_score_record_count'] += 1
            identity = row['resolved_bangumi_id']
            previous = selected.get(identity)
            if previous is None or (row['personal_score'], -row['pk']) > (previous['personal_score'], -previous['pk']):
                selected[identity] = row
        elif state in ('unlinked', 'stale_link', 'identity_conflict'):
            diagnostics['pending_identity_count'] += 1
            diagnostics['stale_identity_count'] += int(state == 'stale_link')
            diagnostics['conflict_identity_count'] += int(state == 'identity_conflict')
        else:
            diagnostics['unavailable_identity_count'] += 1

    trusted, seeds = [], []
    ignored = Counter({reason: 0 for reason in IGNORED_REASONS})
    for identity, row in sorted(selected.items()):
        features = _genres(row, taxonomy_version)
        trusted.append(features)
        ignored.update(features['ignored_counts'])
        if features['tags']:
            source = row['catalog']
            seeds.append({'bangumi_id': identity, 'title': source['title_zh'] or source['title_jp'],
                          'score': float(row['personal_score']), 'tags': features['tags'],
                          # Internal provenance only; private PKs never enter the public cache/profile.
                          'record_id': row['pk'], 'feature_sources': features['sources']})

    # Unrated/low-score/other-state observations are isolated from seeds/vector.
    # Choose the first PK per identity for a deterministic descriptive snapshot.
    weak = {}
    safe_work_ids = set()
    for row in sorted(records, key=lambda row: row['pk']):
        if not _safe(row):
            continue  # No genre extraction for adult/unknown/unresolved data.
        identity = row['resolved_bangumi_id']
        safe_work_ids.add(identity)
        if identity not in selected and identity not in weak:
            weak[identity] = _genres(row, taxonomy_version)
            ignored.update(weak[identity]['ignored_counts'])

    count, usable = len(selected), len(seeds)
    readiness = ('needs_identity' if count < minimum and diagnostics['pending_identity_count']
                 else 'needs_more_samples' if count < minimum
                 else 'needs_tags' if usable < minimum else 'ready')
    return {
        'eligible_sample_count': count, 'usable_sample_count': usable,
        'cold_start': usable < minimum, 'has_usable_tags': bool(usable), 'readiness': readiness,
        'diagnostics': diagnostics,
        'trusted_profile': {'eligible_work_count': count, 'usable_work_count': usable,
                            'ready': usable >= minimum, 'tag_summary': _summary(trusted)},
        'common_topics': {'work_count': len(weak),
                          'feature_work_count': sum(bool(value['tags']) for value in weak.values()),
                          'requires_confirmation': True, 'used_for_recommendation': False,
                          'tag_summary': _summary(weak.values())},
        # Own aggregate diagnostics, not features; no unsafe names/labels/ratings are exposed.
        'record_stats': {'journal_record_count': len(records),
                         'rated_record_count': sum(row['personal_score'] is not None for row in records),
                         'unrated_record_count': sum(row['personal_score'] is None for row in records),
                         'completed_record_count': sum(row['watch_status'] == 'completed' for row in records),
                         'safe_linked_work_count': len(safe_work_ids)},
        'ignored_tag_counts': dict(ignored),
        'rule_version': FEATURE_RULE_VERSION,
    }, seeds
