"""Owner-scoped watch ledger. Observed writes are never claimed as viewing dates."""
import calendar
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from django.db import transaction
from django.db.models import F, Max, Q
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import UserAnimeRecord, WatchRound, WatchEvent

TRACKED = ('watch_status', 'watched_eps', 'total_eps')
ROUND_FIELDS = ('number', 'is_current', 'voided', 'origin', 'status', 'watched_eps', 'total_eps',
                'started_on', 'completed_on', 'rating', 'rating_origin', 'note')
_observation_source = ContextVar('watch_observation_source', default='record_update')


@contextmanager
def watch_observation(source):
    token = _observation_source.set(source)
    try:
        yield
    finally:
        _observation_source.reset(token)


class HistoryConflict(APIException):
    status_code = 409
    default_detail = '观看记录已变化，请重新读取历程后再操作。草稿无需丢弃。'


def date_bounds(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9]\d{3}(?:-\d{2}(?:-\d{2})?)?', value):
        raise serializers.ValidationError('日期请使用 YYYY、YYYY-MM 或 YYYY-MM-DD；未记录请留空。')
    try:
        parts = list(map(int, value.split('-')))
        year = parts[0]
        if len(parts) == 1:
            return date(year, 1, 1), date(year, 12, 31)
        month = parts[1]
        if len(parts) == 2:
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        return date(*parts), date(*parts)
    except ValueError:
        raise serializers.ValidationError('日期不存在，请检查年月日。') from None


def validate_dates(start, end):
    first = date_bounds(start) if start else None
    last = date_bounds(end) if end else None
    if first and last and first[0] > last[1]:
        raise serializers.ValidationError('完成日期不能早于开始日期。')


def record_state(record):
    return {key: getattr(record, key) for key in TRACKED}


def round_state(row):
    result = {key: getattr(row, key) for key in ROUND_FIELDS}
    result['rating'] = str(row.rating) if row.rating is not None else None
    return result


def ensure_round(record):
    row = record.watch_rounds.filter(is_current=True).first()
    if row is None:
        row = WatchRound.objects.create(record=record, number=1, status=record.watch_status,
            watched_eps=record.watched_eps, total_eps=record.total_eps)
    return row


def state(record, row):
    return {'record': record_state(record), 'round': round_state(row)}


def append_event(record, kind, before, after, **extra):
    record.watch_revision += 1
    UserAnimeRecord.objects.filter(pk=record.pk).update(watch_revision=record.watch_revision)
    return WatchEvent.objects.create(record=record, revision=record.watch_revision, kind=kind,
                                    before=before, after=after, **extra)


def observe_change(previous, record):
    """Called inside the model's row lock and transaction; metadata changes skip it."""
    if record_state(previous) == record_state(record):
        return
    row = ensure_round(previous)
    before = state(previous, row)
    row.status = record.watch_status
    row.watched_eps, row.total_eps = record.watched_eps, record.total_eps
    if previous.watch_status != 'completed' and record.watch_status == 'completed':
        row.rating, row.rating_origin = record.personal_score, 'completion_snapshot'
    row.save()
    append_event(record, 'change', before, state(record, row), source=_observation_source.get())


EVENT_GROUPS = {'all': None, 'progress': ('change', 'transition'), 'rewatch': ('start-rewatch',),
                'notes': ('note', 'memory'), 'corrections': ('update-round', 'undo')}


def event_payload(e):
    return {'revision': e.revision, 'kind': e.kind, 'source': e.source, 'occurred_on': e.occurred_on,
            'note': e.note, 'before': e.before, 'after': e.after, 'recorded_at': e.recorded_at.isoformat()}


