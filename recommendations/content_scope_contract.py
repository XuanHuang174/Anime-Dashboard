"""N1 preparation ONLY. Not wired into serving routes; no account grants.

An NSFW source flag is neither an age check nor a content-release approval.
This locked contract deliberately cannot enable adult access via settings,
staff privileges, client assertions, preferences or raw catalog metadata.
"""
from dataclasses import asdict, dataclass

from django.utils.crypto import salted_hmac
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError

CONTRACT_VERSION = 'recommendations.content-scope.n1.v1'
POLICY_VERSION = 'content-scope.closed.v1'
SCOPES = ('safe', 'include_adult')


def contract_descriptor():
    # Fresh containers: callers cannot change subsequent policy responses.
    return {
        'contract_version': CONTRACT_VERSION,
        'policy_version': POLICY_VERSION,
        'phase': 'N1',
        'serving_integration': False,
        'adult_access_enabled': False,
        'eligibility_provider': 'unconfigured',
        'eligibility_version': 0,
        'selectable_scopes': ['safe'],
        'future_requested_scope': 'include_adult',
        'unknown_rating': 'always_excluded',
        'adult_cover': 'placeholder_only',
        'profile_sources': 'non_adult_only',
        'public_publication': 'independent_safe_policy',
        'automatic_grants': False,
        'external_private_data': False,
        'adult_release_approval': 'separate_review_required',
    }


@dataclass(frozen=True)
class ContentScopeDecision:
    contract_version: str
    policy_version: str
    requested_scope: str
    effective_scope: str
    allowed: bool
    reason_code: str
    eligibility_status: str
    eligibility_version: int
    subject_binding: str
    scope_revision: str

    def as_contract(self):
        return asdict(self)


def resolve_n1_scope(user, requested_scope='safe'):
    """Only the authenticated server user may be passed, not a request user ID.

    This is a contract prototype, not a hidden switch. N2 must replace the
    closed policy only after an authoritative eligibility/review design exists.
    A denied decision must become an explicit error, never silent adult fallback.
    """
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise NotAuthenticated('需要登录。')
    if requested_scope not in SCOPES:
        raise ValidationError({'content_scope': '未定义的内容范围。'})
    subject = salted_hmac(CONTRACT_VERSION + '.subject', str(user.pk), algorithm='sha256').hexdigest()
    allowed = requested_scope == 'safe'
    reason = 'safe_default' if allowed else 'adult_eligibility_unavailable'
    revision = salted_hmac(CONTRACT_VERSION + '.context',
        '|'.join((POLICY_VERSION, subject, requested_scope, 'safe', reason, '0')), algorithm='sha256').hexdigest()
    return ContentScopeDecision(CONTRACT_VERSION, POLICY_VERSION, requested_scope, 'safe',
                                allowed, reason, 'unavailable', 0, subject, revision)


def require_n1_scope(user, requested_scope='safe'):
    """Fail-closed integration helper; bindings are fingerprints, not grant tokens."""
    decision = resolve_n1_scope(user, requested_scope)
    if not decision.allowed:
        raise PermissionDenied({'code': decision.reason_code, 'detail': '当前未开放成人内容访问资格。'})
    return decision


class ContentScopeDecisionSerializer(serializers.Serializer):
    """Read-only envelope reserved for N2; does not alter existing V1 responses."""
    contract_version = serializers.ChoiceField(choices=(CONTRACT_VERSION,), read_only=True)
    policy_version = serializers.ChoiceField(choices=(POLICY_VERSION,), read_only=True)
    requested_scope = serializers.ChoiceField(choices=SCOPES, read_only=True)
    effective_scope = serializers.ChoiceField(choices=('safe',), read_only=True)
    allowed = serializers.BooleanField(read_only=True)
    reason_code = serializers.CharField(read_only=True)
    eligibility_status = serializers.ChoiceField(choices=('unavailable',), read_only=True)
    eligibility_version = serializers.IntegerField(read_only=True)
    subject_binding = serializers.CharField(read_only=True)
    scope_revision = serializers.CharField(read_only=True)
