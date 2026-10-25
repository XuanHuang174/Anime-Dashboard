"""Independent series rollout: never narrow the already released exploration modes."""
from django.conf import settings
from .engine import RecommendationUnavailable


def canary_users():
    values = getattr(settings, 'RECOMMENDATION_SERIES_CANARY_USERS', [])
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in values):
        return None
    return tuple(sorted(set(values)))


def available(user):
    users = canary_users()
    return (getattr(settings, 'RECOMMENDATION_SERIES_ENABLED', False) is True and users is not None
            and user is not None and user.is_authenticated and user.is_active
            and (not users or user.get_username() in users))


def require_access(user):
    if not available(user):
        raise RecommendationUnavailable({'code': 'series_disabled', 'detail': '系列功能尚未对当前账号开放。'})
