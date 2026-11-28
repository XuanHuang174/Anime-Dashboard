"""Private manual collections. Writes lock owner -> records -> library.

Membership expresses curation only: never writes a rating, plan or watch event.
Portable UUIDs are scoped to the owner/list, not database row identifiers.
"""
import hashlib
import json
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import CurationLibrary, CurationList, CurationEntry, CurationOperation, UserAnimeRecord
from .watch_plans import record_summary

CONTRACT = 'journal.curations.v1'
MAX_LISTS, MAX_ENTRIES, MAX_TOTAL = 100, 500, 5000
MISSING = object()


class CurationConflict(APIException):
    status_code = 409
    default_detail = '片单或手账已变化，请重新读取并核对。'


def lock_owner(user):
    get_user_model().objects.select_for_update().get(pk=user.pk)


def version(user):
    return CurationLibrary.objects.filter(user=user).values_list('revision', flat=True).first() or 0


def bump(library):
    CurationLibrary.objects.filter(pk=library.pk).update(revision=F('revision') + 1)
    library.refresh_from_db(fields=['revision'])


def snapshot(record):
    data = record.effective_anime_metadata()
    return dict(title=data['chinese_title'], original_title=data['japanese_title'], period=data['airing_period'])


def entries(collection):
    return collection.entries.select_related('record__anime').order_by('position', 'pk')


def entry_data(row, request=None):
    return {'id': str(row.portable_id), 'position': row.position, 'reason': row.reason,
            'title_snapshot': row.title_snapshot, 'unavailable': row.record_id is None,
            'record': current_record(row.record, request) if row.record_id else None}


def current_record(record, request=None):
    return {**record_summary(record, request), 'personal_score': str(record.personal_score) if record.personal_score is not None else None,
            'tags': list(dict.fromkeys([*record.effective_anime_metadata()['base_tags'], *(record.custom_tags or [])]))}


def filtered_members(collection, query, request):
    """Filter the bounded, complete private collection before pagination."""
    search = query.get('search', '').strip().casefold()
    status, tag, year = (query.get(key, 'all') for key in ('status', 'tag', 'year'))
    availability = query.get('availability', 'all')
    if (len(search) > 100 or len(tag) > 100 or status not in ('all', 'plan', 'watching', 'completed', 'on_hold')
            or availability not in ('all', 'available', 'unavailable')
            or year != 'all' and (len(year) != 4 or not year.isascii() or not year.isdigit() or int(year) < 1000)):
        raise serializers.ValidationError('片单作品筛选无效。')
    members = list(entries(collection))
    collection._members = members
    rows = [entry_data(row, request) for row in members]
    tags, years, matched = set(), set(), []
    for row in rows:
        record = row['record']
        if record:
            tags.update(record['tags'])
            if record['period'][:4].isdigit(): years.add(record['period'][:4])
        title = record['title'] if record else row['title_snapshot']['title']
        original = record['japanese_title'] if record else row['title_snapshot']['original_title']
        if search and search not in title.casefold() and search not in original.casefold(): continue
        if availability != 'all' and row['unavailable'] != (availability == 'unavailable'): continue
        if status != 'all' and (not record or record['watch_status'] != status): continue
        if tag != 'all' and (not record or tag not in record['tags']): continue
        if year != 'all' and (not record or record['period'][:4] != year): continue
        matched.append(row)
    return matched, {'tags': sorted(tags), 'years': sorted(years, reverse=True)}


def list_data(row, request=None):
    members = row._members if hasattr(row, '_members') else list(entries(row))
    cover = [entry for entry in members if entry.record_id]
    selected = next((entry for entry in cover if entry.portable_id == row.cover_entry_id), None)
    selected = selected or (cover[0] if cover else None)
    return {'id': str(row.portable_id), 'title': row.title, 'description': row.description,
            'pinned': row.pinned, 'archived': row.archived, 'position': row.position,
            'cover_entry_id': str(row.cover_entry_id) if row.cover_entry_id else None,
            'cover': current_record(selected.record, request) if selected else None,
            'entry_count': len(members), 'unavailable_count': sum(entry.record_id is None for entry in members)}


