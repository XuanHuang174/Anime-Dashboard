"""Owner-only, durable, bounded tag maintenance. Never changes shared Anime rows."""
from collections import Counter
from datetime import timedelta
import hashlib
import json
import re
from types import SimpleNamespace
import unicodedata

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import QueryDict
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import TagCleanupJob, TagCleanupItem, UserAnimeRecord, UserProfile, UserQuickFilter, UserAnimeTag
from .tag_cleanup_rebuild import rebuild_plan, retention_reasons, tag_key
from recommendations.features import feature_tags, MANAGEMENT_TAGS
from recommendations.identity import normalized_title
from recommendations.models import RecommendationAnime
from recommendations.resolution import resolved_records
from recommendations.taxonomy import canonical_tag, AVAILABLE_TAGS, TAXONOMY_VERSION

VERSION = f'tag-cleanup.v2:{TAXONOMY_VERSION}'
BATCH = 50
FORMATS = {'TV', 'OVA', 'OAD', '剧场版', '泡面番', 'WEB', '短片', '动态漫画', 'NSFW', 'R18', 'R-18'}
ACTIVE = ('scanning', 'applying', 'undoing')


class CleanupConflict(APIException):
    status_code = 409
    default_detail = '任务状态已变化，请重新读取。'


class CleanupExpired(APIException):
    status_code = 410
    default_detail = '预览或撤销记录已过期，请重新扫描。'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({'non_field_errors': ['包含未支持的字段。']})
        return super().to_internal_value(data)


class FilterInput(StrictSerializer):
    search = serializers.CharField(max_length=200, required=False, allow_blank=True)
    tag = serializers.CharField(max_length=200, required=False, allow_blank=True)
    year = serializers.RegexField(r'^(?:all|[1-9][0-9]{3})$', required=False)
    status = serializers.ChoiceField(choices=('all', 'plan', 'watching', 'completed', 'on_hold'), required=False)
    type_filter = serializers.ChoiceField(choices=('all', 'yuri', 'moe', 'comedy', 'og', 'special'), required=False)
    quick_filter = serializers.IntegerField(min_value=1, required=False)


class CreateInput(StrictSerializer):
    request_id = serializers.UUIDField()
    scope = serializers.ChoiceField(choices=('filtered', 'all'))
    filters = FilterInput(required=False, default=dict)
    mode = serializers.ChoiceField(choices=('conservative', 'rebuild'), default='conservative')
    keep_personal = serializers.BooleanField(default=True)


class StepInput(StrictSerializer):
    phase = serializers.ChoiceField(choices=('scan', 'apply', 'undo'))
    cursor = serializers.IntegerField(min_value=0)


class ApplyInput(StrictSerializer):
    excluded_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=10000, default=list)
    revision = serializers.IntegerField(min_value=0, required=False)
    confirm_personal_removal = serializers.BooleanField(default=False)


class ChoiceInput(StrictSerializer):
    name = serializers.CharField(max_length=200, trim_whitespace=False)
    action = serializers.ChoiceField(choices=('keep', 'personal', 'remove'))


class DecisionsInput(StrictSerializer):
    record_id = serializers.IntegerField(min_value=1)
    revision = serializers.IntegerField(min_value=0)
    choices = ChoiceInput(many=True, allow_empty=False, max_length=400)


def validated(serializer, data):
    value = serializer(data=data)
    value.is_valid(raise_exception=True)
    return value.validated_data


def protections(user):
    profile = UserProfile.objects.filter(user=user).first()
    names = {name: ['个人常用预设'] for name in (profile.tag_presets if profile else [])}
    for row in UserQuickFilter.objects.filter(user=user).values('tags', 'exclude_tags'):
        for key, reason in (('tags', '快捷筛选包含条件'), ('exclude_tags', '快捷筛选排除条件')):
            for name in row[key] or []:
                if reason not in names.setdefault(name, []): names[name].append(reason)
    return names


