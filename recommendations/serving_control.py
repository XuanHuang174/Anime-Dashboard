"""Two independent gates: deployment capability AND audited module-3 switch."""
import hashlib
import json
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from .eligibility import can_manage, EligibilityConflict, MAX_VERSION
from .models import RecommendationServingControl as Control, RecommendationServingControlEvent as Event


def runtime_enabled():
    return (getattr(settings, 'RECOMMENDATION_RESTRICTED_CONTENT_ENABLED', False) is True
        and getattr(settings, 'RECOMMENDATION_ALGORITHM_VERSION', 'content.v1') == 'content.v1'
        and getattr(settings, 'RECOMMENDATION_V2_BROWSE_ENABLED', False) is not True)


def control_state(*, admin=False):
    row = Control.objects.filter(pk=1).first() if admin or runtime_enabled() else None
    return {'runtime_enabled': runtime_enabled(), 'enabled': bool(row and row.enabled),
        'version': row.version if row else 0,
        'effective_enabled': bool(runtime_enabled() and row and row.enabled)}


def require_superuser(user):
    fresh = get_user_model().objects.filter(pk=getattr(user, 'pk', None)).first()
    if not can_manage(fresh):
        raise PermissionDenied('仅超级管理员可管理受限内容。')
    return fresh


@transaction.atomic
def change_control(actor, changes):
    actor = require_superuser(actor)
    if not isinstance(changes, dict) or set(changes) != {'enabled','expected_version','operation_id','reason','confirmed'}:
        raise ValidationError('开关操作字段无效。')
    if changes['confirmed'] is not True or type(changes['enabled']) is not bool or type(changes['expected_version']) is not int:
        raise ValidationError('需要明确确认、布尔开关及整数版本。')
    if not 0 <= changes['expected_version'] <= MAX_VERSION:
        raise ValidationError('开关版本超出范围。')
    operation = serializers.UUIDField().run_validation(changes['operation_id'])
    reason = serializers.CharField(max_length=200).run_validation(changes['reason'])
    fingerprint = hashlib.sha256(json.dumps([actor.pk, changes['enabled'], changes['expected_version'], str(operation), reason]).encode()).hexdigest()
    Control.objects.get_or_create(pk=1)
    row = Control.objects.select_for_update().get(pk=1)
    require_superuser(actor)  # permission may have changed during lock wait
    receipt = Event.objects.filter(operation_id=operation).first()
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise EligibilityConflict('control_operation_conflict','该编号已用于不同操作。')
        return {**control_state(admin=True), 'replayed':True}
    if row.version != changes['expected_version']:
        raise EligibilityConflict('control_version_conflict','总开关已变化，请重新读取。')
    if changes['enabled'] and not runtime_enabled():
        raise ValidationError('部署总闸未开放或算法不是 V1，不能启用。')
    if row.enabled != changes['enabled']:
        if row.version >= MAX_VERSION:
            raise EligibilityConflict('control_version_exhausted','总开关版本已达上限。')
        row.enabled, row.version = changes['enabled'], row.version + 1
        row.save()
    Event.objects.create(operation_id=operation, actor=actor, request_fingerprint=fingerprint,
        enabled=row.enabled, version=row.version, reason=reason)
    return {**control_state(admin=True), 'replayed':False}