def pagination(query, allowed):
    if set(query) - set(allowed) - {'page', 'page_size', 'version'} or any(len(query.getlist(k)) != 1 for k in query):
        raise serializers.ValidationError('片单查询参数无效。')
    try:
        page, size = int(query.get('page', 1)), int(query.get('page_size', 20))
        if page < 1 or not 1 <= size <= 100: raise ValueError()
        if page > 1 and 'version' not in query: raise ValueError()
        if 'version' in query and (not query['version'].isdigit() or int(query['version']) > 9223372036854775806): raise ValueError()
    except (TypeError, ValueError):
        raise serializers.ValidationError('片单分页参数无效。')
    return page, size


@transaction.atomic
def read(user, query, list_id=None, request=None):
    # Serialize a whole page with mutations, without materializing any empty state.
    lock_owner(user)
    page, size = pagination(query, ('search', 'status', 'tag', 'year', 'availability') if list_id else ('search', 'state', 'pinned'))
    rev = version(user)
    if 'version' in query and int(query['version']) != rev: raise CurationConflict()
    rows = CurationList.objects.filter(library__user=user)
    collection = None
    if list_id:
        collection = get_object_or_404(rows, portable_id=list_id)
        members, options = filtered_members(collection, query, request)
        count = len(members)
        return {'contract_version': CONTRACT, 'version': rev, 'page': page, 'page_size': size,
                'count': count, 'total_count': len(collection._members), 'filter_options': options,
                'next_page': page + 1 if page * size < count else None,
                'collection': list_data(collection, request), 'results': members[(page-1)*size:page*size]}
    else:
        state = query.get('state', 'active')
        if state not in ('active', 'archived', 'all') or query.get('pinned', 'any') not in ('any', 'true', 'false') or len(query.get('search', '')) > 80:
            raise serializers.ValidationError('片单筛选无效。')
        if state != 'all': rows = rows.filter(archived=state == 'archived')
        if query.get('pinned', 'any') != 'any': rows = rows.filter(pinned=query['pinned'] == 'true')
        if query.get('search'): rows = rows.filter(title__icontains=query['search'])
    count = rows.count()
    if collection is None:
        rows = rows.prefetch_related(Prefetch('entries', queryset=CurationEntry.objects.select_related('record__anime'), to_attr='_members'))
    result = {'contract_version': CONTRACT, 'version': rev, 'page': page, 'page_size': size,
              'count': count, 'next_page': page + 1 if page * size < count else None,
              'results': [(entry_data(row, request) if collection else list_data(row, request)) for row in rows[(page-1)*size:page*size]]}
    if collection: result['collection'] = list_data(collection, request)
    return result


