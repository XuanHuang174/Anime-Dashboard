"""11B/11D: single-work and tag exploration, independent read-only snapshots.

No personal ranking, score/review reads, external calls or shared private cache.
Only initial requests rank. Continuations/details hydrate signed public IDs.
"""
import hmac
import math
import re
import time
import uuid
from datetime import datetime
from time import perf_counter

from django.conf import settings
from django.core import signing
from django.db import connection
from rest_framework.exceptions import NotFound, ValidationError

from . import engine
from .browse_controls import filter_index
from .browsing import catalog_revision, public_browse_index
from .content_scope import catalog_for_scope, ensure_scope
from .evidence import _token
from .exploration_catalog import catalog_item
from .exploration_tags import matches_tags, rank_tags
from .exploration_contract import (QUERY_VERSION, RESULT_VERSION, EVIDENCE_VERSION,
    POLICIES, ExplorationEvidenceSerializer, ExplorationQuerySerializer, query_fingerprint, require_enabled, canary_users, ExplorationSnapshotChanged)
from .features import FEATURE_RULE_VERSION, feature_tags
from .feedback import feedback_snapshot
from .quality_readonly import readonly_snapshot
from .resolution import resolved_records
from .serializers import RecommendationListAnimeSerializer, RecommendationPublicAnimeSerializer
from .taxonomy import AVAILABLE_TAGS, TAXONOMY_VERSION

CURSOR_SALT = 'recommendations.explore.cursor.v1'
DETAIL_SALT = 'recommendations.explore.detail.v1'
CONTEXT_SALT = 'recommendations.explore.context.v1'
DETAIL_VERSION = 'recommendations.explore.detail.v1'
CONTEXT_VERSION = 'recommendations.explore.context.v1'
TTL = 900
MAX_TOKEN = 8192
BATCH_SIZE = 24


def epoch():
    return _token(CURSOR_SALT + '.epoch', {'epoch': str(getattr(settings, 'RECOMMENDATION_EXPLORATION_EPOCH', '1')),
                                         'canary_users': canary_users()})


def canonical_query(validated):
    return dict(ExplorationQuerySerializer(instance=validated).data)


def _require_snapshot(user=None):
    require_enabled(user)
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise engine.RecommendationUnavailable('探索需要独立的 PostgreSQL 只读快照。')


def _live(user, query, scope, *, series=False):
    scope = ensure_scope(user, scope)
    catalog = catalog_for_scope(scope)
    seed = (catalog.filter(bangumi_id=query['seed_bangumi_id']).defer('synopsis', 'source_cover_url').first()
            if query['intent'] == 'similar' else None)
    if query['intent'] == 'similar' and seed is None:
        raise NotFound('参照作品当前不可用于探索。')
    adult_ids = set(catalog.filter(nsfw=True).values_list('bangumi_id', flat=True)) if scope['content_scope'] == 'include_adult' else ()
    records = resolved_records(user, allowed_adult_ids=adult_ids, include_profile=False,
                               max_rows=engine.MAX_CATALOG_ROWS)
    if not records.schema_available:
        raise engine.RecommendationUnavailable('手账身份服务尚未就绪。')
    owned = {}
    priority = {'completed': 4, 'watching': 3, 'plan': 2, 'on_hold': 1}
    for row in records:
        identity, status = row['resolved_bangumi_id'], row['watch_status']
        if identity and priority.get(status, 0) > priority.get(owned.get(identity), 0):
            owned[identity] = status
    feedback = feedback_snapshot(user)
    revision = catalog_revision()
    relations = None
    if series:
        from .series_rollout import require_access
        require_access(user)
        from .series_exploration import seed_graph
        relations = seed_graph(query['seed_bangumi_id'], scope)
    context_revision = _token(CURSOR_SALT + '.revision', {
        'owner': user.pk, 'owned': sorted(owned.items()),
        'feedback': [feedback['version'], sorted(feedback['hidden_ids'])],
        'scope': scope, 'catalog': revision, 'query': query,
        'epoch': epoch(), 'policy': POLICIES[query['intent']], 'feature': engine.FEATURE_VERSION,
        'feature_rule': FEATURE_RULE_VERSION, 'taxonomy': TAXONOMY_VERSION,
        'query_contract': QUERY_VERSION, 'evidence_contract': EVIDENCE_VERSION,
        'limits': [engine.MAX_RESULTS, BATCH_SIZE, TTL],
        **({'series': relations['fingerprint']} if series else {})})
    return {'seed': seed, 'owned': owned, 'feedback': feedback, 'scope': scope,
            'catalog': revision, 'revision': context_revision, **({'series': relations} if series else {})}


