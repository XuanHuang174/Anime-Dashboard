"""12D: bounded effective direct facts, never title inference or transitive families."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Max, Q, Subquery
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .browsing import catalog_revision
from .content_scope import catalog_for_scope
from .engine import RecommendationUnavailable
from .series_models import SeriesAssertion, SeriesHead, SeriesOverrideEvent, SeriesNode
from .series_read import SPECS, _visible, node_revision, revision
from .series_source import digest
from .series_store import _basis, SeriesConflict

POLICY = 'series-explore.direct.v1'
RESULT_VERSION = 'recommendations.explore.series.v1'
ANNOTATIONS_VERSION = 'recommendations.series.annotations.v1'
LIMIT = 5000
RELATED_LIMIT = 12
STRONG = frozenset(key for key, spec in SPECS.items() if spec.section != 'related')


def enabled():
    return (getattr(settings, 'RECOMMENDATION_SERIES_ENABLED', False) is True
            and getattr(settings, 'RECOMMENDATION_SERIES_EXPLORATION_ENABLED', False) is True)


def require_enabled():
    if not enabled():
        raise RecommendationUnavailable({'code': 'exploration_disabled', 'detail': '系列探索策略尚未开放，请刷新服务状态。'})


def graph(ids, scope):
    """Read all touching current facts BEFORE any display cap, in six bounded queries.

    Reverse edges retain their original direction/label; no invented inverse kind.
    Both ends must be catalogued, classified and within this request's scope.
    """
    require_enabled()
    ids = set(ids)
    current = SeriesHead.objects.exclude(current_id=None).values('current_id')
    raw = list(SeriesAssertion.objects.filter(observation_id__in=Subquery(current))
        .filter(Q(observation__source_id__in=ids) | Q(target_id__in=ids))
        .select_related('observation__source', 'target').order_by('pk')[:LIMIT + 1])
    latest = SeriesOverrideEvent.objects.filter(Q(source_id__in=ids) | Q(target_id__in=ids)).order_by()
    last_ids = latest.values('source_id', 'target_id').annotate(last=Max('pk')).values('last')
    events = list(SeriesOverrideEvent.objects.filter(pk__in=Subquery(last_ids))
        .select_related('source', 'target').order_by('pk')[:LIMIT + 1])
    if len(raw) > LIMIT or len(events) > LIMIT:
        raise RecommendationUnavailable('直接关联超过处理上限，未执行系列排除。')
    nodes = {}
    effective = {}
    for row in raw:
        source = row.observation.source
        nodes.update({source.pk: source, row.target_id: row.target})
        if not row.requires_review and row.kind in SPECS:
            effective[source.pk, row.target_id, row.kind] = {'source_id': source.pk, 'target_id': row.target_id, 'kind': row.kind, 'origin': 'archive'}
    for event in events:
        nodes.update({event.source_id: event.source, event.target_id: event.target})
    sources = ids | {row[0] for row in effective} | {event.source_id for event in events}
    heads = {head.source_id: head for head in SeriesHead.objects.filter(source_id__in=sources)
             .select_related('source', 'current', 'last_attempt')}
    for head in heads.values():
        nodes[head.source_id] = head.source
    if ids - set(nodes):
        nodes.update({node.pk: node for node in SeriesNode.objects.filter(pk__in=ids - set(nodes))})
    pair_keys = {}
    for key in effective:
        pair_keys.setdefault(key[:2], []).append(key)
    for event in events:
        if event.action == 'clear':
            continue
        for key in pair_keys.get((event.source_id, event.target_id), ()):
            effective.pop(key, None)
        if event.base_observation_id == _basis(heads.get(event.source_id)) and event.action == 'replace' and event.kind in SPECS:
            effective[event.source_id, event.target_id, event.kind] = {'source_id': event.source_id,
                'target_id': event.target_id, 'kind': event.kind, 'origin': 'manual'}
    catalog = {row.bangumi_id: row for row in catalog_for_scope(scope).filter(bangumi_id__in=(set(nodes) | ids))}
    valid = {identity for identity, node in nodes.items() if _visible(node, catalog)}
    facts = sorted((value for value in effective.values() if value['source_id'] != value['target_id'] and value['source_id'] in valid and value['target_id'] in valid),
                   key=lambda row: (row['source_id'], row['target_id'], row['kind']))
    rev = revision()
    fingerprint = digest([POLICY, rev, node_revision(set(nodes) | ids), facts,
                          [(key, head.current_id, head.last_attempt_id) for key, head in sorted(heads.items())]])
    return {'facts': facts, 'nodes': nodes, 'catalog': catalog, 'heads': heads, 'revision': rev, 'fingerprint': fingerprint}


def seed_graph(identity, scope):
    data = graph([identity], scope)
    if identity not in data['catalog']:
        raise NotFound('参照作品当前不可用。')
    node = data['nodes'].get(identity)
    if node is not None and not _visible(node, data['catalog']):
        raise NotFound('参照作品系列身份或分级尚未一致。')
    head = data['heads'].get(identity)
    observation = head.current if head else None
    coverage = observation.coverage if observation else head.last_attempt.coverage if head and head.last_attempt else 'not_loaded'
    if observation and observation.source_at < timezone.now() - timedelta(days=45):
        coverage = 'stale'
    data['coverage'] = coverage
    data['last_attempt_status'] = head.last_attempt.coverage if head and head.last_attempt else 'not_loaded'
    data['strong_ids'] = {row['target_id'] if row['source_id'] == identity else row['source_id']
                          for row in data['facts'] if row['kind'] in STRONG}
    return data


def fact_payload(row, catalog):
    return {**row, 'relation_label': SPECS[row['kind']].label,
            'source_title': catalog[row['source_id']].title_zh or catalog[row['source_id']].title_jp,
            'target_title': catalog[row['target_id']].title_zh or catalog[row['target_id']].title_jp}


def related_block(live, query):
    from .browse_controls import filter_index
    from .features import feature_tags
    data, seed = live['series'], query['seed_bangumi_id']
    excluded = set(live['feedback']['hidden_ids']) | (set(live['owned']) if query['hide_recorded'] else set())
    rows = [data['catalog'][i] for i in data['strong_ids'] - excluded]
    index = {'features': [(row.bangumi_id, feature_tags(row.tags, (row.title_zh, row.title_jp)), row.score, row.status_type) for row in rows]}
    eligible = {row[0] for row in filter_index(index, [{'bangumi_id': row.bangumi_id, 'season': row.season,
        'score': row.score, 'status_type': row.status_type} for row in rows],
        {**query, 'year_from': query.get('year_from'), 'year_to': query.get('year_to')})['features']}
    ordered = sorted(eligible, key=lambda i: (data['nodes'][i].release_date or '9999', i))
    items = []
    for identity in ordered[:RELATED_LIMIT]:
        row, node = data['catalog'][identity], data['nodes'][identity]
        items.append({'bangumi_id': identity, 'title_zh': row.title_zh, 'title_jp': row.title_jp,
            'release_date': node.release_date, 'release_format': node.release_format,
            'watch_status': live['owned'].get(identity), 'facts': [fact_payload(fact, data['catalog'])
                for fact in data['facts'] if fact['kind'] in STRONG and identity in (fact['source_id'], fact['target_id'])]})
    return {'policy_version': POLICY, 'series_revision': data['revision'], 'fingerprint': data['fingerprint'],
            'coverage': data['coverage'], 'last_attempt_status': data['last_attempt_status'],
            'seed_bangumi_id': seed, 'direct_only': True, 'filtered_count': len(ordered),
            'limit': RELATED_LIMIT, 'truncated': len(ordered) > RELATED_LIMIT, 'items': items}


def annotations(user, ids, scope):
    from .feedback import feedback_snapshot
    from .quality_readonly import readonly_snapshot
    with readonly_snapshot():
        data = graph(ids, scope)
        hidden = feedback_snapshot(user)['hidden_ids']
        results = []
        for identity in ids:
            if identity not in data['catalog'] or identity in hidden:
                continue
            facts = [row for row in data['facts'] if identity in (row['source_id'], row['target_id'])
                     and row['source_id'] not in hidden and row['target_id'] not in hidden]
            facts.sort(key=lambda row: (row['kind'] not in STRONG, row['source_id'], row['target_id'], row['kind']))
            results.append({'bangumi_id': identity, 'facts': [fact_payload(row, data['catalog']) for row in facts[:2]], 'truncated': len(facts) > 2})
        return {'contract_version': ANNOTATIONS_VERSION, 'policy_version': POLICY, 'series_revision': data['revision'],
                'fingerprint': data['fingerprint'], 'catalog_revision': catalog_revision(), 'results': results,
                '_series_subjects': list(ids)}


def verify(data, scope):
    from .exploration_contract import ExplorationSnapshotChanged
    ids = data.pop('_series_subjects', None)
    if ids is None:
        return
    current = graph(ids, scope)
    expected = data.get('series_context', data)
    if current['fingerprint'] != expected['fingerprint']:
        if data.get('contract_version') == ANNOTATIONS_VERSION:
            raise SeriesConflict('关系资料在读取期间发生变化，请重新读取。')
        raise ExplorationSnapshotChanged('关系资料在读取期间发生变化，请重新探索。')
