"""3A own-user profile and transactional explicit-preference storage only."""
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Subquery
from rest_framework.exceptions import APIException

from anime.models import UserAnimeRecord
from .identity import safe_catalog
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


def profile_contract(user):
    threshold = Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD))
    minimum = settings.RECOMMENDATION_PROFILE_MIN_SAMPLES
    records = UserAnimeRecord.objects.filter(
        user=user, watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
        personal_score__gte=threshold,
        anime__bgm_id__in=Subquery(safe_catalog().order_by().values('bangumi_id')),
    ).order_by().values('anime__bgm_id', 'metadata_initialized', 'private_base_tags', 'anime__base_tags')
    seen, summary = set(), Counter()
    for row in records:
        if row['anime__bgm_id'] in seen:
            continue
        seen.add(row['anime__bgm_id'])
        tags = row['private_base_tags'] if row['metadata_initialized'] else row['anime__base_tags']
        summary.update(canonical_tags(tags))
    count = len(seen)
    # Counts and genre labels only: never serialize seed IDs, titles or reviews.
    return {
        'contract_version': CONTRACT_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        'eligible_sample_count': count, 'cold_start': count < minimum,
        'has_usable_tags': bool(summary),
        'readiness': 'needs_more_samples' if count < minimum else 'needs_tags' if not summary else 'ready',
        'tag_summary': [{'tag': tag, 'sample_count': number} for tag, number in
                        sorted(summary.items(), key=lambda pair: (-pair[1], AVAILABLE_TAGS.index(pair[0])))],
        'sample_policy': {'watch_status': 'completed', 'minimum_score': threshold,
                          'minimum_samples': minimum, 'requires_safe_bangumi_identity': True},
    }


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