def rank_similar(index, rows, query, seed_tags, excluded):
    """Whole-catalog hard filters BEFORE ranking/capping; no zero-match padding."""
    weights = index['weights']
    seed_norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in sorted(seed_tags)))
    seed_weight = sum(weights.get(tag, 1) for tag in sorted(seed_tags))
    ranked = []
    if not seed_norm:
        return [], 0
    filters = {**query, 'year_from': query.get('year_from'), 'year_to': query.get('year_to')}
    for identity, tags, score, _ in filter_index(index, rows, filters)['features']:
        common = tags & seed_tags
        if identity in excluded or not common:
            continue
        norm = math.sqrt(sum(weights.get(tag, 1) ** 2 for tag in sorted(tags)))
        cosine = sum(weights.get(tag, 1) ** 2 for tag in sorted(common)) / (seed_norm * norm)
        coverage = sum(weights.get(tag, 1) for tag in sorted(common)) / seed_weight
        ranked.append((identity, cosine, coverage, score))
    ranked.sort(key=lambda row: (-row[1], -row[2], -row[3], row[0]))
    return [row[0] for row in ranked[:engine.MAX_RESULTS]], len(ranked)


def _dump(body, salt):
    value = signing.dumps(body, salt=salt, compress=True)
    if len(value) > MAX_TOKEN:
        raise engine.RecommendationUnavailable('探索上下文超出安全大小限制。')
    return value


def _load(token, salt, user, purpose, *, series=False):
    from .series_exploration import RESULT_VERSION as SERIES_RESULT, POLICY as SERIES_POLICY
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN:
        raise ValidationError('探索上下文长度无效。')
    try:
        body = signing.loads(token, salt=salt, max_age=TTL)
    except signing.SignatureExpired:
        raise ExplorationSnapshotChanged('探索快照已过期，请重新探索。') from None
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError('探索上下文无效或已被修改。') from None
    try:
        if (body['owner'] != _token(CURSOR_SALT + '.owner', user.pk)
                or body['epoch'] != epoch() or body['contract'] != (SERIES_RESULT if series else RESULT_VERSION)
                or body.get('series_policy') != (SERIES_POLICY if series else None)
                or series and not re.fullmatch('[a-f0-9]{64}', body.get('series_fingerprint', ''))
                or body['purpose'] != purpose or body['policy'] not in POLICIES.values()
                or body['feature_rule'] != FEATURE_RULE_VERSION or body['taxonomy'] != TAXONOMY_VERSION
                or type(body['issued']) is not int or type(body['expires']) is not int
                or not body['issued'] < body['expires'] <= body['issued'] + TTL
                or body['expires'] <= int(time.time())):
            raise ExplorationSnapshotChanged('探索上下文已失效，请重新探索。')
        if (not re.fullmatch('[a-f0-9]{32}', body['snapshot'])
                or not re.fullmatch('[a-f0-9]{64}', body['revision'])):
            raise ValueError()
        # HTTP topic fields are comma strings; signed canonical queries contain
        # ordered lists. Reconstruct wire input WITHOUT broadening the HTTP schema.
        wire = dict(body['query'])
        for key in ('include_tags', 'exclude_tags'):
            if key not in wire:
                continue
            values = wire[key]
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError()
            if key == 'exclude_tags' and not values:
                del wire[key]
            else:
                wire[key] = ','.join(values)
        check = ExplorationQuerySerializer(data=wire)
        if (not check.is_valid() or canonical_query(check.validated_data) != body['query']
                or POLICIES[check.validated_data['intent']] != body['policy']):
            raise ValueError()
        if purpose == 'browse':
            ids, offset, count = body['ids'], body['offset'], body['eligible_count']
            if (not isinstance(ids, list) or not ids or len(ids) > engine.MAX_RESULTS or len(set(ids)) != len(ids)
                    or any(type(i) is not int or not 0 < i <= 2147483647 for i in ids)
                    or type(offset) is not int or not 0 <= offset < len(ids) or offset % 12 != 0
                    or body['query'].get('seed_bangumi_id') in ids
                    or type(count) is not int or not len(ids) <= count <= engine.MAX_CATALOG_ROWS
                    or len(ids) != min(count, engine.MAX_RESULTS)):
                raise ValueError()
        if purpose == 'detail' and (type(body['candidate']) is not int or not 0 < body['candidate'] <= 2147483647):
            raise ValueError()
        return body
    except (KeyError, TypeError, ValueError):
        raise ExplorationSnapshotChanged('探索上下文不兼容，请重新探索。') from None