class Command(serializers.Serializer):
    action = serializers.ChoiceField(choices=('create', 'create-with-members', 'create-from-review', 'update', 'delete', 'add', 'update-entry', 'remove-entry', 'relink', 'reorder'))
    version = serializers.IntegerField(min_value=0, max_value=9223372036854775806)
    operation_id = serializers.UUIDField()
    list_id = serializers.UUIDField(required=False)
    entry_id = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=80, required=False)
    description = serializers.CharField(max_length=2000, allow_blank=True, required=False, trim_whitespace=False)
    pinned = serializers.BooleanField(required=False)
    archived = serializers.BooleanField(required=False)
    cover_entry_id = serializers.UUIDField(required=False, allow_null=True)
    record_ids = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=2147483647), min_length=1, max_length=MAX_ENTRIES, required=False)
    review = serializers.DictField(required=False)
    record_id = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    entry_ids = serializers.ListField(child=serializers.UUIDField(), max_length=MAX_ENTRIES, required=False)
    reason = serializers.CharField(max_length=2000, allow_blank=True, required=False, trim_whitespace=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields) or type(data.get('version')) is not int:
            raise serializers.ValidationError({'detail': '片单操作包含无效字段或版本。'})
        if any(k in data and type(data[k]) is not bool for k in ('pinned', 'archived')) or 'record_id' in data and type(data['record_id']) is not int:
            raise serializers.ValidationError({'detail': '布尔值和作品编号类型无效。'})
        if 'record_ids' in data and (not isinstance(data['record_ids'], list) or any(type(v) is not int for v in data['record_ids'])):
            raise serializers.ValidationError({'detail': '请明确选择私人作品编号。'})
        if any(k in data and not isinstance(data[k], str) for k in ('title', 'description', 'reason')):
            raise serializers.ValidationError({'detail': '片单文字须为字符串。'})
        return super().to_internal_value(data)

    def validate(self, data):
        common = {'action', 'version', 'operation_id'}
        metadata = {'title', 'description', 'pinned', 'archived', 'cover_entry_id'}
        allowed = {'create': metadata - {'cover_entry_id'}, 'update': metadata, 'delete': set(), 'add': {'record_ids'},
                   'create-with-members': (metadata - {'cover_entry_id'}) | {'record_ids'}, 'create-from-review': (metadata - {'cover_entry_id'}) | {'record_ids', 'review'},
                   'update-entry': {'entry_id', 'reason'}, 'remove-entry': {'entry_id'}, 'relink': {'entry_id', 'record_id'}, 'reorder': {'entry_ids'}}[data['action']]
        creating = data['action'] in ('create', 'create-with-members', 'create-from-review')
        if not creating: common.add('list_id')
        if set(data) - common - allowed: raise serializers.ValidationError('此操作包含不适用的字段。')
        required = {'create': {'title'}, 'update': set(), 'delete': set(), 'add': {'record_ids'}, 'update-entry': {'entry_id', 'reason'},
                    'create-with-members': {'title', 'record_ids'}, 'create-from-review': {'title', 'record_ids', 'review'},
                    'remove-entry': {'entry_id'}, 'relink': {'entry_id', 'record_id'}, 'reorder': {'entry_ids'}}[data['action']]
        if not creating: required.add('list_id')
        if not required.issubset(data) or data['action'] == 'update' and not set(data).intersection(metadata):
            raise serializers.ValidationError('片单操作缺少必要字段。')
        for key in ('record_ids', 'entry_ids'):
            if key in data and len(data[key]) != len(set(data[key])): raise serializers.ValidationError('选择不能包含重复条目。')
        if data['action'] in ('add', 'create-with-members') and len(data.get('record_ids', [])) > 100:
            raise serializers.ValidationError('一次明确收录最多 100 部作品。')
        if data['action'] == 'create-from-review':
            raw = data['review']
            if set(raw) - {'year', 'month', 'start', 'end', 'snapshot'} or not raw.get('snapshot') or not set(raw).intersection({'year', 'month', 'start'}):
                raise serializers.ValidationError('请使用已应用期间的完整回顾依据。')
            from .watch_review import ReviewQuery
            query = ReviewQuery(data=raw); query.is_valid(raise_exception=True)
        return data


def receipt(library, list_id, request, replayed=False):
    row = library.lists.filter(portable_id=list_id).first() if list_id else None
    return {'contract_version': CONTRACT, 'version': library.revision, 'replayed': replayed,
            'list_id': str(list_id) if list_id else None, 'collection': list_data(row, request) if row else None}


