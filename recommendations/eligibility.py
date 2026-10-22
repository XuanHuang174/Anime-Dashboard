"""M1 manual access authorization; never age verification or adult serving."""
import hashlib
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotAuthenticated, NotFound, PermissionDenied, ValidationError

from .models import RecommendationContentEligibility as Eligibility, RecommendationEligibilityEvent as Event
from .adult_policy import self_service_enabled, SESSION_CONTRACT, SESSION_POLICY

CONTRACT_VERSION = 'recommendations.eligibility.manual.m2.v1'
POLICY_VERSION = 'eligibility.manual.m2.v1'
MAX_VERSION = 2147483647
MAX_DAYS = 30


class EligibilityConflict(APIException):
    status_code = 409

    def __init__(self, code, detail):
        super().__init__({'code': code, 'detail': detail, 'reload_required': True})


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({'non_field_errors': ['包含未定义的访问申请参数。']})
        if type(data.get('expected_version')) is not int:
            raise ValidationError({'expected_version': '必须提供整数版本。'})
        return super().to_internal_value(data)


class EligibilityMutationSerializer(StrictInput):
    expected_version = serializers.IntegerField(min_value=0, max_value=MAX_VERSION)
    operation_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=200, allow_blank=False)
    days = serializers.IntegerField(min_value=1, max_value=MAX_DAYS, default=MAX_DAYS)
    public_message = serializers.CharField(max_length=200, allow_blank=True, default='')
    confirmed = serializers.BooleanField(default=False)
    self_approval_confirmed = serializers.BooleanField(default=False)

    def to_internal_value(self, data):
        for key in ('confirmed', 'self_approval_confirmed'):
            if isinstance(data, dict) and key in data and type(data[key]) is not bool:
                raise ValidationError({key: '必须明确提交布尔确认，不能使用字符串或数字。'})
        return super().to_internal_value(data)


class EligibilitySelfRevokeSerializer(StrictInput):
    expected_version = serializers.IntegerField(min_value=0, max_value=MAX_VERSION)
    operation_id = serializers.UUIDField()
    confirmed = serializers.BooleanField()

    def to_internal_value(self, data):
        if not isinstance(data, dict) or data.get('confirmed') is not True:
            raise ValidationError({'confirmed': '请明确阅读并确认本次操作。'})
        return super().to_internal_value(data)


def _require_user(user):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise NotAuthenticated()


def can_manage(user):
    # A former delegated permission or cached has_perm result is NOT approval authority.
    return bool(user and user.is_active and user.is_staff and user.is_superuser)


def _contract(user, row, *, now=None):
    now = now or timezone.now()
    version = row.eligibility_version if row else 0
    administrative = row.status if row else 'not_requested'
    source = row.authorization_source if row else 'none'
    effective, usable = administrative, False
    valid_until = row.expires_at if row else None
    if row and row.status == 'active':
        if source != 'manual':
            effective = 'legacy_unavailable'
        elif row.expires_at is None or row.expires_at <= now:
            effective = 'expired'
        else:
            usable = True
    if not user.is_active:
        usable, effective = False, 'account_inactive'
    subject = salted_hmac(CONTRACT_VERSION + '.subject', str(user.pk), algorithm='sha256').hexdigest()
    from .serving_control import control_state
    serving = control_state()
    session = self_service_enabled()
    contract, policy = (SESSION_CONTRACT, SESSION_POLICY) if session else (CONTRACT_VERSION, POLICY_VERSION)
    available = bool(user.is_active) if session else usable
    release_revision = None
    if session:
        # A read-only adapter; no account grant, directory import or approval.
        from .content_scope import release_snapshot
        released, release_revision, _ = release_snapshot()
        available = available and bool(released)
    revision = salted_hmac(CONTRACT_VERSION + '.revision', json.dumps(
        [subject, policy, version, source, effective, valid_until.isoformat() if valid_until else None,
         serving['version'], serving['effective_enabled'], release_revision], separators=(',', ':')), algorithm='sha256').hexdigest()
    from .scope_control import scope_control_contract
    return {'contract_version': contract, 'policy_version': policy,
        'scope_control': scope_control_contract(available=available,
            serving_enabled=serving['effective_enabled'], revision=revision),
        'administrative_status': administrative, 'effective_status': effective,
        'authorization_source': source, 'manual_authorization_valid': usable,
        'administrative_qualification_valid': usable, 'eligibility_version': version,
        'age_assurance_status': 'not_performed', 'valid_until': valid_until,
        'requested_at': row.requested_at if row else None,
        'decision_message': row.decision_message if row and source == 'manual' and administrative == 'rejected' else '',
        'can_apply': bool(not session and user.is_active and not usable and not (source == 'manual' and administrative == 'pending')),
        'adult_access_allowed': bool(available and serving['effective_enabled']),
        'serving_integration': serving['effective_enabled'], 'effective_scope': 'safe',
        'serving_control_version': serving['version'],
        'subject_binding': subject, 'eligibility_revision': revision}


def own_eligibility(user):
    _require_user(user)
    current_user = get_user_model().objects.filter(pk=user.pk).first()
    if current_user is None:
        raise NotFound()
    row = Eligibility.objects.filter(user_id=user.pk).first()
    result = _contract(current_user, row)
    live_version = Eligibility.objects.filter(user_id=user.pk).values_list('eligibility_version', flat=True).first() or 0
    if live_version != result['eligibility_version']:
        raise EligibilityConflict('eligibility_changed', '访问授权已更新，请重新读取。')
    return result