def _base_body(user, query, live):
    from .series_exploration import RESULT_VERSION as SERIES_RESULT, POLICY as SERIES_POLICY
    now = int(time.time())
    deadline = live['scope'].get('scope_valid_until')
    expires = min(now + TTL, int(datetime.fromisoformat(deadline).timestamp())) if deadline else now + TTL
    if expires <= now:
        raise ExplorationSnapshotChanged('内容范围已到期。')
    return {'contract': SERIES_RESULT if 'series' in live else RESULT_VERSION, 'policy': POLICIES[query['intent']], 'query': query,
            **({'series_policy': SERIES_POLICY, 'series_fingerprint': live['series']['fingerprint']} if 'series' in live else {}),
            'owner': _token(CURSOR_SALT + '.owner', user.pk), 'epoch': epoch(),
            'snapshot': uuid.uuid4().hex, 'issued': now, 'expires': expires,
            'revision': live['revision'], 'feature_rule': FEATURE_RULE_VERSION, 'taxonomy': TAXONOMY_VERSION}


def _context(body, purpose, **extra):
    base = {key: value for key, value in body.items() if key not in ('ids', 'offset', 'eligible_count', 'purpose', 'candidate')}
    return _dump({**base, 'purpose': purpose, **extra}, DETAIL_SALT if purpose == 'detail' else CONTEXT_SALT)


def _versions(query, live):
    return {'query_fingerprint': query_fingerprint(query), 'catalog_feature_version': live['catalog'],
            'scope_revision': live['scope']['scope_revision'], 'taxonomy_version': TAXONOMY_VERSION,
            'ranking_policy_version': POLICIES[query['intent']]}


def _source(live):
    if live['seed'] is None:
        return None
    if '_selected_source' in live:
        return live['_selected_source']
    seed = live['seed']
    genres = feature_tags(seed.tags, (seed.title_zh, seed.title_jp))
    live['_selected_source'] = {'bangumi_id': seed.bangumi_id,
            'title': seed.title_zh.strip() or seed.title_jp.strip() or f'未命名番剧 {seed.bangumi_id}',
            'tags': [tag for tag in AVAILABLE_TAGS if tag in genres]}
    return live['_selected_source']


def _item(anime, body, live, *, detail=False):
    source = _source(live)
    tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
    selected = source['tags'] if source is not None else body['query']['include_tags']
    shared = [tag for tag in selected if tag in tags]
    versions = _versions(body['query'], live)
    evidence = {'contract_version': EVIDENCE_VERSION, 'kind': 'selected_work' if source is not None else 'selected_tags',
                'interpretation': 'shared_genre_association', 'is_probability': False,
                'shared_tags': shared, 'source': source, 'versions': versions}
    check = ExplorationEvidenceSerializer(data=evidence, context={'query': body['query'],
        'candidate_id': anime.bangumi_id, 'candidate_tags': tags,
        'selected_source': source, 'evidence_context': versions})
    check.is_valid(raise_exception=True)
    serializer = RecommendationPublicAnimeSerializer if detail else RecommendationListAnimeSerializer
    data = dict(serializer(anime, context={'content_scope': live['scope'],
        'validated_adult_ids': {anime.bangumi_id} if anime.nsfw else set()}).data)
    listed = '、'.join(f'「{tag}」' for tag in shared)
    reason = (f'与参照作品《{source["title"]}》共同包含{listed}题材。' if source is not None
              else f'命中 {len(shared)}/{len(selected)} 个所选题材：{listed}。')
    proof = _token(DETAIL_SALT + '.evidence', [body['owner'], body['snapshot'], body['revision'], anime.bangumi_id, evidence, reason])
    return {**data, 'content_rating': 'adult' if anime.nsfw else 'non_adult',
            'watch_status': live['owned'].get(anime.bangumi_id), 'reason': reason,
            'evidence': evidence, 'evidence_context': versions, 'evidence_token': proof,
            'detail_context': _context(body, 'detail', candidate=anime.bangumi_id)}