def sources(user, ids, *, lock=False):
    rows = resolved_records(user, record_ids=ids, include_profile=False)
    safe = {row['resolved_bangumi_id'] for row in rows if row['identity_state'] == 'linked'}
    catalog_rows = RecommendationAnime.objects.filter(bangumi_id__in=safe, nsfw=False).order_by('pk')
    if lock: catalog_rows = catalog_rows.select_for_update()
    catalog = {row['bangumi_id']: row for row in catalog_rows.values('bangumi_id', 'tags', 'updated_at')}
    return {row['pk']: {'id': row['resolved_bangumi_id'], 'state': row['identity_state'],
            'version': catalog[row['resolved_bangumi_id']]['updated_at'].isoformat() if row['identity_state'] == 'linked' and row['resolved_bangumi_id'] in catalog else None,
            'tags': [tag for tag in AVAILABLE_TAGS if tag in feature_tags(catalog.get(row['resolved_bangumi_id'], {}).get('tags', []))] if row['identity_state'] == 'linked' else []} for row in rows}


def tag_state(record):
    return [record.effective_anime_metadata()['base_tags'], record.custom_tags,
            sorted((preference.name, preference.color) for preference in record.tag_preferences.all())]


def conservative_plan(record, saved, source):
    raw_base = record.private_base_tags if record.metadata_initialized else record.anime.base_tags
    if not isinstance(raw_base, list) or not isinstance(record.custom_tags, list) or any(not isinstance(tag, str) for tag in record.custom_tags):
        return None
    metadata = record.effective_anime_metadata()
    base = metadata['base_tags']
    if not isinstance(base, list) or len(base) > 200 or any(not isinstance(tag, str) or len(tag) > 200 for tag in base):
        return None
    protected_names = set(saved) | set(record.custom_tags or []) | {p.name for p in record.tag_preferences.all()}
    protected_keys = {unicodedata.normalize('NFKC', tag).strip().casefold() for tag in protected_names}
    titles = {normalized_title(t) for t in (metadata['chinese_title'], metadata['japanese_title']) if t}
    studios = {normalized_title(t) for t in re.split(r'[,，、/]', metadata['studio'] or '') if t.strip()}
    after, changes, protected, uncertain = [], [], list(record.custom_tags), []
    for raw in base:
        name = unicodedata.normalize('NFKC', raw).strip()
        canonical = canonical_tag(name)
        reason = None
        if name.casefold() in protected_keys or name in FORMATS or name in MANAGEMENT_TAGS or re.fullmatch(r'[二三四五六七八九十\d]+刷', name):
            value = raw
            protected.append(raw)
        elif canonical:
            value = canonical
            if value != raw: changes.append({'kind': 'normalize', 'from': raw, 'to': value})
        elif re.fullmatch(r'[1-9][0-9]{3}(?:年.*|[-/]\d{1,2}(?:[-/]\d{1,2})?)?', name):
            value, reason = None, '年份信息'
        elif normalized_title(name) in titles:
            value, reason = None, '与作品名称相同'
        elif name and normalized_title(name) in studios:
            value, reason = None, '与制作公司相同'
        else:
            value = raw
            uncertain.append(raw)
        if reason: changes.append({'kind': 'remove', 'from': raw, 'reason': reason})
        if value is not None:
            if value not in after: after.append(value)
            else: changes.append({'kind': 'deduplicate', 'from': raw})
    for tag in AVAILABLE_TAGS:
        if tag in source.get('tags', []) and tag not in after and not any(canonical_tag(value) == tag for value in after):
            after.append(tag); changes.append({'kind': 'add', 'to': tag, 'reason': '已确认作品的本地标准题材'})
    inputs = [tag_state(record), sorted(saved), metadata['chinese_title'], metadata['japanese_title'], metadata['studio'], source]
    return dict(title=metadata['chinese_title'][:200], before=base, after=after, changes=changes,
        protected=list(dict.fromkeys(protected)), uncertain=list(dict.fromkeys(uncertain)),
        source_note='已读取本地标准题材' if source.get('tags') and source.get('state') == 'linked' else '暂无当前可用的标准题材，仅整理已有标签',
        before_hash=digest(inputs), plan_hash=digest([after, changes, VERSION]),
        status='proposed' if after != base else 'unchanged')


