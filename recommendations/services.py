"""Own-user contracts: independent preferences and 4A read-only extraction."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import APIException, NotAuthenticated

from .extraction import extract_profile, EXTRACTION_CONTRACT_VERSION
from .cold_start import cold_start_contract
from .resolution import resolved_records
from .models import RecommendationPreference
from .taxonomy import MAX_PREFERENCE_TAGS, TAXONOMY_VERSION, canonical_tags, AVAILABLE_TAGS

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
    if not getattr(user, 'is_authenticated', False):
        raise NotAuthenticated()
    threshold = Decimal(str(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD))
    minimum = settings.RECOMMENDATION_PROFILE_MIN_SAMPLES
    records = resolved_records(user)
    extracted, seeds = extract_profile(records, threshold=threshold, minimum=minimum)
    # Counts and genre labels only: never serialize seed IDs, titles or reviews.
    contract = {
        'contract_version': CONTRACT_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        **{key: extracted[key] for key in ('eligible_sample_count', 'usable_sample_count', 'cold_start',
                                         'has_usable_tags', 'readiness', 'diagnostics')},
        'identity_service_ready': records.schema_available,
        'tag_summary': [{'tag': item['tag'], 'sample_count': item['sample_count']}
                        for item in extracted['trusted_profile']['tag_summary']],
        'sample_policy': {'watch_status': 'completed', 'minimum_score': threshold,
                          'minimum_samples': minimum, 'requires_safe_bangumi_identity': True,
                          'requires_usable_tags': True},
        'extraction': {'contract_version': EXTRACTION_CONTRACT_VERSION,
                       'rule_version': extracted['rule_version'], 'taxonomy_version': TAXONOMY_VERSION,
                       'explicit_preferences': preference_contract(user),
                       **{key: extracted[key] for key in ('trusted_profile', 'common_topics',
                                                        'record_stats', 'ignored_tag_counts')}},
    }
    contract['cold_start_state'] = cold_start_contract(extracted,
        preference_tags=contract['extraction']['explicit_preferences']['tags'], minimum=minimum)
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
