"""N2-1 administrative service only; adult recommendation serving stays closed."""
import hashlib
import json
import re

from datetime import datetime, timedelta
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotAuthenticated, NotFound, PermissionDenied, ValidationError

from . import age_assurance
from .models import RecommendationContentEligibility as Eligibility, RecommendationEligibilityEvent as Event

CONTRACT_VERSION = 'recommendations.eligibility.n2-1.v1'
POLICY_VERSION = 'eligibility-management.closed.v1'
MANAGE_PERMISSION = 'recommendations.manage_content_eligibility'
MAX_VERSION = 2147483647
MAX_DAYS = 30


class EligibilityConflict(APIException):
    status_code = 409

    def __init__(self, code, detail):
        super().__init__({'code': code, 'detail': detail, 'reload_required': True})


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({'non_field_errors': ['包含未定义的资格参数。']})
        return super().to_internal_value(data)


class EligibilityMutationSerializer(StrictInput):
    expected_version = serializers.IntegerField(min_value=0, max_value=MAX_VERSION)
    operation_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=200, allow_blank=False)
    days = serializers.IntegerField(min_value=1, max_value=MAX_DAYS, default=MAX_DAYS)


class EligibilitySelfRevokeSerializer(StrictInput):
    expected_version = serializers.IntegerField(min_value=0, max_value=MAX_VERSION)
    operation_id = serializers.UUIDField()
    confirmed = serializers.BooleanField()

    def validate_confirmed(self, value):
        if value is not True:
            raise ValidationError('请明确确认停用资格。')
        return value


def _require_user(user):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise NotAuthenticated()


def can_manage(user):
    return bool(user and user.is_active and user.is_staff and user.has_perm(MANAGE_PERMISSION))


