"""Own-user contracts: independent preferences and 4A read-only extraction."""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import APIException, NotAuthenticated

from .extraction import extract_profile, EXTRACTION_CONTRACT_VERSION
from .cold_start import cold_start_contract
from .resolution import resolved_records
from .models import RecommendationPreference
from .taxonomy import MAX_PREFERENCE_TAGS, TAXONOMY_VERSION, canonical_tags, AVAILABLE_TAGS
from .profile_policy import PROFILE_POLICY_VERSION, default_score, sampling_policy, score_controls, validate_profile_min_score

CONTRACT_VERSION = 'recommendations.3a.v1'


class PreferenceVersionConflict(APIException):
    status_code = 409
    default_detail = '偏好已发生变化，请重新读取后再提交。'
    default_code = 'preference_version_conflict'


def preference_contract(user):
    if not getattr(user, 'is_authenticated', False):
        raise NotAuthenticated()
    preference = RecommendationPreference.objects.filter(user=user).first()
    result = {
        'contract_version': CONTRACT_VERSION, 'taxonomy_version': TAXONOMY_VERSION,
        'tags': canonical_tags(preference.tags) if preference else [],
        'version': preference.version if preference else 0,
        'updated_at': preference.updated_at if preference else None,
        'available_tags': list(AVAILABLE_TAGS),
        'profile_min_score': preference.profile_min_score if preference else default_score(),
        'profile_policy_version': PROFILE_POLICY_VERSION,
        'profile_min_score_control': score_controls(),
    }
    result['effective_profile_min_score'] = sampling_policy(result)['minimum_score']
    return result


def profile_data(user, *, preview_score=None, evaluation_baseline=False):
    if not getattr(user, 'is_authenticated', False):
        raise NotAuthenticated()
    preferences = preference_contract(user)
    policy = sampling_policy(preferences, preview_score=preview_score, evaluation_baseline=evaluation_baseline)
    threshold, minimum = policy['minimum_score'], policy['minimum_samples']
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
        'sample_policy': policy,
        'extraction': {'contract_version': EXTRACTION_CONTRACT_VERSION,
                       'rule_version': extracted['rule_version'], 'taxonomy_version': TAXONOMY_VERSION,
                       'explicit_preferences': preferences,
                       'preference_policy': {'maximum_tags': MAX_PREFERENCE_TAGS,
                                             'suggested_initial_min': 3, 'suggested_initial_max': 5,
                                             'maximum_observed_topics': 8,
                                             'explicit_preferences_are_hard_filters': False,
                                             'common_topics_require_confirmation': True},
                       **{key: extracted[key] for key in ('trusted_profile', 'common_topics',
                                                        'record_stats', 'ignored_tag_counts')}},
    }
    contract['cold_start_state'] = cold_start_contract(extracted,
        preference_tags=contract['extraction']['explicit_preferences']['tags'], minimum=minimum)
    return contract, records, seeds


def profile_contract(user):
    return profile_data(user)[0]


def preview_profile(user, score):
    profile = profile_data(user, preview_score=score)[0]
    return {'contract_version': 'recommendations.profile-preview.v1', 'is_preview': True,
            'saved': False, 'profile_min_score': score,
            'preference_version': profile['extraction']['explicit_preferences']['version'],
            **{key: profile[key] for key in ('sample_policy', 'eligible_sample_count', 'usable_sample_count',
                'cold_start', 'cold_start_state', 'readiness', 'identity_service_ready', 'diagnostics')}}


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
    current_score = preference.profile_min_score if preference else default_score()
    score = validate_profile_min_score(changes.get('profile_min_score', current_score))
    if 'profile_min_score' in changes and 'expected_version' not in changes:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'expected_version': '修改画像门槛必须携带当前偏好版本。'})
    if updated != current or score != current_score:
        if preference is None:
            RecommendationPreference.objects.create(user=user, tags=updated, profile_min_score=score, version=1)
        else:
            if preference.version >= 2_147_483_647:
                raise PreferenceVersionConflict('偏好版本达到上限，暂不能更新，请联系管理员。')
            preference.tags = updated
            preference.profile_min_score = score
            preference.profile_policy_version = PROFILE_POLICY_VERSION
            preference.version += 1
            preference.save(update_fields=['tags', 'profile_min_score', 'profile_policy_version', 'version', 'updated_at'])
    return preference_contract(user)
