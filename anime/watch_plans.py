"""Private viewing intentions. Lock order: records by pk, then owner queue.

Plans never fabricate watching events. Actual starts use the watch-history
service, and its observer reconciles the linked plan in the same transaction.
"""
import hashlib
import json

from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Max, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import UserAnimeRecord, WatchPlan, WatchPlanOperation, WatchPlanQueue
from .watch_history import date_bounds

ACTIVE = ('pending', 'ongoing')
PURPOSE = {'plan': 'first', 'watching': 'continue', 'on_hold': 'resume', 'completed': 'rewatch'}
FIELDS = ('priority', 'position', 'planned_for', 'note', 'purpose', 'state',
          'target_round_number', 'anchor_round_number', 'history_base')


class PlanConflict(APIException):
    status_code = 409
    default_detail = '观看计划或历程已变化，请重新读取并核对后再操作。'


def lock_queue(user_id, create=False):
    if create:
        WatchPlanQueue.objects.get_or_create(user_id=user_id)
    return WatchPlanQueue.objects.select_for_update().filter(user_id=user_id).first()


def bump(queue):
    WatchPlanQueue.objects.filter(pk=queue.pk).update(revision=F('revision') + 1)
    queue.refresh_from_db(fields=['revision'])


def current_round(record):
    return record.watch_rounds.filter(is_current=True, voided=False).first()


def next_position(queue, priority):
    value = queue.plans.filter(priority=priority).aggregate(value=Max('position'))['value']
    if value is not None and value >= 2147483000:
        # Imported sparse positions must not exhaust PostgreSQL's integer range.
        # Queue lock serializes this content-free order normalization.
        rows = list(queue.plans.filter(priority=priority).order_by('position', 'record_id'))
        for index, plan in enumerate(rows): plan.position = index
        WatchPlan.objects.bulk_update(rows, ['position'])
        return len(rows)
    return (value if value is not None else -1) + 1


def enroll_state(plan, record):
    row = current_round(record)
    number = row.number if row else 1
    plan.purpose = PURPOSE[record.watch_status]
    plan.anchor_round_number = number
    plan.target_round_number = None if plan.purpose == 'rewatch' else number
    plan.history_base = record.watch_history_base
    plan.state = 'ongoing' if record.watch_status == 'watching' else 'pending'


def reconcile(plan, record, event=None):
    """Re-evaluate real round facts, never infer a rewatch from status alone."""
    row = current_round(record)
    number = row.number if row else 1
    if plan.history_base != record.watch_history_base:
        # Clear/restore changes the round namespace. Retain the arrangement,
        # but never keep a reference to the deleted private round.
        plan.history_base = record.watch_history_base
        plan.anchor_round_number = number
        if plan.purpose == 'rewatch' and plan.state == 'pending':
            plan.target_round_number = None
        else:
            plan.target_round_number = number
    if plan.state == 'archived':
        return
    if plan.purpose == 'rewatch' and plan.target_round_number is None:
        if plan.anchor_round_number > number:
            plan.anchor_round_number = number
        if event is not None and event.kind == 'start-rewatch' and number > plan.anchor_round_number:
            plan.target_round_number = number
        else:
            plan.state = 'pending'
            return
    target = record.watch_rounds.filter(number=plan.target_round_number, voided=False).first()
    if target is None and plan.target_round_number != number:
        # Undo rewatch voids its round. Wait for an explicit future rewatch.
        if plan.purpose == 'rewatch':
            plan.target_round_number = None
            plan.anchor_round_number = number
            plan.state = 'pending'
            return
        plan.target_round_number = number
    status = target.status if target else record.watch_status
    plan.state = {'plan': 'pending', 'on_hold': 'pending', 'watching': 'ongoing', 'completed': 'completed'}[status]


def sync_plan(record, event=None):
    """Called only inside the existing record lock and transaction."""
    plan = WatchPlan.objects.filter(record=record).first()
    if plan is None or plan.state == 'archived' and plan.history_base == record.watch_history_base:
        return
    queue = lock_queue(record.user_id)
    before = tuple(getattr(plan, key) for key in FIELDS)
    old_state = plan.state
    reconcile(plan, record, event)
    if old_state == 'completed' and plan.state in ACTIVE:
        plan.position = next_position(queue, plan.priority)
    changed = before != tuple(getattr(plan, key) for key in FIELDS)
    observed_change = event is not None and event.before['record'] != event.after['record']
    if changed:
        plan.save()
    if changed or observed_change:
        bump(queue)