@transaction.atomic
def mutate_eligibility(*, actor, subject_id, action, changes):
    _require_user(actor)
    if type(subject_id) is not int or subject_id <= 0 or action not in ('apply', 'approve', 'reject', 'revoke', 'self_revoke'):
        raise ValidationError('不支持的访问授权操作；旧签发/续期入口已停用。')
    current_actor = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if current_actor is None:
        raise PermissionDenied('账号不可用。')
    own_action = action in ('apply', 'self_revoke')
    if action in ('apply', 'approve') and self_service_enabled():
        raise ValidationError('本次浏览使用筛选开关，不再提交账号访问申请。')
    if own_action:
        if actor.pk != subject_id:
            raise PermissionDenied('只能申请或停用自己的访问授权。')
        serializer = EligibilitySelfRevokeSerializer(data=changes)
    else:
        if not can_manage(current_actor):
            raise PermissionDenied('仅超级管理员可以审批或撤销访问授权。')
        serializer = EligibilityMutationSerializer(data=changes)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    is_self_approval = not own_action and action == 'approve' and actor.pk == subject_id
    if is_self_approval and not (data['confirmed'] is True and data['self_approval_confirmed'] is True):
        raise ValidationError({'code': 'self_approval_confirmation_required',
            'detail': '超级管理员批准本人申请，须同时确认本次审批和本人授权；操作将记录为自审。'})
    if not is_self_approval and not own_action:
        # Preserve pre-upgrade non-self receipt fingerprints during rolling deploys.
        data.pop('confirmed', None)
        data.pop('self_approval_confirmed', None)
    # Shared lock order with want/feedback. Locks first creation AND all state decisions.
    subject = get_user_model().objects.select_for_update().filter(pk=subject_id).first()
    if subject is None:
        raise NotFound('账号不存在。')
    current_actor = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    if current_actor is None or (not own_action and not can_manage(current_actor)):
        raise PermissionDenied('操作权限已失效。')
    row = Eligibility.objects.select_for_update().filter(user_id=subject_id).first()
    version = row.eligibility_version if row else 0
    fingerprint = hashlib.sha256(json.dumps({'policy': POLICY_VERSION, 'actor': actor.pk,
        'subject': subject_id, 'action': action, **data}, default=str, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = Event.objects.filter(eligibility=row, operation_id=data['operation_id']).first() if row else None
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise EligibilityConflict('eligibility_operation_conflict', '操作编号已用于其他请求。')
        return {'replayed': True, 'changed': False, 'operation_version': receipt.applied_version,
                'eligibility': _contract(subject, row)}
    if version != data['expected_version']:
        raise EligibilityConflict('eligibility_version_conflict', '访问授权版本已更新，请重新读取后操作。')
    now = timezone.now()
    previous = row.status if row else 'not_requested'
    if action in ('approve', 'reject') and not (row and row.authorization_source == 'manual'
                                               and row.status == 'pending' and row.requested_at):
        raise EligibilityConflict('eligibility_state_conflict', '必须先有用户的待审批申请。')
    if action in ('apply', 'approve') and not subject.is_active:
        raise PermissionDenied('停用账号不可申请或获得授权。')
    current = _contract(subject, row, now=now)
    if action == 'apply' and current['manual_authorization_valid']:
        raise EligibilityConflict('eligibility_state_conflict', '当前访问授权仍有效，无需重复申请。')
    target = {'apply': 'pending', 'approve': 'active', 'reject': 'rejected',
              'revoke': 'revoked', 'self_revoke': 'revoked'}[action]
    changed = (row is None or row.authorization_source != 'manual' or previous != target)
    if changed and version >= MAX_VERSION:
        raise EligibilityConflict('eligibility_version_exhausted', '访问授权版本已达上限。')
    if row is None:
        row = Eligibility(user=subject)
    if changed:
        row.status, row.eligibility_version = target, version + 1
        row.authorization_source, row.purpose = 'manual', 'manual_access'
        # Never manufacture a verified age provider for a manual decision.
        row.assurance_provider = row.assurance_reference = ''
        row.assurance_version, row.assurance_valid_until = 0, None
        if action == 'apply':
            row.requested_at, row.decided_at, row.decision_message = now, None, ''
            row.granted_at = row.expires_at = row.revoked_at = row.granted_by = None
        elif action == 'approve':
            row.decided_at = row.granted_at = now
            row.expires_at = now + timedelta(days=data['days'])
            row.granted_by, row.revoked_at, row.decision_message = current_actor, None, ''
        elif action == 'reject':
            row.decided_at = now
            row.decision_message = data['public_message'] or '本次申请未通过，可重新申请。'
        else:
            row.revoked_at = now
        row.save()
    event = Event.objects.create(eligibility=row, actor=current_actor, operation_id=data['operation_id'], action=action,
        is_self_approval=is_self_approval,
        request_fingerprint=fingerprint, previous_status=previous, resulting_status=row.status,
        previous_version=version, applied_version=row.eligibility_version, changed=changed,
        reason='用户主动申请' if action == 'apply' else '用户主动停用' if action == 'self_revoke' else data['reason'],
        resulting_expires_at=row.expires_at)
    return {'replayed': False, 'changed': changed, 'operation_version': event.applied_version,
            'eligibility': _contract(subject, row)}