def read_history(record, page=1, page_size=20, category='all', compact=False):
    rounds = list(record.watch_rounds.all())
    rows = [round_state(row) for row in rounds]
    if not rows:
        # A virtual baseline keeps GET read-only and historical dates unknown.
        rows = [dict(number=1, is_current=True, voided=False, origin='baseline', status=record.watch_status,
            watched_eps=record.watched_eps, total_eps=record.total_eps, started_on='', completed_on='',
            rating=None, rating_origin='', note='')]
    events = record.watch_events.order_by('-revision')
    total_count = events.count()
    if compact:
        targets = record.watch_events.filter(kind='undo').annotate(target=F('revision') - 1).values('target')
        events = events.exclude(revision__in=targets)
    if EVENT_GROUPS[category]:
        match = Q(kind__in=EVENT_GROUPS[category])
        if compact:
            originals = record.watch_events.filter(kind__in=EVENT_GROUPS[category]).annotate(undo_revision=F('revision') + 1).values('undo_revision')
            match |= Q(kind='undo', revision__in=originals)
        events = events.filter(match)
    count = events.count()
    page = min(page, max(1, (count + page_size - 1) // page_size))
    selected = list(events[(page - 1) * page_size:page * page_size])
    originals = {e.revision: e for e in record.watch_events.filter(revision__in=[e.revision - 1 for e in selected if compact and e.kind == 'undo'])}
    payload = []
    for event in selected:
        item = event_payload(event)
        if event.revision - 1 in originals:
            item['undone_event'] = event_payload(originals[event.revision - 1])
        payload.append(item)
    return {'contract_version': 'journal.watch-history.v1', 'version': record.watch_revision, 'rounds': rows,
        'base_revision': record.watch_history_base, 'total_count': total_count, 'category': category, 'compact': compact,
        'completed_rounds': sum(row['status'] == 'completed' and not row['voided'] for row in rows),
        'rewatch_count': max(0, sum(not row['voided'] for row in rows) - 1),
        'count': count, 'page': page, 'page_size': page_size,
        'events': payload}


class CommandSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=0, max_value=2147483647)
    operation_id = serializers.UUIDField()
    action = serializers.ChoiceField(choices=('transition', 'start-rewatch', 'update-round', 'note', 'memory', 'undo', 'clear'))
    confirm_clear = serializers.BooleanField(required=False)
    watch_status = serializers.ChoiceField(choices=UserAnimeRecord.WatchStatus.values, required=False)
    watched_eps = serializers.IntegerField(min_value=0, required=False)
    total_eps = serializers.IntegerField(min_value=0, required=False)
    round_number = serializers.IntegerField(min_value=1, required=False)
    started_on = serializers.CharField(allow_blank=True, max_length=10, required=False)
    completed_on = serializers.CharField(allow_blank=True, max_length=10, required=False)
    occurred_on = serializers.CharField(allow_blank=True, max_length=10, required=False)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, allow_null=True, required=False)
    note = serializers.CharField(allow_blank=True, max_length=2000, required=False)
    target_revision = serializers.IntegerField(min_value=1, required=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields) or type(data.get('version')) is not int:
            raise serializers.ValidationError({'detail': '请提交合法观看操作、版本与唯一操作编号。'})
        return super().to_internal_value(data)

    def validate(self, data):
        allowed = {
            'transition': {'watch_status', 'watched_eps', 'total_eps', 'occurred_on', 'note'},
            'start-rewatch': {'started_on', 'note'},
            'update-round': {'round_number', 'started_on', 'completed_on', 'rating', 'note'},
            'note': {'note', 'occurred_on'}, 'memory': {'note', 'occurred_on'}, 'undo': {'target_revision'},
            'clear': {'confirm_clear'},
        }[data['action']]
        if set(data) - {'action', 'operation_id', 'version'} - allowed:
            raise serializers.ValidationError('此操作包含不适用的字段。')
        for key in ('started_on', 'completed_on', 'occurred_on'):
            if data.get(key): date_bounds(data[key])
        if data['action'] in ('note', 'memory') and not data.get('note'):
            raise serializers.ValidationError('随记内容不能为空。')
        if data['action'] == 'memory' and not data.get('occurred_on'):
            raise serializers.ValidationError('补录往事请填写实际日期或大致年月。')
        if data['action'] == 'undo' and 'target_revision' not in data:
            raise serializers.ValidationError('请选择需要撤销的操作。')
        if data['action'] == 'clear' and data.get('confirm_clear') is not True:
            raise serializers.ValidationError('请明确确认清空本作品的观看历程。')
        if data['action'] == 'update-round' and ('round_number' not in data or not set(data).intersection(('started_on', 'completed_on', 'rating', 'note'))):
            raise serializers.ValidationError('请选择轮次及至少一项更正内容。')
        if data['action'] == 'transition' and not set(data).intersection(TRACKED):
            raise serializers.ValidationError('请填写状态或进度变化。')
        return data