def browse_page(user, validated, *, scope_context, metrics=None, series=False):
    _require_snapshot(user)
    metrics = metrics if metrics is not None else {}
    body = _load(validated['cursor'], CURSOR_SALT, user, 'browse', series=series) if 'cursor' in validated else None
    query = body['query'] if body else canonical_query(validated)
    if series and query['intent'] != 'similar':
        raise ValidationError('系列分区仅支持单番相似。标签探索保留原匹配结果。')
    with readonly_snapshot():
        live = _live(user, query, scope_context, series=series)
        if body and not hmac.compare_digest(body['revision'], live['revision']):
            raise ExplorationSnapshotChanged('目录、范围、手账状态或隐藏反馈已变化，请重新探索。')
        source = _source(live)
        if body is None:
            started = perf_counter()
            rows, index = public_browse_index(live['catalog'], metrics, scope_context=live['scope'])
            excluded = set(live['feedback']['hidden_ids'])
            if source is not None:
                excluded.add(query['seed_bangumi_id'])
            if query['hide_recorded']:
                excluded.update(live['owned'])
            if series:
                excluded.update(live['series']['strong_ids'])
            ids, eligible = (rank_similar(index, rows, query, frozenset(source['tags']), excluded)
                             if source is not None else rank_tags(index, rows, query, excluded))
            metrics['selection_ms'] = round((perf_counter() - started) * 1000, 3)
            body = {**_base_body(user, query, live), 'purpose': 'browse', 'ids': ids,
                    'offset': 0, 'eligible_count': eligible}
        ids, offset = body['ids'], body['offset']
        start = offset // BATCH_SIZE * BATCH_SIZE
        batch_end = min(start + BATCH_SIZE, len(ids))
        end = min(offset + query['page_size'], batch_end)
        current = {row.bangumi_id: row for row in catalog_for_scope(live['scope']).filter(
            bangumi_id__in=ids[offset:end]).defer('synopsis', 'source_cover_url')}
        if len(current) != len(ids[offset:end]):
            raise ExplorationSnapshotChanged('探索作品已不可用。')
        cursor = lambda at: _dump({**body, 'offset': at}, CURSOR_SALT)
        from .series_exploration import related_block
        return {'contract_version': body['contract'], 'query_contract_version': QUERY_VERSION,
                **({'series_context': related_block(live, query), '_series_subjects': [query['seed_bangumi_id']]} if series else {}),
                'intent': query['intent'], 'algorithm_version': POLICIES[query['intent']], 'query': query,
                'evidence_contract_version': EVIDENCE_VERSION, 'evidence_context': _versions(query, live),
                'selected_seed': catalog_item(live['seed'], live['scope']) if source is not None else None,
                'snapshot_id': body['snapshot'], 'snapshot_expires_at': body['expires'],
                'snapshot_context': _context(body, 'context'), 'eligible_count': body['eligible_count'],
                'returned_count': len(ids), 'result_limit': engine.MAX_RESULTS,
                'batch': offset // BATCH_SIZE + 1, 'batch_size': BATCH_SIZE, 'batch_count': batch_end - start,
                'batch_hero_bangumi_id': ids[start] if ids else None,
                'page': (offset - start) // query['page_size'] + 1, 'page_size': query['page_size'],
                'next_page_cursor': cursor(end) if end < batch_end else None,
                'next_batch_cursor': cursor(batch_end) if batch_end < len(ids) else None,
                'batch_exhausted': end >= batch_end, 'exhausted': end >= len(ids),
                'empty_reason': 'seed_without_topics' if source is not None and not source['tags'] else 'no_matches' if not ids else None,
                'results': [_item(current[i], body, live) for i in ids[offset:end]]}