def plan_data(plan):
    if plan is None:
        return None
    return {'version': 1, **{key: getattr(plan, key) for key in FIELDS},
            'enrolled_at': plan.enrolled_at.isoformat()}


def record_summary(record, request=None):
    metadata = record.effective_anime_metadata()
    image = metadata.get('poster_image')
    cover = image.url if image else metadata.get('poster_url', '')
    if cover and request:
        cover = request.build_absolute_uri(cover)
    return {'id': record.pk, 'title': metadata['chinese_title'], 'japanese_title': metadata['japanese_title'],
            'period': metadata['airing_period'], 'cover': cover, 'watch_status': record.watch_status,
            'watched_eps': record.watched_eps, 'total_eps': record.total_eps, 'watch_revision': record.watch_revision}


def detail_data(record, queue, request=None, replayed=False):
    return {'contract_version': 'journal.watch-plan.v1', 'version': queue.revision if queue else 0,
            'record': record_summary(record, request), 'plan': plan_data(WatchPlan.objects.filter(record=record).first()),
            'replayed': replayed}


class PlanCommandSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('enroll', 'update', 'remove', 'start'))
    version = serializers.IntegerField(min_value=0, max_value=9223372036854775806)
    operation_id = serializers.UUIDField()
    priority = serializers.IntegerField(min_value=1, max_value=3, required=False)
    planned_for = serializers.CharField(allow_blank=True, max_length=10, required=False)
    note = serializers.CharField(allow_blank=True, max_length=500, required=False)
    watch_version = serializers.IntegerField(min_value=0, max_value=2147483647, required=False)
    occurred_on = serializers.CharField(allow_blank=True, max_length=10, required=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields) or type(data.get('version')) is not int:
            raise serializers.ValidationError({'detail': '请提交合法计划操作、版本与唯一编号。'})
        if 'priority' in data and type(data['priority']) is not int:
            raise serializers.ValidationError({'detail': '优先级请使用 1、2 或 3。'})
        return super().to_internal_value(data)

    def validate(self, data):
        allowed = {'enroll': {'priority', 'planned_for', 'note'}, 'update': {'priority', 'planned_for', 'note'},
                   'remove': set(), 'start': {'watch_version', 'occurred_on'}}[data['action']]
        if set(data) - {'action', 'version', 'operation_id'} - allowed:
            raise serializers.ValidationError('此计划操作包含不适用的字段。')
        for key in ('planned_for', 'occurred_on'):
            if data.get(key): date_bounds(data[key])
        if data['action'] == 'update' and not set(data).intersection(allowed):
            raise serializers.ValidationError('请至少修改一项计划内容。')
        if data['action'] == 'start' and 'watch_version' not in data:
            raise serializers.ValidationError('开始观看需要同时核对最新历程版本。')
        return data


