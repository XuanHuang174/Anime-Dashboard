"""One-hop, scoped public relations. No profile, journal, title inference or IO."""
from datetime import timedelta
from contextlib import nullcontext

from django.conf import settings
from django.core import signing
from django.db.models import Max, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .browsing import catalog_revision
from .content_scope import catalog_for_scope
from .engine import RecommendationUnavailable
from .quality_readonly import readonly_snapshot
from .series_contract import KINDS
from .series_models import SeriesState, SeriesNode, SeriesHead, SeriesOverrideEvent
from .series_source import digest
from .series_store import SeriesConflict, _basis

RESULT_VERSION = 'recommendations.series.read.v1'
SALT = RESULT_VERSION
MAX_RELATIONS = 5000
PAGE_LIMIT = 200
SPECS = {row.key: row for row in KINDS}


def require_enabled():
    if getattr(settings, 'RECOMMENDATION_SERIES_ENABLED', False) is not True:
        raise RecommendationUnavailable('系列关系尚未开放。')


def revision():
    return SeriesState.objects.filter(pk=1).values_list('revision', flat=True).first() or 0


def node_revision(ids):
    return digest(list(SeriesNode.objects.filter(pk__in=ids).order_by('pk').values_list(
        'bangumi_id', 'subject_type', 'nsfw', 'metadata_revision', 'release_date', 'release_format')))


def _visible(node, catalog):
    return (node.subject_type == 2 and node.nsfw is not None and node.pk in catalog
            and node.nsfw == catalog[node.pk].nsfw)


def read_relations(user, source_id, scope, context=None, *, snapshot=True):
    require_enabled()
    if context is not None and (not isinstance(context, str) or len(context) > 4096):
        raise ValidationError('系列上下文无效。')
    with readonly_snapshot() if snapshot else nullcontext():
        source = catalog_for_scope(scope).filter(bangumi_id=source_id).first()
        if source is None:
            raise NotFound('作品不可用。')
        head = SeriesHead.objects.select_related('source', 'current', 'last_attempt').filter(source_id=source_id).first()
        node = head.source if head else SeriesNode.objects.filter(pk=source_id).first()
        if node is not None and not _visible(node, {source_id: source}):
            raise NotFound('作品不可用。')
        current = head.current if head else None
        source_rows = list(current.assertions.select_related('target').order_by('pk')[:MAX_RELATIONS + 1]) if current else []
        last = SeriesOverrideEvent.objects.filter(source_id=source_id).order_by().values('target_id').annotate(last=Max('pk')).values('last')
        events = list(SeriesOverrideEvent.objects.filter(pk__in=Subquery(last)).select_related('target')[:MAX_RELATIONS + 1])
        if len(source_rows) > MAX_RELATIONS or len(events) > MAX_RELATIONS:
            raise RecommendationUnavailable('局部系列关系超过处理上限。')
        targets = {row.target_id: row.target for row in source_rows}
        targets.update({event.target_id: event.target for event in events})
        # Preserve multiple kinds per pair, but review-only observations never serve.
        effective = {(row.target_id, row.kind): {'kind': row.kind, 'node': row.target, 'origin': 'archive'}
                     for row in source_rows if not row.requires_review and row.kind in SPECS}
        for event in events:
            if event.action == 'clear':
                continue
            effective = {key: value for key, value in effective.items() if key[0] != event.target_id}
            # New upstream observations require explicit re-review, not silent reactivation.
            if event.base_observation_id == _basis(head) and event.action == 'replace':
                effective[event.target_id, event.kind] = {'kind': event.kind, 'node': event.target, 'origin': 'manual'}
        allowed = {row.bangumi_id: row for row in catalog_for_scope(scope).filter(bangumi_id__in=targets)}
        items = []
        for value in effective.values():
            target, kind = value['node'], value['kind']
            if not _visible(target, allowed):
                continue
            entry = allowed[target.pk]
            spec = SPECS[kind]
            date = target.release_date
            items.append({'bangumi_id': target.pk, 'title_zh': entry.title_zh, 'title_jp': entry.title_jp,
                'release_date': date, 'release_precision': {4: 'year', 7: 'month', 10: 'day'}.get(len(date), 'unknown'),
                'release_format': target.release_format, 'kind': kind, 'relation_label': spec.label,
                'section': spec.section, 'origin': value['origin'], 'can_add_from_series': False})
        items.sort(key=lambda row: (row['release_date'] or '9999', row['bangumi_id'], row['kind']))
        rev, catalog_rev = revision(), catalog_revision()
        result = items[:PAGE_LIMIT]
        checked_ids = sorted({source_id} | {row['bangumi_id'] for row in result})
        nodes_rev = node_revision(checked_ids)
        binding = {'owner': user.pk, 'source': source_id, 'series_revision': rev,
                   'catalog_revision': catalog_rev, 'node_revision': nodes_rev, 'scope_revision': scope['scope_revision']}
        if context:
            try:
                old = signing.loads(context, salt=SALT, max_age=900)
            except signing.BadSignature:
                raise SeriesConflict('系列上下文无效或已过期。') from None
            if old != binding:
                raise SeriesConflict()
        coverage = current.coverage if current else (head.last_attempt.coverage if head and head.last_attempt else 'not_loaded')
        if current and current.source_at < timezone.now() - timedelta(days=45):
            coverage = 'stale'
        return {'contract_version': RESULT_VERSION, 'series_revision': rev, 'catalog_revision': catalog_rev,
            'selected_seed': {'bangumi_id': source_id, 'title_zh': source.title_zh, 'title_jp': source.title_jp},
            'coverage': coverage, 'last_attempt_status': head.last_attempt.coverage if head and head.last_attempt else 'not_loaded',
            'snapshot_version': current.snapshot_version if current else None,
            'results': result, 'limit': PAGE_LIMIT, 'truncated': len(items) > PAGE_LIMIT,
            'scope_note': '仅展示当前内容范围内、已收录且身份与分级一致的动画；关系资料可能不完整。',
            'ordering': 'release_date_not_watch_order', 'depth': 1,
            'context': signing.dumps(binding, salt=SALT, compress=True), '_node_revision': nodes_rev}


def verify_response(data):
    ids = [data['selected_seed']['bangumi_id']] + [row['bangumi_id'] for row in data['results']]
    if (data['series_revision'] != revision() or data['catalog_revision'] != catalog_revision()
            or data.pop('_node_revision') != node_revision(ids)):
        raise SeriesConflict('资料在读取期间发生变化，请重新读取。')
