"""Manual review ledger, never an age verifier or an adult recommendation switch."""
from datetime import timedelta
import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from .models import (RecommendationAnime, RecommendationContentReview as Review,
    RecommendationContentReviewState as ReviewState, RecommendationContentReviewEvent as Event)
from .normalization import clean_id

POLICY_VERSION = 'release-review.ledger.closed.v1'
CONTRACT_VERSION = 'recommendations.content-review.n2-2.v1'
MAX_VERSION = 2147483647
MANAGE_PERMISSION = 'recommendations.manage_content_review'
CATALOG_FIELDS = ('bangumi_id', 'title_zh', 'title_jp', 'nsfw', 'season', 'status_type',
                  'tags', 'synopsis', 'cover_url', 'source_cover_url')


class ReviewConflict(APIException):
    status_code = 409

    def __init__(self, detail='作品或审核版本已变化，请重新读取并核对。'):
        super().__init__({'code':'content_review_changed', 'detail':detail})


def can_review(user):
    return bool(user and user.is_active and user.is_staff and user.has_perm(MANAGE_PERMISSION))


def require_reviewer(actor):
    if not getattr(actor, 'is_authenticated', False) or not getattr(actor, 'pk', None):
        raise PermissionDenied('需要登录审核账号。')
    fresh = get_user_model().objects.filter(pk=actor.pk).first()
    if not can_review(fresh):
        raise PermissionDenied('没有作品审核专门权限。')
    return fresh