def digest(raw, record_id=None):
    return hashlib.sha256(json.dumps([record_id, raw], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def replay(queue, operation, request_hash):
    receipt = queue.operations.filter(operation_id=operation).first()
    if receipt and receipt.request_hash != request_hash:
        raise PlanConflict('同一操作编号不能用于不同内容或作品。')
    return receipt is not None


@transaction.atomic
def command(user, record_id, raw, request=None):
    serializer = PlanCommandSerializer(data=raw); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    record = get_object_or_404(UserAnimeRecord.objects.select_for_update(of=('self',)).select_related('anime'), pk=record_id, user=user)
    queue = lock_queue(user.pk, create=True)
    request_hash = digest(raw, record.pk)
    if replay(queue, data['operation_id'], request_hash):
        return detail_data(record, queue, request, True)
    if data['version'] != queue.revision:
        raise PlanConflict()
    plan = WatchPlan.objects.filter(record=record).first()
    action = data['action']
    if action == 'enroll':
        if plan is not None and plan.state in ACTIVE:
            raise serializers.ValidationError('这部作品已经在当前计划中。')
        plan = plan or WatchPlan(record=record, queue=queue)
        for key in ('priority', 'planned_for', 'note'):
            if key in data: setattr(plan, key, data[key])
        enroll_state(plan, record)
        plan.position = next_position(queue, plan.priority)
        plan.enrolled_at = timezone.now()
        plan.save(); bump(queue)
    elif plan is None or plan.state == 'archived':
        raise serializers.ValidationError('请先将作品加入观看计划。')
    elif action == 'update':
        before = (plan.priority, plan.planned_for, plan.note)
        if 'priority' in data and plan.priority != data['priority']:
            plan.position = next_position(queue, data['priority'])
        for key in ('priority', 'planned_for', 'note'):
            if key in data: setattr(plan, key, data[key])
        if before == (plan.priority, plan.planned_for, plan.note):
            raise serializers.ValidationError('计划内容没有变化。')
        plan.save(); bump(queue)
    elif action == 'remove':
        plan.state = 'archived'; plan.save(); bump(queue)
    elif action == 'start':
        if data['watch_version'] != record.watch_revision or plan.state != 'pending':
            raise PlanConflict()
        if plan.purpose == 'rewatch':
            if plan.target_round_number is not None or record.watch_status != 'completed':
                raise PlanConflict('请核对当前轮次；准备重看须通过明确的新一轮操作开始。')
            history_body = {'action': 'start-rewatch', 'started_on': data.get('occurred_on', '')}
        else:
            if record.watch_status not in ('plan', 'on_hold'):
                raise PlanConflict()
            history_body = {'action': 'transition', 'watch_status': 'watching', 'occurred_on': data.get('occurred_on', '')}
        # Independent operation namespaces; the same UUID safely identifies the
        # actual start in the history ledger and the intention command receipt.
        from .watch_history import execute
        execute(record.pk, user, {**history_body, 'version': record.watch_revision, 'operation_id': str(data['operation_id'])})
        record.refresh_from_db(); queue.refresh_from_db()
    WatchPlanOperation.objects.create(queue=queue, operation_id=data['operation_id'], request_hash=request_hash)
    return detail_data(record, queue, request)


@transaction.atomic
def read_detail(user, record_id, request=None):
    record = get_object_or_404(UserAnimeRecord.objects.select_for_update(of=('self',)).select_related('anime'), pk=record_id, user=user)
    return detail_data(record, lock_queue(user.pk), request)


@transaction.atomic
def read_queue(user, params, request=None):
    if set(params) - {'page', 'page_size', 'state', 'priority', 'search', 'sort'} or any(len(params.getlist(key)) != 1 for key in params):
        raise serializers.ValidationError('计划筛选参数无效。')
    try:
        page, size = int(params.get('page', 1)), int(params.get('page_size', 20))
        priority = int(params['priority']) if 'priority' in params else None
        if page < 1 or not 1 <= size <= 100 or priority is not None and priority not in (1, 2, 3): raise ValueError()
    except (ValueError, TypeError):
        raise serializers.ValidationError('计划分页或优先级参数无效。')
    state, order, search = params.get('state', 'active'), params.get('sort', 'manual'), params.get('search', '').strip()
    if state not in ('all', 'active', *WatchPlan.State.values) or order not in ('manual', 'planned', 'added') or len(search) > 100:
        raise serializers.ValidationError('计划筛选参数无效。')
    # Serialize the read with lifecycle and reorder writes without creating
    # queue/plan/round rows on GET.
    list(UserAnimeRecord.objects.select_for_update().filter(user=user).order_by('pk').values_list('pk', flat=True))
    queue = lock_queue(user.pk)
    query = WatchPlan.objects.filter(record__user=user).select_related('record__anime')
    counts = {row['state']: row['n'] for row in query.values('state').annotate(n=Count('pk'))}
    if state == 'active': query = query.filter(state__in=ACTIVE)
    elif state != 'all': query = query.filter(state=state)
    if priority is not None: query = query.filter(priority=priority)
    if search:
        query = query.filter(Q(record__private_chinese_title__icontains=search) | Q(record__private_japanese_title__icontains=search) |
            Q(record__metadata_initialized=False, record__anime__chinese_title__icontains=search) |
            Q(record__metadata_initialized=False, record__anime__japanese_title__icontains=search))
    if order == 'planned':
        query = query.annotate(undated=Case(When(planned_for='', then=Value(1)), default=Value(0), output_field=IntegerField())).order_by('undated', 'planned_for', 'priority', 'position', 'record_id')
    elif order == 'added': query = query.order_by('-enrolled_at', 'record_id')
    else: query = query.order_by('priority', 'position', 'record_id')
    count = query.count()
    page = min(page, max(1, (count + size - 1) // size))
    return {'contract_version': 'journal.watch-plans.v1', 'version': queue.revision if queue else 0,
        'count': count, 'page': page, 'page_size': size, 'sort': order, 'counts_scope': 'owner_all_plans',
        'counts': {key: counts.get(key, 0) for key in WatchPlan.State.values},
        'results': [{'record': record_summary(plan.record, request), 'plan': plan_data(plan)} for plan in query[(page - 1) * size:page * size]]}


class ReorderSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=0, max_value=9223372036854775806)
    operation_id = serializers.UUIDField()
    priority = serializers.IntegerField(min_value=1, max_value=3)
    record_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=10000)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != set(self.fields) or type(data.get('version')) is not int:
            raise serializers.ValidationError({'detail': '请提交完整队列顺序、版本与唯一编号。'})
        return super().to_internal_value(data)


def order_data(queue, priority, replayed=False):
    ids = queue.plans.filter(priority=priority, state__in=ACTIVE).order_by('position', 'record_id').values_list('record_id', flat=True)
    return {'contract_version': 'journal.watch-plan-order.v1', 'version': queue.revision,
            'priority': priority, 'record_ids': list(ids), 'replayed': replayed}


@transaction.atomic
def reorder(user, raw):
    serializer = ReorderSerializer(data=raw); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    list(UserAnimeRecord.objects.select_for_update().filter(user=user).order_by('pk').values_list('pk', flat=True))
    queue = lock_queue(user.pk, create=True)
    request_hash = digest(raw)
    if replay(queue, data['operation_id'], request_hash):
        return order_data(queue, data['priority'], True)
    if queue.revision != data['version']: raise PlanConflict()
    plans = list(queue.plans.filter(priority=data['priority'], state__in=ACTIVE))
    ids = data['record_ids']
    if len(ids) != len(set(ids)) or set(ids) != {plan.record_id for plan in plans}:
        raise serializers.ValidationError('请在未筛选的完整优先级队列中排序，不能跨档或混入其他账号作品。')
    positions = {record_id: index for index, record_id in enumerate(ids)}
    for plan in plans: plan.position = positions[plan.record_id]
    WatchPlan.objects.bulk_update(plans, ['position'])
    bump(queue)
    WatchPlanOperation.objects.create(queue=queue, operation_id=data['operation_id'], request_hash=request_hash)
    return order_data(queue, data['priority'])


class PlanBackupSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1, max_value=1)
    priority = serializers.IntegerField(min_value=1, max_value=3)
    position = serializers.IntegerField(min_value=0, max_value=2147483646)
    planned_for = serializers.CharField(allow_blank=True, max_length=10)
    note = serializers.CharField(allow_blank=True, max_length=500)
    purpose = serializers.ChoiceField(choices=WatchPlan.Purpose.values)
    state = serializers.ChoiceField(choices=WatchPlan.State.values)
    target_round_number = serializers.IntegerField(min_value=1, max_value=2147483647, allow_null=True)
    anchor_round_number = serializers.IntegerField(min_value=1, max_value=2147483647)
    history_base = serializers.IntegerField(min_value=0, max_value=2147483646)
    enrolled_at = serializers.DateTimeField()

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != set(self.fields):
            raise serializers.ValidationError({'detail': '计划备份字段不完整或包含不允许的字段。'})
        return super().to_internal_value(data)

    def validate_planned_for(self, value):
        if value: date_bounds(value)
        return value

    def validate(self, data):
        if data['target_round_number'] is None and data['purpose'] != 'rewatch':
            raise serializers.ValidationError('非重看计划必须关联本作品轮次。')
        if data['target_round_number'] is None and data['state'] not in ('pending', 'archived'):
            raise serializers.ValidationError('尚未开始的重看计划不能标为进行中或完成。')
        return data


def validate_plan_backup(value, history=None, expected_status=None):
    if value is None: return None
    serializer = PlanBackupSerializer(data=value); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if history is not None and data['state'] != 'archived':
        numbers = {row['number'] for row in history['rounds'] if not row['voided']} or {1}
        if data['history_base'] != history.get('base_revision', 0) or data['target_round_number'] is not None and data['target_round_number'] not in numbers:
            raise serializers.ValidationError('计划关联的观看轮次不存在或已被清空。')
        target = next((row for row in history['rounds'] if row['number'] == data['target_round_number']), None)
        target_status = target['status'] if target else expected_status if not history['rounds'] and data['target_round_number'] == 1 else None
        if target_status and data['state'] != {'plan': 'pending', 'on_hold': 'pending', 'watching': 'ongoing', 'completed': 'completed'}[target_status]:
            raise serializers.ValidationError('计划阶段与关联观看轮次不一致。')
    return data


def restore_plan(record, queue, value):
    if value is None: return
    data = dict(value); data.pop('version', None)
    plan = WatchPlan(record=record, queue=queue, **data)
    reconcile(plan, record)
    plan.save()