@transaction.atomic
def command(user, raw, request=None):
    serializer = Command(data=raw); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    lock_owner(user)
    library = CurationLibrary.objects.filter(user=user).first()
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    previous = library.operations.filter(operation_id=data['operation_id']).first() if library else None
    if previous:
        if previous.request_hash != digest: raise CurationConflict('同一操作编号不能用于另一条请求。')
        return receipt(library, previous.list_id, request, True)
    if data['version'] != (library.revision if library else 0): raise CurationConflict()
    action = data['action']
    creating = action in ('create', 'create-with-members', 'create-from-review')
    collection = get_object_or_404(CurationList, library__user=user, portable_id=data['list_id']) if not creating else None
    if action == 'create-from-review':
        # Freeze all private source records before verifying the original review.
        # History/progress writers also lock their record; never start a read-only
        # transaction inside this write transaction.
        from . import watch_review
        locked = list(UserAnimeRecord.objects.select_for_update(of=('self',)).filter(user=user).order_by('pk').values_list('pk', flat=True)[:watch_review.LIMITS['records']+1])
        if len(locked) > watch_review.LIMITS['records']: raise watch_review.ReviewUnavailable()
        query = watch_review.ReviewQuery(data=data['review']); query.is_valid(raise_exception=True)
        period = query.validated_data['period']
        source_records, rounds, events = watch_review.read_source(user)
        watch_review.verify_snapshot(user, data['review']['snapshot'], period, watch_review.fingerprint(source_records, rounds, events))
        facts = watch_review.build_facts(source_records, rounds, events, period)
        allowed = {fact['record_id'] for fact in facts if fact['attribution'] == 'definite'}
        if set(data['record_ids']) - allowed: raise serializers.ValidationError('所选作品不属于本次确定回顾清单。')
    # All record lookup is owner-scoped. A whole batch fails before any add.
    ids = data.get('record_ids', [data['record_id']] if 'record_id' in data else [])
    records = list(UserAnimeRecord.objects.select_for_update(of=('self',)).filter(user=user, pk__in=ids).select_related('anime').order_by('pk'))
    if len(records) != len(ids): raise serializers.ValidationError('所选私人作品不存在或已移除，请重新选择。')
    if library is None: library = CurationLibrary.objects.create(user=user)
    else: library = CurationLibrary.objects.select_for_update().get(pk=library.pk)
    if creating:
        if library.lists.count() >= MAX_LISTS: raise serializers.ValidationError('片单最多 100 张。')
        collection = CurationList.objects.create(library=library, position=library.lists.count(), **{k: data[k] for k in ('title', 'description', 'pinned', 'archived') if k in data})
        if action == 'create-from-review':
            collection.description = f"创建自 {period['start']} 至 {period['end']} 的观看回顾（{'年度' if period['mode'] == 'year' else '月度' if period['mode'] == 'month' else '自选期间'}）\n" + collection.description
            if len(collection.description) > 2000: raise serializers.ValidationError('简介加上回顾来源说明不能超过 2000 字符。')
            collection.save(update_fields=['description'])
        if 'record_ids' in data:
            if CurationEntry.objects.filter(collection__library=library).count() + len(ids) > MAX_TOTAL: raise serializers.ValidationError('全部片单合计最多 5000 项。')
            record_map = {row.pk: row for row in records}
            CurationEntry.objects.bulk_create([CurationEntry(collection=collection, record=record_map[rid], position=i, title_snapshot=snapshot(record_map[rid])) for i, rid in enumerate(ids)])
    elif action == 'update':
        if data.get('cover_entry_id') and not collection.entries.filter(portable_id=data['cover_entry_id'], record__isnull=False).exists():
            raise serializers.ValidationError('封面须选择本片单内可用的作品。')
        for key in ('title', 'description', 'pinned', 'archived', 'cover_entry_id'):
            if key in data: setattr(collection, key, data[key])
        collection.save()
    elif action == 'add':
        present = set(collection.entries.filter(record_id__in=ids).values_list('record_id', flat=True))
        new = [value for value in ids if value not in present]
        if collection.entries.count() + len(new) > MAX_ENTRIES or CurationEntry.objects.filter(collection__library=library).count() + len(new) > MAX_TOTAL:
            raise serializers.ValidationError('每张片单最多 500 项，全部片单合计最多 5000 项。')
        ordered = list(collection.entries.all())
        by_id = {row.pk: row for row in records}
        for record_id in new: ordered.append(CurationEntry(collection=collection, record=by_id[record_id], title_snapshot=snapshot(by_id[record_id])))
        for index, row in enumerate(ordered): row.position = index; row.save()
    elif action == 'reorder':
        rows = {row.portable_id: row for row in collection.entries.all()}
        if set(data['entry_ids']) != set(rows): raise serializers.ValidationError('排序须包含本片单的全部条目，包括暂不可用的作品。')
        for index, entry_id in enumerate(data['entry_ids']): rows[entry_id].position = index
        CurationEntry.objects.bulk_update(list(rows.values()), ['position'])
    elif action in ('update-entry', 'remove-entry', 'relink'):
        entry = get_object_or_404(collection.entries, portable_id=data['entry_id'])
        if action == 'remove-entry':
            entry.delete()
            if collection.cover_entry_id == data['entry_id']: collection.cover_entry_id = None; collection.save()
        elif action == 'relink':
            if entry.record_id is not None or collection.entries.filter(record_id=data['record_id']).exists():
                raise serializers.ValidationError('只能明确关联失效条目，且不能重复收录。')
            entry.record = records[0]; entry.title_snapshot = snapshot(records[0]); entry.save()
        else:
            entry.reason = data['reason']; entry.save(update_fields=['reason'])
    list_id = collection.portable_id
    if action == 'delete': collection.delete()
    bump(library)
    CurationOperation.objects.create(library=library, operation_id=data['operation_id'], request_hash=digest, list_id=list_id)
    return receipt(library, list_id, request)


