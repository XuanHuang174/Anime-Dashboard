"""M2 explicit request scope. Safe is always default; no role bypasses either gate."""
import hashlib
import hmac
import json
import re
from django.contrib.auth import get_user_model
from django.db.models import IntegerField, Subquery, Value, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from .models import RecommendationAnime, RecommendationContentReviewState, RecommendationContentReview
from .content_review import POLICY_VERSION as RELEASE_POLICY_VERSION, catalog_revision
from .serving_control import control_state, runtime_enabled
from .adult_policy import GENERAL_SCOPE_POLICY, general_adult_boundary, self_service_enabled

CONTRACT_VERSION = 'recommendations.content-scope.n2-2.v1'
POLICY_VERSION = 'content-scope.controlled.m2.v1'
VERSION_KEYS = ('content_scope_contract_version', 'content_scope_policy_version',
    'content_release_policy_version', 'content_scope', 'eligibility_version', 'content_review_version',
    'serving_control_version', 'release_revision', 'scope_valid_until', 'scope_revision')


def scope_query(query):
    clean = query.copy()
    if hasattr(query, 'getlist') and len(query.getlist('content_scope')) > 1:
        raise ValidationError({'content_scope': '内容范围不能重复。'})
    requested = query.get('content_scope', 'safe')
    if requested not in ('safe', 'include_adult'):
        raise ValidationError({'content_scope': '未定义的内容范围。'})
    if requested != 'safe' and not runtime_enabled():
        raise PermissionDenied({'code': 'adult_scope_closed', 'detail': 'NSFW 推荐尚未开放。'})
    clean.pop('content_scope', None)
    return clean


def release_snapshot():
    """Bulk CURRENT content/review read. No N+1, inference, media fetch or user data."""
    now = timezone.now()
    rows = list(RecommendationContentReview.objects.select_related('anime').filter(
        status='approved', policy_version=RELEASE_POLICY_VERSION, expires_at__gt=now,
        exclusions_confirmed=True, anime__nsfw=True, anime__bangumi_id__isnull=False)
        .exclude(evidence_reference='').order_by('anime__bangumi_id')[:5001])
    if len(rows) > 5000:
        from .engine import RecommendationUnavailable
        raise RecommendationUnavailable()
    valid = [r for r in rows if general_adult_boundary(r.anime)
             and r.catalog_revision == catalog_revision(r.anime)]
    revision = hashlib.sha256(json.dumps([(r.anime.bangumi_id, r.review_version,
        r.catalog_revision, r.expires_at.isoformat()) for r in valid], separators=(',', ':')).encode()).hexdigest()
    return {r.anime.bangumi_id for r in valid}, revision, min((r.expires_at for r in valid), default=None)


def catalog_for_scope(scope=None):
    from .identity import safe_catalog
    if not scope or scope.get('content_scope') == 'safe':
        return safe_catalog()
    if scope.get('content_scope') != 'include_adult' or not control_state()['effective_enabled']:
        raise PermissionDenied({'code':'adult_scope_closed', 'detail':'NSFW 推荐已关闭。'})
    identities, revision, _ = release_snapshot()
    if revision != scope.get('release_revision'):
        from .engine import RecommendationSnapshotChanged
        raise RecommendationSnapshotChanged('作品审核已变化，请重新加载。')
    return RecommendationAnime.objects.filter(bangumi_id__isnull=False).filter(
        Q(nsfw=False) | Q(nsfw=True, bangumi_id__in=identities))


def candidate_catalog(context):
    return catalog_for_scope(context.get('_content_scope'))


def lock_scope(user, expected):
    """Writers already hold the owner row; serialize the global OFF switch next.

    Order is owner -> control -> catalog, so expiry/review/user revocation are
    checked again after any catalog wait. Safe writes retain their old locks.
    """
    if expected and expected.get('content_scope') == 'include_adult':
        from .models import RecommendationServingControl
        if not RecommendationServingControl.objects.select_for_update().filter(pk=1).exists():
            from .engine import RecommendationSnapshotChanged
            raise RecommendationSnapshotChanged('服务开关已失效，请重新加载。')
    return ensure_scope(user, expected) if expected else require_scope(user)


