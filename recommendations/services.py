"""3A own-user profile and transactional explicit-preference storage only."""
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import APIException

from anime.models import UserAnimeRecord
from .features import feature_tags
from .resolution import resolved_records
from .models import RecommendationPreference
from .taxonomy import AVAILABLE_TAGS, MAX_PREFERENCE_TAGS, TAXONOMY_VERSION, canonical_tags

CONTRACT_VERSION = 'recommendations.3a.v1'


class PreferenceVersionConflict(APIException):
    status_code = 409
    default_detail = '偏好已发生变化，请重新读取后再提交。'
    default_code = 'preference_version_conflict'


def preference_contract(user):
    preference = RecommendationPreference.objects.filter(user=user).first()
    return {
        'contract_version': CONTRACT_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        'tags': canonical_tags(preference.tags) if preference else [],
        'version': preference.version if preference else 0,
        'updated_at': preference.updated_at if preference else None,
        'available_tags': list(AVAILABLE_TAGS),
    }


def profile_data(user):
    threshold = Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD))
    minimum = settings.RECOMMENDATION_PROFILE_MIN_SAMPLES
    records = resolved_records(user)
    high = [row for row in records if row['watch_status'] == UserAnimeRecord.WatchStatus.COMPLETED
            and row['personal_score'] is not None and row['personal_score'] >= threshold]
    diagnostics = {'high_score_record_count': len(high), 'linked_high_score_record_count': 0,
                   'pending_identity_count': 0, 'unavailable_identity_count': 0,
                   'stale_identity_count': 0, 'conflict_identity_count': 0}
    selected = {}
    for row in high:
        state = row['identity_state']
        if state == 'linked':
            diagnostics['linked_high_score_record_count'] += 1
            identity = row['resolved_bangumi_id']
            previous = selected.get(identity)
            if previous is None or row['personal_score'] > previous['personal_score']:
                selected[identity] = row
        elif state in ('unlinked', 'stale_link', 'identity_conflict'):
            diagnostics['pending_identity_count'] += 1
            diagnostics['stale_identity_count'] += int(state == 'stale_link')
            diagnostics['conflict_identity_count'] += int(state == 'identity_conflict')
        else:
            diagnostics['unavailable_identity_count'] += 1
    summary, seeds = Counter(), []
    for identity, row in sorted(selected.items()):
        source = row['catalog']
        tags = feature_tags(row['metadata']['tags'], (source['title_zh'], source['title_jp']))
        summary.update(tags)
        if tags:
            seeds.append({'bangumi_id': identity, 'title': source['title_zh'] or source['title_jp'],
                          'score': float(row['personal_score']), 'tags': tags})
    count = len(selected)
    readiness = ('needs_identity' if count < minimum and diagnostics['pending_identity_count']
                 else 'needs_more_samples' if count < minimum else 'needs_tags' if not summary else 'ready')
    # Counts and genre labels only: never serialize seed IDs, titles or reviews.
    contract = {
        'contract_version': CONTRACT_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        'eligible_sample_count': count, 'cold_start': count < minimum,
        'has_usable_tags': bool(summary),
        'readiness': readiness, 'diagnostics': diagnostics, 'identity_service_ready': records.schema_available,
        'tag_summary': [{'tag': tag, 'sample_count': number} for tag, number in
                        sorted(summary.items(), key=lambda pair: (-pair[1], AVAILABLE_TAGS.index(pair[0])))],
        'sample_policy': {'watch_status': 'completed', 'minimum_score': threshold,
                          'minimum_samples': minimum, 'requires_safe_bangumi_identity': True},
    }
    return contract, records, seeds


def profile_contract(user):
    return profile_data(user)[0]


@transaction.atomic
def patch_preferences(*, user, changes):
    # Lock the authenticated user BEFORE creating a preference row. This also
    # serializes the first two concurrent writes when there is no row to lock.
    get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
    preference = RecommendationPreference.objects.select_for_update().filter(user=user).first()
    current_version = preference.version if preference else 0
    if 'expected_version' in changes and changes['expected_version'] != current_version:
        raise PreferenceVersionConflict()
    current = canonical_tags(preference.tags) if preference else []
    if 'tags' in changes:
        updated = changes['tags']
    else:
        updated = canonical_tags([tag for tag in current if tag not in changes.get('remove_tags', [])]
                                 + changes.get('add_tags', []))
    if len(updated) > MAX_PREFERENCE_TAGS:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'tags': '增量更新后的标签不能超过 20 个。'})
    if updated != current:
        if preference is None:
            RecommendationPreference.objects.create(user=user, tags=updated, version=1)
        else:
            if preference.version >= 2_147_483_647:
                raise PreferenceVersionConflict('偏好版本达到上限，暂不能更新，请联系管理员。')
            preference.tags = updated
            preference.version += 1
            preference.save(update_fields=['tags', 'version', 'updated_at'])
    return preference_contract(user)
