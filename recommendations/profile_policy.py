"""User-scoped sampling policy. No journal writes, inference or shared state."""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError

PROFILE_POLICY_VERSION = 'profile-sampling.v1'
MIN_PROFILE_SCORE = Decimal('6.0')
MAX_PROFILE_SCORE = Decimal('10.0')
PROFILE_SCORE_STEP = Decimal('0.5')
PROFILE_SCORE_OPTIONS = tuple(MIN_PROFILE_SCORE + PROFILE_SCORE_STEP * n for n in range(9))
FIXED_EVALUATION_SCORE = Decimal('8.5')


def validate_profile_min_score(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number not in PROFILE_SCORE_OPTIONS:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise ValidationError('画像取样门槛必须为 6.0–10.0，步长 0.5。') from None
    return number


def default_score():
    return validate_profile_min_score(settings.RECOMMENDATION_PROFILE_SCORE_THRESHOLD)


def score_controls():
    return {'enabled': bool(settings.RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED),
            'default': default_score(), 'minimum': MIN_PROFILE_SCORE, 'maximum': MAX_PROFILE_SCORE,
            'step': PROFILE_SCORE_STEP, 'choices': list(PROFILE_SCORE_OPTIONS)}


def sampling_policy(preferences, *, preview_score=None, evaluation_baseline=False):
    configured = validate_profile_min_score(preferences['profile_min_score'])
    enabled = bool(settings.RECOMMENDATION_PROFILE_CUSTOM_THRESHOLD_ENABLED)
    threshold = configured if enabled else default_score()
    source = 'user_setting' if enabled else 'server_default'
    if evaluation_baseline:
        threshold, source = FIXED_EVALUATION_SCORE, 'fixed_evaluation_baseline'
    elif preview_score is not None:
        threshold, source = validate_profile_min_score(preview_score), 'unsaved_preview'
    return {'profile_policy_version': PROFILE_POLICY_VERSION, 'watch_status': 'completed',
            'minimum_score': threshold, 'configured_minimum_score': configured,
            'customization_enabled': enabled, 'source': source,
            'minimum_samples': settings.RECOMMENDATION_PROFILE_MIN_SAMPLES,
            'requires_safe_bangumi_identity': True, 'requires_usable_tags': True}


def context_score(context):
    # Frozen offline/synthetic contexts may predate sample_policy; serving
    # contexts always carry the effective policy, never a client-provided score.
    value = context.get('profile', {}).get('sample_policy', {}).get('minimum_score')
    return float(validate_profile_min_score(value) if value is not None else default_score())