def plan(record, saved, source, mode='conservative', keep_personal=True, decisions=None):
    # Validate legacy data before building either policy; malformed rows never get rewritten.
    proposal = conservative_plan(record, saved, source)
    if proposal is None or len(record.custom_tags) > 200 or any(len(tag) > 200 for tag in record.custom_tags): return None
    if mode == 'rebuild':
        proposal = {'title': proposal['title'], **rebuild_plan(record, saved, source, keep_personal, decisions or {})}
    else:
        reasons = retention_reasons(record, saved)
        colors = sorted((row.name, row.color) for row in record.tag_preferences.all())
        proposal['details'] = {'before_custom': record.custom_tags, 'after_custom': record.custom_tags,
            'before_colors': colors, 'after_colors': colors, 'source': source, 'taxonomy_version': TAXONOMY_VERSION,
            'candidates': [{'name': name, 'reasons': reasons.get(tag_key(name), ['用途未知，保守保留']),
                            'fixed': True, 'action': 'keep', 'personal': name in proposal['protected']}
                           for name in dict.fromkeys(proposal['protected'] + proposal['uncertain'])]}
        proposal['personal_removed'] = 0
    if len(proposal['after']) > 200 or len(proposal['details']['after_custom']) > 200: return None
    metadata = record.effective_anime_metadata()
    proposal['before_hash'] = digest([tag_state(record), saved, metadata['chinese_title'], metadata['japanese_title'], metadata['studio'], source])
    proposal['plan_hash'] = digest([proposal['after'], proposal['details'], mode, keep_personal, VERSION])
    return proposal


def summary(job):
    counts = Counter(job.items.values_list('status', flat=True))
    extra = job.items.aggregate(
        protected_records=Count('id', filter=~Q(protected=[])),
        needs_review=Count('id', filter=~Q(uncertain=[])),
        change_records=Count('id', filter=~Q(changes=[])),
        personal_removals=Sum('personal_removed'),
    )
    extra['personal_removals'] = extra['personal_removals'] or 0
    return {'id': str(job.id), 'status': job.status, 'scope': job.scope, 'filters': job.filters,
        'mode': job.mode, 'keep_personal': job.keep_personal, 'revision': job.revision,
        'obsolete': job.rule_version != VERSION and job.status in ('scanning', 'ready'),
        'total': len(job.record_ids), 'scan_cursor': job.scan_cursor, 'apply_cursor': job.apply_cursor,
        'undo_cursor': job.undo_cursor, 'selected_count': len(job.selection), 'counts': dict(counts), **extra,
        'created_at': job.created_at, 'expires_at': job.expires_at,
        'expired': job.expires_at <= timezone.now(), 'rule_version': job.rule_version}


def redact(jobs):
    jobs.items.update(before=[], after=[], changes=[], protected=[], uncertain=[], details={}, personal_removed=0, before_hash='', plan_hash='', after_hash='', protection_hash='', title='')
    jobs.record_ids = []; jobs.selection = []; jobs.filters = {}; jobs.status = 'expired'
    jobs.save(update_fields=['record_ids', 'selection', 'filters', 'status'])


def prune(user):
    keep = list(TagCleanupJob.objects.filter(user=user, completed_at__isnull=False).exclude(status='expired')
                .order_by('-completed_at').values_list('pk', flat=True)[:5])
    obsolete = TagCleanupJob.objects.filter(user=user).exclude(status='expired').filter(
        Q(expires_at__lte=timezone.now()) | (Q(completed_at__isnull=False) & ~Q(pk__in=keep)))
    for job in obsolete: redact(job)


def job_rows(user, ids, *, lock=False):
    rows = UserAnimeRecord.objects.filter(user=user, pk__in=ids).select_related('anime').prefetch_related('tag_preferences')
    if lock: rows = rows.select_for_update(of=('self',))
    return {record.pk: record for record in rows}


def save_tags(record, tags):
    fields = ['private_base_tags']
    if not record.metadata_initialized:
        record.initialize_private_metadata()
        fields += ['metadata_initialized', 'private_chinese_title', 'private_japanese_title', 'private_airing_period',
            'private_studio', 'private_episodes', 'private_description', 'private_poster_url', 'private_poster_image', 'private_baike_url']
    record.private_base_tags = list(tags)
    # Maintenance has its own timestamps; do not reorder the journal's user edits.
    record.save(update_fields=fields)