def catalog_revision(anime):
    # Existing fields ONLY. No semantic inference, media fetching, title matching
    # or user data. Source/reference changes require a fresh manual review.
    return hashlib.sha256(json.dumps([getattr(anime,key) for key in CATALOG_FIELDS],
        ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _current_review(identity):
    # One statement reads catalog and review together. Never trust a caller's
    # stale model instance, nor bind a new fingerprint to an older displayed title.
    anime=RecommendationAnime.objects.select_related('content_review').filter(bangumi_id=identity).first()
    if anime is None: raise NotFound('作品不存在。')
    try: row=anime.content_review
    except Review.DoesNotExist: row=None
    return anime,row


def _decision(anime, row, now=None):
    state = row.status if row else 'unreviewed'
    now = now or timezone.now()
    if anime.nsfw is not True:
        state = 'unknown_rating' if anime.nsfw is None else 'not_required_for_safe_v1'
    elif row and row.status == 'approved':
        if (row.policy_version != POLICY_VERSION or row.catalog_revision != catalog_revision(anime)
                or not row.exclusions_confirmed or not row.evidence_reference):
            state = 'stale'
        elif row.expires_at is None or row.expires_at <= now:
            state = 'expired'
    return {'contract_version':CONTRACT_VERSION, 'bangumi_id':anime.bangumi_id,
            'administrative_status':row.status if row else 'unreviewed', 'effective_status':state,
            'review_version':row.review_version if row else 0, 'catalog_revision':catalog_revision(anime),
            'policy_version':POLICY_VERSION, 'expires_at':row.expires_at if row else None,
            'review_valid':state == 'approved', 'adult_access_allowed':False, 'serving_enabled':False}


def release_decision(anime, *, now=None):
    """Trusted internal CURRENT read; no public route, write or automatic approval."""
    current,row=_current_review(anime.bangumi_id)
    return _decision(current,row,now)


def reviewer_contract(actor, bangumi_id):
    require_reviewer(actor)
    try: identity=clean_id(bangumi_id)
    except ValueError: raise NotFound('作品不存在。') from None
    anime,row=_current_review(identity)
    return anime, _decision(anime,row)


class MutationSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=0, max_value=MAX_VERSION)
    expected_catalog_revision = serializers.RegexField(r'^[a-f0-9]{64}$')
    expected_policy_version = serializers.CharField(max_length=64)
    operation_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=240)
    evidence_reference = serializers.RegexField(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$', required=False)
    exclusions_confirmed = serializers.BooleanField(default=False)
    days = serializers.IntegerField(min_value=1, max_value=30, default=30)

    def to_internal_value(self, data):
        if not isinstance(data,dict) or set(data)-set(self.fields):
            raise ValidationError({'non_field_errors':['包含未定义的审核字段。']})
        for key in ('expected_version','days'):
            if key in data and type(data[key]) is not int:
                raise ValidationError({key:'必须是整数，不能是布尔值或字符串。'})
        return super().to_internal_value(data)


@transaction.atomic
def mutate_review(*, actor, bangumi_id, action, changes):
    reviewer = require_reviewer(actor)
    if action not in ('prepare','approve','reject','revoke'):
        raise ValidationError('未定义的审核操作。')
    payload=MutationSerializer(data=changes); payload.is_valid(raise_exception=True)
    data=payload.validated_data
    try: identity=clean_id(bangumi_id)
    except ValueError: raise NotFound('作品不存在。') from None
    # All writers: catalog -> singleton epoch -> review -> audit receipt.
    # get_or_create uses a savepoint/unique PK for concurrent first-state creation.
    anime=RecommendationAnime.objects.select_for_update().filter(bangumi_id=identity).first()
    if anime is None: raise NotFound('作品不存在。')
    row=Review.objects.filter(anime=anime).first()
    fingerprint=hashlib.sha256(json.dumps({'actor':reviewer.pk,'work':identity,'action':action,**data},
        default=str,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    receipt=Event.objects.filter(review=row,operation_id=data['operation_id']).first() if row else None
    if receipt:
        if receipt.request_fingerprint != fingerprint: raise ReviewConflict('操作编号已用于其他审核内容。')
        return {'replayed':True,'changed':False,'operation_version':receipt.applied_version,
                'review':release_decision(anime)}
    version=row.review_version if row else 0
    current_fp=catalog_revision(anime)
    if (data['expected_version'] != version or data['expected_catalog_revision'] != current_fp
            or data['expected_policy_version'] != POLICY_VERSION):
        raise ReviewConflict()
    if action == 'prepare' and row and row.status != 'pending':
        raise ReviewConflict('已有结论不能被待审核操作覆盖。')
    if action == 'approve' and (anime.nsfw is not True or not data['exclusions_confirmed'] or not data.get('evidence_reference')):
        raise ValidationError('仅可审核明确成人标记作品；必须记录内部审核依据，并明确排除禁止内容。未知分级不可放行。')
    state,_=ReviewState.objects.get_or_create(pk=1)
    state=ReviewState.objects.select_for_update().get(pk=state.pk)
    target={'prepare':'pending','approve':'approved','reject':'rejected','revoke':'revoked'}[action]
    changed=row is None or version == 0 or row.status != target or action == 'approve'
    if changed and (version >= MAX_VERSION or state.version >= MAX_VERSION):
        raise ReviewConflict('审核版本已达上限，不能归零或覆盖。')
    previous=row.status if row else 'unreviewed'
    if row is None: row=Review(anime=anime)
    if changed:
        row.status=target; row.review_version=version+1
        row.policy_version=POLICY_VERSION; row.catalog_revision=current_fp; row.reviewer=reviewer
        if action == 'approve':
            row.evidence_reference=data['evidence_reference']; row.exclusions_confirmed=True
            row.reviewed_at=timezone.now(); row.expires_at=row.reviewed_at+timedelta(days=data['days'])
        row.save()
        state.version+=1; state.save(update_fields=['version'])
    Event.objects.create(review=row,actor=reviewer,operation_id=data['operation_id'],action=action,
        request_fingerprint=fingerprint,previous_status=previous,resulting_status=row.status,
        previous_version=version,applied_version=row.review_version,applied_release_version=state.version,
        changed=changed,reason=data['reason'],evidence_reference=row.evidence_reference,
        catalog_revision=row.catalog_revision,policy_version=row.policy_version,resulting_expires_at=row.expires_at)
    return {'replayed':False,'changed':changed,'operation_version':row.review_version,'review':release_decision(anime)}
