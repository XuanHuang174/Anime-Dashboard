"""8A exact-work feedback. No journal writes, feature inference or shared private cache."""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Subquery
from django.utils import timezone
from rest_framework.exceptions import APIException, NotAuthenticated, NotFound, ValidationError

from .identity import safe_catalog
from .models import (UserRecommendationFeedback as Feedback,
                     UserRecommendationFeedbackState as FeedbackState,
                     RecommendationFeedbackOperation as Operation)

CONTRACT_VERSION = 'recommendations.feedback.v1'
MAX_VERSION = 2147483647


class FeedbackConflict(APIException):
    status_code = 409

    def __init__(self, code, detail, **extra):
        self.payload = {'code': code, 'detail': detail, **extra}
        super().__init__(self.payload)


def _require_user(user):
    if not getattr(user, 'is_authenticated', False):
        raise NotAuthenticated()


def current_version(user):
    _require_user(user)
    return FeedbackState.objects.filter(user_id=user.pk).values_list('feedback_version', flat=True).first() or 0


def ensure_version(user, expected):
    if current_version(user) != expected:
        raise FeedbackConflict('feedback_version_conflict', '反馈已在另一处更新，请重新读取后再操作。',
                               reload_required=True)


def feedback_snapshot(user):
    """Optimistic request-local snapshot. GET never creates a state row."""
    version = current_version(user)
    identities = frozenset(Feedback.objects.filter(user_id=user.pk, is_active=True)
                          .values_list('bangumi_id', flat=True))
    ensure_version(user, version)
    return {'version': version, 'hidden_ids': identities}


def _work_contract(row):
    return {'bangumi_id': row.bangumi_id, 'is_active': row.is_active,
            'feedback_version': row.feedback_version, 'hidden_at': row.hidden_at,
            'updated_at': row.updated_at}


@transaction.atomic
def mutate_feedback(*, user, action, changes):
    """All writers lock user -> safe catalog -> state/work, same order as add_want.

    expected_feedback_version is mandatory; retries MUST reuse the exact UUID/body.
    A replay reports CURRENT work state, not an obsolete receipt's active flag.
    """
    _require_user(user)
    if action not in ('hide', 'undo'):
        raise ValidationError('不支持的反馈操作。')
    # Validate even direct service callers, not just the HTTP adapter.
    from .serializers import FeedbackMutationSerializer, FeedbackUndoSerializer
    serializer = (FeedbackUndoSerializer if action == 'undo' else FeedbackMutationSerializer)(data=changes)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if get_user_model().objects.select_for_update().filter(pk=user.pk, is_active=True).only('pk').first() is None:
        raise NotFound('账号不可用。')
    if safe_catalog().select_for_update().filter(bangumi_id=data['bangumi_id']).only('pk').first() is None:
        raise NotFound('该作品不可用于推荐。')
    version = current_version(user)
    fingerprint = hashlib.sha256(json.dumps({'action': action, **data}, sort_keys=True,
                                            separators=(',', ':'), default=str).encode()).hexdigest()
    receipt = Operation.objects.filter(user_id=user.pk, operation_id=data['operation_id']).first()
    replayed = receipt is not None
    row = Feedback.objects.select_for_update().filter(user_id=user.pk, bangumi_id=data['bangumi_id']).first()
    changed = False
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise FeedbackConflict('feedback_operation_conflict', '操作编号已用于其他请求，请重新读取并使用新的编号。')
        if row is None:
            raise FeedbackConflict('feedback_state_changed', '反馈记录已变化，请重新读取。')
    else:
        if version != data['expected_feedback_version']:
            raise FeedbackConflict('feedback_version_conflict', '反馈版本已更新，请重新读取后再操作。',
                                   feedback_version=version, reload_required=True)
        if action == 'undo':
            if row is None:
                raise NotFound('没有可撤销的反馈。')
            if row.feedback_version != data['target_feedback_version']:
                raise FeedbackConflict('feedback_target_conflict', '这不是当前作品的最新反馈，请重新读取后再撤销。',
                                       feedback_version=version, reload_required=True)
        active = action == 'hide'
        changed = row is None or row.is_active != active
        if changed:
            if version >= MAX_VERSION:
                raise FeedbackConflict('feedback_version_exhausted', '反馈版本已达上限，请联系管理员。')
            version += 1
            state, _ = FeedbackState.objects.get_or_create(user_id=user.pk)
            state.feedback_version = version
            state.save(update_fields=('feedback_version', 'updated_at'))
            if row is None:
                row = Feedback(user_id=user.pk, bangumi_id=data['bangumi_id'])
            row.is_active = active
            row.feedback_version = version
            if active:
                row.hidden_at = timezone.now()
            row.save()
        receipt = Operation.objects.create(user_id=user.pk, operation_id=data['operation_id'],
            bangumi_id=data['bangumi_id'], action=action, request_fingerprint=fingerprint,
            applied_feedback_version=version)
    return {'contract_version': CONTRACT_VERSION, 'action': action, 'operation_id': data['operation_id'],
            'replayed': replayed,
            'changed': changed, 'feedback_version': version,
            'operation_feedback_version': receipt.applied_feedback_version, 'feedback': _work_contract(row)}


def hidden_feedback(user, options):
    _require_user(user)
    version = current_version(user)
    if options.get('feedback_version', version) != version:
        raise FeedbackConflict('feedback_version_conflict', '隐藏清单已变化，请重新加载第一页。',
                               feedback_version=version, reload_required=True)
    query = Feedback.objects.filter(user_id=user.pk, is_active=True,
        bangumi_id__in=Subquery(safe_catalog().values('bangumi_id'))).order_by('-hidden_at', '-pk')
    count = query.count()
    start = (options['page'] - 1) * options['page_size']
    rows = list(query[start:start + options['page_size']])
    catalog = {row.bangumi_id: row for row in safe_catalog().filter(bangumi_id__in=[r.bangumi_id for r in rows])}
    results = [{**_work_contract(row), 'title_zh': catalog[row.bangumi_id].title_zh,
                'title_jp': catalog[row.bangumi_id].title_jp, 'season': catalog[row.bangumi_id].season,
                'status_type': catalog[row.bangumi_id].status_type} for row in rows if row.bangumi_id in catalog]
    ensure_version(user, version)
    return {'contract_version': CONTRACT_VERSION, 'feedback_version': version, 'count': count,
            'page': options['page'], 'page_size': options['page_size'],
            'next_page': options['page'] + 1 if start + len(rows) < count else None, 'results': results}