def save_state(record, tags, details, side):
    save_tags(record, tags)
    if not details: return  # Already applied v1 tasks retain their original undo behavior.
    custom = details[f'{side}_custom']
    if record.custom_tags != custom:
        record.custom_tags = list(custom); record.save(update_fields=['custom_tags'])
    wanted = dict(details[f'{side}_colors'])
    existing = {row.name: row for row in record.tag_preferences.all()}
    for name, row in existing.items():
        if name not in wanted: row.delete()
        elif row.color != wanted[name]: row.color = wanted[name]; row.save(update_fields=['color'])
    for name, color in wanted.items():
        if name not in existing: UserAnimeTag.objects.create(record=record, name=name, color=color)
    getattr(record, '_prefetched_objects_cache', {}).pop('tag_preferences', None)


ITEM_FIELDS = ('record_id', 'title', 'before', 'after', 'changes', 'protected', 'uncertain',
               'details', 'personal_removed', 'source_note', 'status', 'message')


class TagCleanupViewSet(viewsets.GenericViewSet):
    permission_classes = (IsAuthenticated,)
    queryset = TagCleanupJob.objects.all()
    lookup_value_regex = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

    def finalize_response(self, request, response, *args, **kwargs):
        from django.utils.cache import patch_vary_headers
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    def locked(self):
        get_user_model().objects.select_for_update().get(pk=self.request.user.pk)
        job = self.get_queryset().select_for_update().filter(pk=self.kwargs['pk']).first()
        if job is None:
            from django.http import Http404
            raise Http404
        if job.expires_at <= timezone.now() or job.status == 'expired': raise CleanupExpired()
        return job

    def list(self, request):
        latest = self.get_queryset().exclude(status__in=('cancelled', 'expired')).order_by('-created_at')[:6]
        return Response([summary(job) for job in latest])

    def retrieve(self, request, *args, **kwargs):
        job = self.get_object()
        data = summary(job)
        rows = job.items.all()
        mode = request.query_params.get('filter', 'all')
        if mode == 'changes': rows = rows.exclude(changes=[])
        elif mode == 'review': rows = rows.exclude(uncertain=[])
        elif mode == 'protected': rows = rows.exclude(protected=[])
        elif mode == 'conflicts': rows = rows.filter(status__in=('conflict', 'undo_conflict'))
        elif mode == 'no_source': rows = rows.filter(status='no_source')
        elif mode != 'all': raise ValidationError('未支持的预览类别。')
        try: page = int(request.query_params.get('page', 1))
        except (ValueError, TypeError): raise ValidationError('页码无效。')
        if not 1 <= page <= 10000: raise ValidationError('页码无效。')
        data.update(page=page, item_count=rows.count(), items=[])
        if not data['expired'] and job.status not in ('cancelled', 'expired'):
            data['personal_removal_records'] = dict(job.items.filter(status='proposed', personal_removed__gt=0).values_list('record_id', 'personal_removed'))
            data['items'] = list(rows.values(*ITEM_FIELDS)[(page-1)*20:page*20])
        return Response(data)

    @transaction.atomic
    def create(self, request):
        args = validated(CreateInput, request.data)
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        old = self.get_queryset().filter(request_id=args['request_id']).first()
        if old: return Response(summary(old))
        prune(request.user)
        active = self.get_queryset().filter(status__in=ACTIVE).first()
        if active and active.status == 'scanning' and active.rule_version != VERSION:
            redact(active); active = None
        if active: raise CleanupConflict('已有进行中的任务，请先继续或撤销该任务。')
        for stale in self.get_queryset().filter(status='ready'):
            stale.status = 'cancelled'; stale.save(update_fields=['status']); redact(stale)
        filters = args['filters'] if args['scope'] == 'filtered' else {}
        if filters.get('quick_filter') and not UserQuickFilter.objects.filter(user=request.user, pk=filters['quick_filter']).exists():
            raise ValidationError('快捷筛选已不存在，请重新选择范围。')
        query = UserAnimeRecord.objects.filter(user=request.user)
        if filters:
            from .views import _apply_record_filters
            params = QueryDict('', mutable=True)
            params.update({key: str(value) for key, value in filters.items()})
            query = _apply_record_filters(query, SimpleNamespace(query_params=params), request.user)
        ids = list(query.order_by('pk').values_list('pk', flat=True)[:settings.USER_ANIME_RECORD_LIMIT+1])
        if len(ids) > settings.USER_ANIME_RECORD_LIMIT: raise ValidationError('作品数量超过当前处理上限。')
        job = TagCleanupJob.objects.create(user=request.user, request_id=args['request_id'], scope=args['scope'],
            mode=args['mode'], keep_personal=args['keep_personal'],
            filters=filters, record_ids=ids, rule_version=VERSION, expires_at=timezone.now()+timedelta(days=1), status='scanning' if ids else 'ready')
        return Response(summary(job), status=201)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def apply(self, request, pk=None):
        args = validated(ApplyInput, request.data)
        job = self.locked()
        if job.status in ('applying', 'applied'): return Response(summary(job))
        if job.status != 'ready' or job.rule_version != VERSION: raise CleanupConflict('请先完成当前规则的扫描。')
        if job.mode == 'rebuild' and 'revision' not in args: raise ValidationError('请提交核对时的方案版本。')
        if 'revision' in args and args['revision'] != job.revision: raise CleanupConflict('取舍方案已变化，请重新读取后确认。')
        ids = set(job.items.filter(status='proposed').values_list('record_id', flat=True))
        excluded = set(args['excluded_ids'])
        if not excluded <= ids: raise ValidationError('选择包含当前预览之外的记录。')
        job.selection = sorted(ids - excluded)
        if not job.selection: raise ValidationError('请至少选择一部有变化的作品。')
        removed = job.items.filter(record_id__in=job.selection).aggregate(total=Sum('personal_removed'))['total'] or 0
        if removed and not args['confirm_personal_removal']:
            raise ValidationError(f'本次会移除 {removed} 个个人标记，请核对后明确确认。')
        job.status = 'applying'; job.expires_at = timezone.now()+timedelta(days=30)
        job.save(update_fields=['selection', 'status', 'expires_at'])
        return Response(summary(job))

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def decisions(self, request, pk=None):
        args = validated(DecisionsInput, request.data)
        job = self.locked()
        if job.status != 'ready' or job.mode != 'rebuild' or job.rule_version != VERSION:
            raise CleanupConflict('仅当前规则下尚未应用的标准题材方案可调整取舍。')
        if args['revision'] != job.revision: raise CleanupConflict('取舍方案已变化，请重新读取。')
        item = job.items.filter(record_id=args['record_id']).first()
        if item is None:
            from django.http import Http404
            raise Http404
        if item.status not in ('proposed', 'unchanged') or not item.details.get('candidates'):
            raise ValidationError('此作品当前没有可调整的标准题材方案。')
        candidates = {row['name']: row for row in item.details['candidates']}
        names = [row['name'] for row in args['choices']]
        if len(set(names)) != len(names): raise ValidationError('同一标签只能提交一次取舍。')
        for row in args['choices']:
            if row['name'] not in candidates or (candidates[row['name']]['fixed'] and row['action'] != 'keep'):
                raise ValidationError('只能调整当前方案中的非固定标签。')
        record = job_rows(request.user, [item.record_id], lock=True).get(item.record_id)
        source = sources(request.user, [item.record_id], lock=True).get(item.record_id, {})
        saved = protections(request.user)
        proposal = plan(record, saved, source, job.mode, job.keep_personal, item.details.get('decisions', {})) if record else None
        if not proposal or proposal['before_hash'] != item.before_hash or proposal['plan_hash'] != item.plan_hash:
            raise CleanupConflict('作品标签、保护设置或标准资料已变化，请重新扫描。')
        decisions = {**item.details.get('decisions', {}), **{row['name']: row['action'] for row in args['choices']}}
        proposal = plan(record, saved, source, job.mode, job.keep_personal, decisions)
        if proposal is None: raise ValidationError('保留后的标签数量超过上限，请减少本次保留项。')
        for key, value in proposal.items(): setattr(item, key, value)
        item.save(update_fields=list(proposal))
        job.revision += 1; job.save(update_fields=['revision'])
        return Response({'job': summary(job), 'item': {key: getattr(item, key) for key in ITEM_FIELDS}})

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def undo(self, request, pk=None):
        if request.data: raise ValidationError('撤销不接受覆盖数据。')
        job = self.locked()
        if job.status in ('undoing', 'undone'): return Response(summary(job))
        if job.status not in ('applied', 'applying'): raise CleanupConflict('当前任务没有可撤销的应用阶段。')
        job.selection = list(job.items.filter(status='applied').values_list('record_id', flat=True))
        job.undo_cursor = 0; job.status = 'undoing'
        job.save(update_fields=['selection', 'undo_cursor', 'status'])
        return Response(summary(job))

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def step(self, request, pk=None):
        args = validated(StepInput, request.data)
        job = self.locked(); phase = args['phase']; cursor_name = f'{phase}_cursor'
        cursor = getattr(job, cursor_name)
        expected = {'scan': 'scanning', 'apply': 'applying', 'undo': 'undoing'}[phase]
        if args['cursor'] < cursor: return Response(summary(job))
        if args['cursor'] != cursor or job.status != expected: raise CleanupConflict()
        if phase == 'scan' and job.rule_version != VERSION: raise CleanupConflict('扫描规则已更新，请新建扫描。')
        ids = job.record_ids if phase == 'scan' else job.selection
        batch = ids[cursor:cursor+BATCH]
        records = job_rows(request.user, batch, lock=phase != 'scan')
        saved = protections(request.user)
        origin = sources(request.user, batch, lock=phase == 'apply') if phase != 'undo' else {}
        for identity in batch:
            record = records.get(identity)
            if phase == 'scan':
                proposal = plan(record, saved, origin.get(identity, {}), job.mode, job.keep_personal) if record else None
                TagCleanupItem.objects.create(job=job, record_id=identity, **(proposal or dict(status='missing' if not record else 'unchanged', message='记录已删除、标签格式或整理后数量不符合要求，已跳过。')))
                continue
            item = job.items.get(record_id=identity)
            if not record:
                item.status = 'conflict' if phase == 'apply' else 'undo_conflict'; item.message = '记录已删除，未作修改。'
            elif phase == 'apply':
                proposal = plan(record, saved, origin.get(identity, {}), job.mode, job.keep_personal, item.details.get('decisions', {}))
                if not proposal or proposal['before_hash'] != item.before_hash or proposal['plan_hash'] != item.plan_hash or job.rule_version != VERSION:
                    item.status = 'conflict'; item.message = '标签、保护设置或标准资料已变化，请重新扫描。'
                else:
                    save_state(record, item.after, item.details, 'after')
                    item.after_hash = digest(tag_state(record)); item.protection_hash = digest(sorted(saved)); item.status = 'applied'
            else:
                if digest(tag_state(record)) != item.after_hash:
                    item.status = 'undo_conflict'; item.message = '应用后标签或配色已被编辑，已保留新修改；可下载变更清单手动恢复。'
                elif digest(sorted(saved)) != item.protection_hash:
                    item.status = 'undo_conflict'; item.message = '个人保护设置已变化，已跳过撤销，请核对变更清单。'
                else:
                    save_state(record, item.before, item.details, 'before'); item.status = 'undone'
            item.save(update_fields=['status', 'message', 'after_hash', 'protection_hash'])
        setattr(job, cursor_name, cursor+len(batch))
        if cursor+len(batch) >= len(ids):
            job.status = {'scan': 'ready', 'apply': 'applied', 'undo': 'undone'}[phase]
            if phase != 'scan' and job.completed_at is None: job.completed_at = timezone.now()
        job.save(update_fields=[cursor_name, 'status', 'completed_at'])
        if job.status in ('applied', 'undone'): prune(request.user)
        return Response(summary(job))

    @action(detail=True, methods=['get'], url_path='changes')
    def changes(self, request, pk=None):
        job = self.get_object()
        if job.expires_at <= timezone.now() or job.status in ('expired', 'cancelled'): raise CleanupExpired()
        response = Response({'job': summary(job), 'items': list(job.items.values(*ITEM_FIELDS))})
        response['Content-Disposition'] = f'attachment; filename="tag-cleanup-{job.id}.json"'
        return response