def preserve_deleted_record(record):
    """Runs before SET_NULL, also for admin/ORM deletion. Retains reasons/order."""
    if not CurationEntry.objects.filter(record_id=record.pk).exists(): return
    lock_owner(record.user)
    UserAnimeRecord.objects.select_for_update().filter(pk=record.pk).first()
    CurationEntry.objects.filter(record_id=record.pk).update(title_snapshot=snapshot(record))
    library = CurationLibrary.objects.select_for_update().filter(user_id=record.user_id).first()
    if library: bump(library)


def deletion_impact(user, record_id):
    get_object_or_404(UserAnimeRecord, pk=record_id, user=user)
    rows = CurationEntry.objects.filter(record_id=record_id, collection__library__user=user).select_related('collection').order_by('collection_id')
    return {'contract_version': CONTRACT, 'version': version(user), 'count': rows.count(),
            'lists': [{'id': str(row.collection.portable_id), 'title': row.collection.title} for row in rows],
            'retains_reasons_and_positions': True}


def export_backup(user, record_refs):
    result = []
    for row in CurationList.objects.filter(library__user=user).order_by('position', 'pk'):
        result.append({'id': str(row.portable_id), 'title': row.title, 'description': row.description, 'pinned': row.pinned,
                       'archived': row.archived, 'cover_entry_id': str(row.cover_entry_id) if row.cover_entry_id else None,
                       'entries': [{'id': str(entry.portable_id), 'record_ref': record_refs.get(entry.record_id),
                                    'title_snapshot': snapshot(entry.record) if entry.record_id else entry.title_snapshot,
                                    'reason': entry.reason} for entry in entries(row)]})
    return {'version': 1, 'lists': result}


class Snapshot(serializers.Serializer):
    title = serializers.CharField(max_length=200, allow_blank=True)
    original_title = serializers.CharField(max_length=200, allow_blank=True)
    period = serializers.CharField(max_length=32, allow_blank=True)


class BackupEntry(serializers.Serializer):
    id = serializers.UUIDField()
    record_ref = serializers.RegexField(r'^[A-Za-z0-9_-]{1,64}$', allow_null=True)
    title_snapshot = Snapshot()
    reason = serializers.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)


class BackupList(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=2000, allow_blank=True, trim_whitespace=False)
    pinned = serializers.BooleanField()
    archived = serializers.BooleanField()
    cover_entry_id = serializers.UUIDField(allow_null=True)
    entries = BackupEntry(many=True, max_length=MAX_ENTRIES)