def detail(user, identity, validated, *, scope_context, series=False):
    _require_snapshot(user)
    body = _load(validated['detail_context'], DETAIL_SALT, user, 'detail', series=series)
    if body['candidate'] != identity:
        raise ExplorationSnapshotChanged('详情作品与探索上下文不一致。')
    expected = validated.get('evidence_token')
    with readonly_snapshot():
        live = _live(user, body['query'], scope_context, series=series)
        if expected and not hmac.compare_digest(body['revision'], live['revision']):
            raise ExplorationSnapshotChanged('探索依据已变化，请重新读取详情。')
        if series and body['series_fingerprint'] != live['series']['fingerprint']:
            raise ExplorationSnapshotChanged('系列资料已变化，请重新探索。')
        if series and identity in live['series']['strong_ids']:
            raise ExplorationSnapshotChanged('作品属于已确认直接关联，请从关联区查看。')
        anime = catalog_for_scope(live['scope']).filter(bangumi_id=identity).first()
        if anime is None:
            raise NotFound('作品当前不可用于探索。')
        tags = feature_tags(anime.tags, (anime.title_zh, anime.title_jp))
        rows = [{'bangumi_id': identity, 'score': anime.score, 'status_type': anime.status_type, 'season': anime.season}]
        filters = {**body['query'], 'year_from': body['query'].get('year_from'), 'year_to': body['query'].get('year_to')}
        allowed = filter_index({'features': [(identity, tags, anime.score, anime.status_type)]}, rows, filters)['features']
        source = _source(live)
        matches = (identity != live['seed'].bangumi_id and bool(tags.intersection(source['tags']))
                   if source is not None else matches_tags(tags, body['query']))
        if not allowed or identity in live['feedback']['hidden_ids'] or not matches:
            raise ExplorationSnapshotChanged('作品不再满足本次探索条件。')
        if not expected:
            body = {**body, 'revision': live['revision'], 'snapshot': uuid.uuid4().hex}
            deadline = live['scope'].get('scope_valid_until')
            if deadline:
                body['expires'] = min(body['expires'], int(datetime.fromisoformat(deadline).timestamp()))
        item = _item(anime, body, live, detail=True)
        if expected and not hmac.compare_digest(expected, item['evidence_token']):
            raise ExplorationSnapshotChanged('探索证据已变化。')
        from .series_exploration import related_block
        return {**item, 'contract_version': DETAIL_VERSION, 'intent': body['query']['intent'], 'query': body['query'],
                **({'series_context': related_block(live, body['query']), '_series_subjects': [body['query']['seed_bangumi_id']]} if series else {}),
                'selected_seed': catalog_item(live['seed'], live['scope']) if source is not None else None,
                'snapshot_expires_at': body['expires']}


def context_probe(user, token, *, scope_context, series=False):
    _require_snapshot(user)
    body = _load(token, CONTEXT_SALT, user, 'context', series=series)
    with readonly_snapshot():
        live = _live(user, body['query'], scope_context, series=series)
        if not hmac.compare_digest(live['revision'], body['revision']):
            raise ExplorationSnapshotChanged('探索上下文已变化，请重新探索。')
        from .series_exploration import related_block
        return {'contract_version': CONTEXT_VERSION, 'valid': True,
                **({'series_context': related_block(live, body['query']), '_series_subjects': [body['query']['seed_bangumi_id']]} if series else {}),
                'intent': body['query']['intent'],
                'snapshot_id': body['snapshot'], 'snapshot_expires_at': body['expires'],
                'query_fingerprint': query_fingerprint(body['query']),
                'evidence_context': _versions(body['query'], live),
                'selected_seed': {'bangumi_id': live['seed'].bangumi_id} if live['seed'] is not None else None}
