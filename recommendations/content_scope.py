"""N2-2 CLOSED serving boundary. No flag, role or eligibility row enables adult data.

The opaque revision is an invalidation hint, NOT a grant token. A future release
needs explicit manual access authorization AND per-work release review. Keep safe_catalog
and non-adult profile extraction unchanged; do not widen queries here.
"""
import hmac
import re

from django.contrib.auth import get_user_model
from django.db.models import IntegerField, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from .models import RecommendationContentReviewState
from .content_review import POLICY_VERSION as RELEASE_POLICY_VERSION

CONTRACT_VERSION = 'recommendations.content-scope.n2-2.v1'
POLICY_VERSION = 'content-scope.closed.v3'
VERSION_KEYS = ('content_scope_contract_version', 'content_scope_policy_version',
                'content_release_policy_version', 'content_scope', 'eligibility_version', 'content_review_version', 'scope_revision')


def scope_query(query):
    """Remove only the declared scope; leave all other strict validation intact."""
    clean = query.copy()
    if hasattr(query, 'getlist') and len(query.getlist('content_scope')) > 1:
        raise ValidationError({'content_scope': '内容范围不能重复。'})
    requested = query.get('content_scope', 'safe')
    if requested not in ('safe', 'include_adult'):
        raise ValidationError({'content_scope': '未定义的内容范围。'})
    # Reject BEFORE catalog/qualification lookup, irrespective of roles or proof.
    if requested != 'safe':
        raise PermissionDenied({'code': 'adult_scope_closed', 'detail': '成人内容访问尚未开放。'})
    clean.pop('content_scope', None)
    return clean


def require_scope(user, requested_scope='safe'):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise NotAuthenticated()
    scope_query({'content_scope': requested_scope})
    # One owner-only LEFT JOIN; no journal, private text, audit or proof material.
    prefix = 'recommendation_content_eligibility__'
    row = get_user_model().objects.filter(pk=user.pk).annotate(_content_review_version=Coalesce(Subquery(
        RecommendationContentReviewState.objects.filter(pk=1).values('version')[:1]),Value(0),output_field=IntegerField())).values(
        'is_active', '_content_review_version', *(prefix + field for field in ('status', 'eligibility_version', 'expires_at',
                                                  'assurance_valid_until', 'authorization_source'))).first()
    if not row or not row['is_active']:
        raise PermissionDenied('账号不可用。')
    status = row[prefix + 'status'] or 'not_requested'
    version = row[prefix + 'eligibility_version'] or 0
    expiry, assurance_expiry = row[prefix + 'expires_at'], row[prefix + 'assurance_valid_until']
    expired = status == 'active' and any(value is not None and value <= timezone.now()
                                        for value in (expiry, assurance_expiry))
    state = [user.pk, CONTRACT_VERSION, POLICY_VERSION, RELEASE_POLICY_VERSION, 'safe',
             status, version, str(expiry), str(assurance_expiry), bool(expired), row['_content_review_version'],
             row[prefix + 'authorization_source'], 'manual-authorization-m1', 'adult-serving-disabled']
    revision = salted_hmac(CONTRACT_VERSION, repr(state), algorithm='sha256').hexdigest()
    return {'content_scope_contract_version': CONTRACT_VERSION,
            'content_scope_policy_version': POLICY_VERSION,
            'content_release_policy_version': RELEASE_POLICY_VERSION,
            'content_scope': 'safe', 'eligibility_version': version,
            'content_review_version':row['_content_review_version'], 'scope_revision': revision}


def ensure_scope(user, expected):
    from .engine import RecommendationSnapshotChanged
    current = require_scope(user)
    if expected != current:
        raise RecommendationSnapshotChanged('内容范围或资格已更新，请重新加载推荐。')
    return current


def check_precondition(revision, current):
    """Optional write/read precondition for future clients; never authorizes scope."""
    if revision is None:
        return
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValidationError('内容范围上下文格式无效。')
    from .engine import RecommendationSnapshotChanged
    if not hmac.compare_digest(revision, current['scope_revision']):
        raise RecommendationSnapshotChanged('内容范围或资格已更新，请重新加载推荐。')