@transaction.atomic
def execute(record_id, user, raw):
    command = CommandSerializer(data=raw); command.is_valid(raise_exception=True)
    data = command.validated_data
    from django.shortcuts import get_object_or_404
    record = get_object_or_404(UserAnimeRecord.objects.select_for_update(), pk=record_id, user=user)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if record.watch_clear_operation == data['operation_id']:
        if record.watch_clear_hash != digest: raise HistoryConflict('同一操作编号不能用于不同内容。')
        return {**read_history(record), 'replayed': True}
    existing = record.watch_events.filter(operation_id=data['operation_id']).first()
    if existing:
        if existing.request_hash != digest: raise HistoryConflict('同一操作编号不能用于不同内容。')
        return {**read_history(record), 'replayed': True}
    if data['version'] != record.watch_revision: raise HistoryConflict()
    if data['action'] == 'clear':
        # Remove private content, retaining only a content-free receipt. Never
        # rewind the concurrency version or replay a pre-clear operation.
        record.watch_events.all().delete()
        record.watch_rounds.all().delete()
        record.watch_revision += 1
        record.watch_history_base = record.watch_revision
        record.watch_clear_operation, record.watch_clear_hash = data['operation_id'], digest
        UserAnimeRecord.objects.filter(pk=record.pk).update(watch_revision=record.watch_revision,
            watch_history_base=record.watch_history_base, watch_clear_operation=data['operation_id'], watch_clear_hash=digest)
        ensure_round(record)
        return {**read_history(record), 'replayed': False}
    row = ensure_round(record)
    before = state(record, row)
    action = data['action']
    if action == 'transition':
        for key in TRACKED:
            if key in data: setattr(record, key, data[key])
        if record_state(record) == before['record']:
            raise serializers.ValidationError('状态和进度没有变化；记录感受请使用随记。')
        if record.total_eps and record.watched_eps > record.total_eps:
            raise serializers.ValidationError('已观看集数不能超过总集数。')
        row.status, row.watched_eps, row.total_eps = record.watch_status, record.watched_eps, record.total_eps
        if before['record']['watch_status'] != 'completed' and record.watch_status == 'completed':
            row.rating, row.rating_origin = record.personal_score, 'completion_snapshot'
            if data.get('occurred_on'): row.completed_on = data['occurred_on']
        if before['record']['watch_status'] == 'plan' and record.watch_status == 'watching' and data.get('occurred_on'):
            row.started_on = data['occurred_on']
    elif action == 'start-rewatch':
        if row.status != 'completed':
            raise HistoryConflict('请先完成当前轮次，再开始重看；更正进度请使用状态或进度修改。')
        row.is_current = False; row.save(update_fields=['is_current'])
        number = record.watch_rounds.aggregate(value=Max('number'))['value'] + 1
        row = WatchRound(record=record, number=number, origin='observed', status='watching',
            total_eps=record.total_eps, started_on=data.get('started_on', ''), note=data.get('note', ''))
        record.watch_status, record.watched_eps = 'watching', 0
    elif action == 'update-round':
        row = get_object_or_404(record.watch_rounds, number=data['round_number'], voided=False)
        before = state(record, row)
        for key in ('started_on', 'completed_on', 'rating', 'note'):
            if key in data: setattr(row, key, data[key])
        if 'rating' in data: row.rating_origin = 'manual' if data['rating'] is not None else ''
    elif action == 'undo':
        target = record.watch_events.filter(revision=data['target_revision']).first()
        if not target or target.revision != record.watch_revision or target.kind == 'undo':
            raise HistoryConflict('只能撤销最近一次未被后续历程更改的操作；较早记录可单独更正。')
        if record_state(record) != target.after['record']:
            raise HistoryConflict()
        target_round = target.after['round']
        row = get_object_or_404(record.watch_rounds, number=target_round['number'])
        if round_state(row) != target_round: raise HistoryConflict()
        before = state(record, row)
        if target.before['round']['number'] != row.number:
            row.is_current = False; row.voided = True; row.save()
        row, _ = WatchRound.objects.update_or_create(record=record, number=target.before['round']['number'], defaults={k:v for k,v in target.before['round'].items() if k != 'number'})
        for key, value in target.before['record'].items(): setattr(record, key, value)
    validate_dates(row.started_on, row.completed_on)
    row.save()
    # Avoid the automatic observer; this command supplies richer event metadata.
    UserAnimeRecord.objects.filter(pk=record.pk).update(**record_state(record))
    if record_state(record) != before['record']:
        from django.utils import timezone
        UserAnimeRecord.objects.filter(pk=record.pk).update(updated_at=timezone.now())
    append_event(record, action, before, state(record, row), source='history_command',
        occurred_on=data.get('occurred_on', ''), note=data.get('note', ''), operation_id=data['operation_id'], request_hash=digest)
    return {**read_history(record), 'replayed': False}


class RecordStateSerializer(serializers.Serializer):
    watch_status = serializers.ChoiceField(choices=UserAnimeRecord.WatchStatus.values)
    watched_eps = serializers.IntegerField(min_value=0)
    total_eps = serializers.IntegerField(min_value=0)

    def validate(self, data):
        if data['total_eps'] and data['watched_eps'] > data['total_eps']:
            raise serializers.ValidationError('历程进度超过总集数。')
        return data


class RoundBackupSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1)
    is_current = serializers.BooleanField()
    voided = serializers.BooleanField()
    origin = serializers.ChoiceField(choices=('baseline', 'observed'))
    status = serializers.ChoiceField(choices=UserAnimeRecord.WatchStatus.values)
    watched_eps = serializers.IntegerField(min_value=0)
    total_eps = serializers.IntegerField(min_value=0)
    started_on = serializers.CharField(allow_blank=True, max_length=10)
    completed_on = serializers.CharField(allow_blank=True, max_length=10)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10, allow_null=True)
    rating_origin = serializers.ChoiceField(choices=('', 'manual', 'completion_snapshot'))
    note = serializers.CharField(allow_blank=True, max_length=2000)

    def validate(self, data):
        validate_dates(data['started_on'], data['completed_on'])
        if data['total_eps'] and data['watched_eps'] > data['total_eps']:
            raise serializers.ValidationError('轮次进度超过总集数。')
        if data['voided'] and data['is_current']:
            raise serializers.ValidationError('已撤销轮次不能作为当前轮次。')
        return data


class StateBackupSerializer(serializers.Serializer):
    record = RecordStateSerializer()
    round = RoundBackupSerializer()


class EventBackupSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=('change', 'transition', 'start-rewatch', 'update-round', 'note', 'memory', 'undo'))
    source = serializers.ChoiceField(choices=('record_update', 'history_command', 'backup_restore', 'collection_import'))
    occurred_on = serializers.CharField(allow_blank=True, max_length=10)
    note = serializers.CharField(allow_blank=True, max_length=2000)
    before = StateBackupSerializer()
    after = StateBackupSerializer()
    recorded_at = serializers.DateTimeField()
    operation_id = serializers.UUIDField(allow_null=True)
    request_hash = serializers.RegexField(r'^(?:[a-f0-9]{64})?$', allow_blank=True)

    def validate_occurred_on(self, value):
        if value: date_bounds(value)
        return value


class HistoryBackupSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1, max_value=1)
    revision = serializers.IntegerField(min_value=0, max_value=2147483646)
    base_revision = serializers.IntegerField(min_value=0, max_value=2147483646, default=0)
    rounds = RoundBackupSerializer(many=True, max_length=1000)
    events = EventBackupSerializer(many=True, max_length=10000)

    def validate(self, data):
        rounds, events = data['rounds'], data['events']
        numbers = {row['number'] for row in rounds}
        if len(numbers) != len(rounds) or (rounds and sum(row['is_current'] for row in rounds) != 1):
            raise serializers.ValidationError('轮次重复或当前轮次不唯一。')
        if data['base_revision'] > data['revision'] or len(events) != data['revision'] - data['base_revision'] or any(event['revision'] != index for index, event in enumerate(events, data['base_revision'] + 1)):
            raise serializers.ValidationError('历程版本不连续或事件不完整。')
        operations = [e['operation_id'] for e in events if e['operation_id']]
        if len(set(operations)) != len(operations):
            raise serializers.ValidationError('历程操作编号重复。')
        for event in events:
            if any(event[side]['round']['number'] not in numbers for side in ('before', 'after')):
                raise serializers.ValidationError('事件引用了不存在的轮次。')
        if not rounds and data['revision']:
            raise serializers.ValidationError('缺少观看轮次。')
        return data


def validate_backup(value, expected):
    serializer = HistoryBackupSerializer(data=value); serializer.is_valid(raise_exception=True)
    result = dict(serializer.data)
    current = next((row for row in result['rounds'] if row['is_current']), None)
    if current and {'watch_status': current['status'], 'watched_eps': current['watched_eps'], 'total_eps': current['total_eps']} != expected:
        raise serializers.ValidationError('备份的当前轮次与手账状态或进度不一致。')
    if result['events'] and result['events'][-1]['after']['record'] != expected:
        raise serializers.ValidationError('备份最后的历程与当前状态不一致。')
    return result


def export_history(record):
    return {'version': 1, 'revision': record.watch_revision, 'base_revision': record.watch_history_base,
        'rounds': [round_state(row) for row in record.watch_rounds.all()],
        'events': [{'revision': e.revision, 'kind': e.kind, 'source': e.source, 'occurred_on': e.occurred_on,
            'note': e.note, 'before': e.before, 'after': e.after, 'recorded_at': e.recorded_at.isoformat(),
            'operation_id': str(e.operation_id) if e.operation_id else None, 'request_hash': e.request_hash}
            for e in record.watch_events.all()]}


def restore_history(record, data):
    # No incoming database IDs are accepted: all children belong to this new
    # owner-scoped record, and recorded_at remains the original observation time.
    WatchRound.objects.bulk_create([WatchRound(record=record, **row) for row in data['rounds']])
    WatchEvent.objects.bulk_create([WatchEvent(record=record, **row) for row in data['events']])
    record.watch_revision = data['revision']
    record.watch_history_base = data.get('base_revision', 0)
    UserAnimeRecord.objects.filter(pk=record.pk).update(watch_revision=record.watch_revision, watch_history_base=record.watch_history_base)