def _assurance(user_id, now):
    try:
        result = age_assurance.current_age_assurance(user_id)
    except age_assurance.AgeAssuranceUnavailable:
        return None
    if not isinstance(result, age_assurance.AgeAssurance) or result.subject_id != user_id:
        return None
    if (result.status != 'verified' or type(result.version) is not int or not 1 <= result.version <= MAX_VERSION
            or not isinstance(result.provider, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result.provider)
            or not isinstance(result.reference, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', result.reference)
            or not isinstance(result.valid_until, datetime) or timezone.is_naive(result.valid_until)
            or result.valid_until <= now):
        return None
    return result


def _contract(user, row, *, now=None):
    now = now or timezone.now()
    version = row.eligibility_version if row else 0
    administrative = row.status if row else 'not_requested'
    effective = administrative
    usable = False
    valid_until = row.expires_at if row else None
    assurance_status = 'unavailable'
    if row and row.status == 'active':
        if row.expires_at <= now or row.assurance_valid_until <= now:
            effective, assurance_status = 'expired', 'expired'
        else:
            source = _assurance(user.pk, now)
            if source and (source.provider, source.reference, source.version) == (
                    row.assurance_provider, row.assurance_reference, row.assurance_version):
                valid_until = min(row.expires_at, source.valid_until)
                usable, assurance_status = True, 'verified'
            else:
                effective = 'assurance_unavailable'
    if not user.is_active:
        usable, effective = False, 'account_inactive'
    subject = salted_hmac(CONTRACT_VERSION + '.subject', str(user.pk), algorithm='sha256').hexdigest()
    revision = salted_hmac(CONTRACT_VERSION + '.revision', json.dumps(
        [subject, POLICY_VERSION, version, effective, assurance_status, valid_until.isoformat() if valid_until else None],
        separators=(',', ':')), algorithm='sha256').hexdigest()
    return {'contract_version': CONTRACT_VERSION, 'policy_version': POLICY_VERSION,
            'administrative_status': administrative, 'effective_status': effective,
            'administrative_qualification_valid': usable, 'eligibility_version': version,
            'age_assurance_status': assurance_status, 'valid_until': valid_until,
            # Hard closed, NOT configurable in N2-1; no existing engine consumes this.
            'adult_access_allowed': False, 'serving_integration': False, 'effective_scope': 'safe',
            'subject_binding': subject, 'eligibility_revision': revision}


def own_eligibility(user):
    _require_user(user)
    # GET never creates or updates a record, including natural expiry.
    current_user = get_user_model().objects.filter(pk=user.pk).first()
    if current_user is None:
        raise NotFound()
    row = Eligibility.objects.filter(user_id=user.pk).first()
    result = _contract(current_user, row)
    # Avoid returning a pre-revocation status read if a writer committed meanwhile.
    live_version = Eligibility.objects.filter(user_id=user.pk).values_list('eligibility_version', flat=True).first() or 0
    if live_version != result['eligibility_version']:
        raise EligibilityConflict('eligibility_changed', '资格已更新，请重新读取。')
    return result


@transaction.atomic
def mutate_eligibility(*, actor, subject_id, action, changes):
    _require_user(actor)
    if type(subject_id) is not int or subject_id <= 0 or action not in ('prepare', 'grant', 'renew', 'revoke', 'self_revoke'):
        raise ValidationError('不支持的资格操作。')
    # Fresh permission lookup; do not trust cached permissions on a request user.
    current_actor = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if current_actor is None:
        raise PermissionDenied('账号不可用。')
    if action == 'self_revoke':
        if actor.pk != subject_id:
            raise PermissionDenied('只能停用自己的资格。')
        serializer = EligibilitySelfRevokeSerializer(data=changes)
    else:
        if not can_manage(current_actor):
            raise PermissionDenied('没有资格管理权限。')
        if action in ('grant', 'renew') and actor.pk == subject_id:
            raise PermissionDenied('不能为自己签发或续期。')
        serializer = EligibilityMutationSerializer(data=changes)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    # Same user-first lock order as add_want/feedback. Serializes first-row creation too.
    subject = get_user_model().objects.select_for_update().filter(pk=subject_id).first()
    if subject is None:
        raise NotFound('账号不存在。')
    row = Eligibility.objects.select_for_update().filter(user_id=subject_id).first()
    version = row.eligibility_version if row else 0
    fingerprint = hashlib.sha256(json.dumps({'actor': actor.pk, 'subject': subject_id, 'action': action, **data},
        default=str, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = Event.objects.filter(eligibility=row, operation_id=data['operation_id']).first() if row else None
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise EligibilityConflict('eligibility_operation_conflict', '操作编号已用于其他请求。')
        return {'replayed': True, 'changed': False, 'operation_version': receipt.applied_version,
                'eligibility': _contract(subject, row)}
    if version != data['expected_version']:
        raise EligibilityConflict('eligibility_version_conflict', '资格版本已更新，请重新读取后操作。')
    now = timezone.now()
    previous = row.status if row else 'not_requested'
    target = 'revoked' if action in ('revoke', 'self_revoke') else 'pending' if action == 'prepare' else 'active'
    if action == 'prepare' and row and row.status != 'pending':
        raise EligibilityConflict('eligibility_state_conflict', '已有资格不能被待审核操作覆盖。')
    if action == 'renew' and (not row or row.status != 'active'):
        raise EligibilityConflict('eligibility_state_conflict', '只有已签发记录可续期；已撤销记录需要重新审核签发。')
    if action == 'grant' and row and row.status == 'active':
        raise EligibilityConflict('eligibility_state_conflict', '已有签发记录请使用续期操作。')
    source = None
    if action in ('grant', 'renew'):
        if not subject.is_active:
            raise PermissionDenied('停用账号不可获得资格。')
        source = _assurance(subject.pk, now)
        if source is None:
            raise ValidationError({'code': 'age_assurance_unavailable', 'detail': '缺少服务端可信成年依据，未签发或续期。'})
        deadline = min(now + timedelta(days=data['days']), source.valid_until)
        if action == 'renew' and deadline <= row.expires_at:
            raise ValidationError('续期必须延长有效期；需要缩短或终止时请明确撤销。')
    changed = row is None or row.status != target or action in ('grant', 'renew')
    if changed and version >= MAX_VERSION:
        raise EligibilityConflict('eligibility_version_exhausted', '资格版本已达上限。')
    if row is None:
        row = Eligibility(user=subject)
    if changed:
        row.status, row.eligibility_version = target, version + 1
        if source:
            row.granted_at = now
            row.expires_at = deadline
            row.granted_by = current_actor
            row.revoked_at = None
            row.assurance_provider, row.assurance_reference = source.provider, source.reference
            row.assurance_version, row.assurance_valid_until = source.version, source.valid_until
        elif target == 'revoked':
            row.revoked_at = now
        row.save()
    event = Event.objects.create(eligibility=row, actor=current_actor, operation_id=data['operation_id'], action=action,
        request_fingerprint=fingerprint, previous_status=previous, resulting_status=row.status, previous_version=version,
        applied_version=row.eligibility_version, changed=changed,
        reason='用户主动停用' if action == 'self_revoke' else data['reason'], resulting_expires_at=row.expires_at)
    return {'replayed': False, 'changed': changed, 'operation_version': event.applied_version,
            'eligibility': _contract(subject, row)}