def require_scope(user, requested_scope='safe'):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise NotAuthenticated()
    scope_query({'content_scope': requested_scope})
    control = control_state()
    session = self_service_enabled()
    prefix = 'recommendation_content_eligibility__'
    fields = ('is_active', '_content_review_version')
    if not session:
        fields += tuple(prefix + f for f in ('status','eligibility_version','expires_at','authorization_source','requested_at'))
    row = get_user_model().objects.filter(pk=user.pk).annotate(_content_review_version=Coalesce(Subquery(
        RecommendationContentReviewState.objects.filter(pk=1).values('version')[:1]),Value(0),output_field=IntegerField())).values(
        *fields).first()
    if not row or not row['is_active']:
        raise PermissionDenied('账号不可用。')
    status, version = ('session', 0) if session else (row[prefix+'status'] or 'not_requested', row[prefix+'eligibility_version'] or 0)
    expiry = None if session else row[prefix+'expires_at']
    authorization_source = 'session' if session else row[prefix+'authorization_source']
    expired = status == 'active' and (expiry is None or expiry <= timezone.now())
    authorized = authorization_source == 'manual' and status == 'active' and not expired
    if requested_scope == 'include_adult' and ((not session and not authorized) or not control['effective_enabled']):
        raise PermissionDenied({'code':'adult_scope_forbidden','detail':'访问授权无效或 NSFW 总开关已关闭。'})
    release = release_snapshot() if requested_scope == 'include_adult' else (
        set(), hashlib.sha256(b'safe-only').hexdigest(), None)
    if session and requested_scope == 'include_adult' and not release[0]:
        raise PermissionDenied({'code':'adult_catalog_unavailable', 'detail':'暂无符合一般成人内容范围的有效核验作品。'})
    deadline = min([d for d in (None if session else expiry, release[2]) if d is not None], default=None) if requested_scope == 'include_adult' else None
    result = {'content_scope_contract_version':CONTRACT_VERSION,'content_scope_policy_version':GENERAL_SCOPE_POLICY if session else POLICY_VERSION,
        'content_release_policy_version':RELEASE_POLICY_VERSION,'content_scope':requested_scope,
        'eligibility_version':version,'content_review_version':row['_content_review_version'],
        'serving_control_version':control['version'],'release_revision':release[1],
        'scope_valid_until':deadline.isoformat() if deadline else None}
    result['scope_revision'] = salted_hmac(CONTRACT_VERSION, json.dumps(
        [user.pk, result, status, str(expiry), bool(expired), authorization_source,
         control['runtime_enabled'], control['effective_enabled']], sort_keys=True), algorithm='sha256').hexdigest()
    return result


def ensure_scope(user, expected):
    from .engine import RecommendationSnapshotChanged
    try:
        current = require_scope(user, expected.get('content_scope', 'safe'))
    except PermissionDenied:
        raise RecommendationSnapshotChanged('访问授权或服务开关已变化，请重新加载。') from None
    if expected != current:
        raise RecommendationSnapshotChanged('内容范围或资格已更新，请重新加载推荐。')
    return current


def check_precondition(revision, current):
    if revision is None:
        return
    if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValidationError('内容范围上下文格式无效。')
    from .engine import RecommendationSnapshotChanged
    if not hmac.compare_digest(revision, current['scope_revision']):
        raise RecommendationSnapshotChanged('内容范围或资格已更新，请重新加载推荐。')


def verify_response_catalog(data, scope):
    """Final READ COMMITTED check after browse's read-only snapshot is closed.

    An ingest can change a formerly safe candidate without a review epoch write.
    Never send its old payload just because REPEATABLE READ still saw it as safe.
    """
    if not isinstance(data, dict):
        return
    rows = data.get('results', [data] if 'bangumi_id' in data else [])
    candidates, sources = set(), set()
    for row in rows:
        if row.get('bangumi_id'):
            candidates.add(row['bangumi_id'])
        candidates.update(c['bangumi_id'] for c in row.get('candidates', []))
        evidence = row.get('evidence') or {}
        if evidence.get('kind') == 'rated_work' and evidence.get('source', {}).get('bangumi_id'):
            sources.add(evidence['source']['bangumi_id'])
    from .identity import safe_catalog
    from .engine import RecommendationSnapshotChanged
    if (candidates and candidates != set(catalog_for_scope(scope).filter(bangumi_id__in=candidates).values_list('bangumi_id', flat=True))
        or sources and sources != set(safe_catalog().filter(bangumi_id__in=sources).values_list('bangumi_id', flat=True))):
        raise RecommendationSnapshotChanged('作品安全状态已变化，请重新加载。')