def validate_backup(raw, records):
    refs = [row['record_ref'] for row in records if 'record_ref' in row]
    if len(refs) != len(set(refs)): raise serializers.ValidationError('备份作品引用重复。')
    if raw is MISSING: return raw
    if not isinstance(raw, dict) or set(raw) != {'version', 'lists'} or type(raw['version']) is not int or raw['version'] != 1 or not isinstance(raw['lists'], list) or len(raw['lists']) > MAX_LISTS:
        raise serializers.ValidationError('片单备份格式或版本无效。')
    # Do not silently accept fields that look like database/owner references.
    for row in raw['lists']:
        if not isinstance(row, dict) or set(row) != set(BackupList().fields) or type(row.get('pinned')) is not bool or type(row.get('archived')) is not bool or not isinstance(row.get('entries'), list):
            raise serializers.ValidationError('片单备份字段无效。')
        for entry in row['entries']:
            if not isinstance(entry, dict) or set(entry) != set(BackupEntry().fields) or not isinstance(entry.get('title_snapshot'), dict) or set(entry['title_snapshot']) != set(Snapshot().fields):
                raise serializers.ValidationError('片单条目备份字段无效。')
    serializer = BackupList(data=raw['lists'], many=True); serializer.is_valid(raise_exception=True)
    lists = serializer.validated_data
    if len({row['id'] for row in lists}) != len(lists) or sum(len(row['entries']) for row in lists) > MAX_TOTAL:
        raise serializers.ValidationError('片单编号重复或条目超限。')
    for row in lists:
        members = row['entries']; linked = [e['record_ref'] for e in members if e['record_ref'] is not None]
        if len({e['id'] for e in members}) != len(members) or len(linked) != len(set(linked)) or set(linked) - set(refs):
            raise serializers.ValidationError('片单成员引用无效或重复，不能按片名猜测关联。')
        if row['cover_entry_id'] and row['cover_entry_id'] not in {e['id'] for e in members}:
            raise serializers.ValidationError('片单封面引用不属于本片单。')
    return {'version': 1, 'lists': lists}


def prepare_restore(user, records, backup=MISSING):
    """Resolve to indices inside THIS file; never accept a database record id."""
    if backup is not MISSING:
        ref_indices = {row['record_ref']: i for i, row in enumerate(records) if 'record_ref' in row}
        lists = backup['lists']
        return [{**row, 'entries': [{**e, 'record_index': ref_indices.get(e['record_ref'])} for e in row['entries']]} for row in lists]
    existing = list(UserAnimeRecord.objects.filter(user=user).select_related('anime'))
    fields = ('chinese_title', 'japanese_title', 'airing_period')
    old_keys = {}
    for record in existing:
        key = tuple(record.effective_anime_metadata()[field] for field in fields)
        old_keys.setdefault(key, []).append(record.pk)
    incoming = {tuple(row['anime'][field] for field in fields): i for i, row in enumerate(records)}
    indices = {ids[0]: incoming[key] for key, ids in old_keys.items() if len(ids) == 1 and key in incoming}
    saved = export_backup(user, {})['lists']
    live = {(str(e.collection.portable_id), str(e.portable_id)): indices.get(e.record_id) for e in CurationEntry.objects.filter(collection__library__user=user).select_related('collection')}
    return [{**row, 'entries': [{**e, 'record_index': live[(row['id'], e['id'])]} for e in row['entries']]} for row in saved]


def restore_impact(user, records, backup=MISSING):
    prepared = prepare_restore(user, records, backup)
    old = set(CurationList.objects.filter(library__user=user).values_list('portable_id', flat=True))
    new = {UUID(str(row['id'])) for row in prepared}
    members = [e for row in prepared for e in row['entries']]
    return {'mode': 'preserve' if backup is MISSING else 'replace', 'lists_in_file': 0 if backup is MISSING else len(new),
            'added_lists': len(new-old), 'replaced_lists': 0 if backup is MISSING else len(new & old),
            'removed_lists': len(old-new), 'preserved_lists': len(old) if backup is MISSING else 0,
            'entries': len(members), 'linked_entries': sum(e['record_index'] is not None for e in members),
            'unavailable_entries': sum(e['record_index'] is None for e in members)}


def apply_restore(user, prepared, replacement_records, supplied=False):
    library = CurationLibrary.objects.select_for_update().filter(user=user).first()
    if library is None and not prepared and not supplied: return
    if library is None: library = CurationLibrary.objects.create(user=user)
    library.lists.all().delete()
    for position, row in enumerate(prepared):
        collection = CurationList.objects.create(library=library, portable_id=row['id'], position=position,
            **{k: row[k] for k in ('title', 'description', 'pinned', 'archived', 'cover_entry_id')})
        CurationEntry.objects.bulk_create([CurationEntry(collection=collection, portable_id=e['id'], position=i,
            record=replacement_records[e['record_index']] if e['record_index'] is not None else None,
            title_snapshot=e['title_snapshot'], reason=e['reason']) for i, e in enumerate(row['entries'])])
    bump(library)
