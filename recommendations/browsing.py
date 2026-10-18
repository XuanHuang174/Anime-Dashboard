"""9A stateless, authenticated fixed browse snapshots (no private shared cache).

Signed cursors contain only public result IDs/options and keyed opaque revisions,
not user IDs, source works, personal scores or reviews. Continuations hydrate the
signed order, NEVER rerank. Timestamp is fixed at issuance, not extended on use.
"""
import hashlib
import hmac
import json
import math
import re
import time
import uuid
from time import perf_counter

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import connection
from django.db.models import TextField, Value
from django.db.models.functions import Cast, Coalesce, Concat, Length
from rest_framework.exceptions import ValidationError

from anime.models import UserAnimeRecord
from . import engine
from .browse_controls import (CONTROL_POLICY_VERSION, OPTION_KEYS, canonical_options,
                              filter_index, rank_controlled)
from .evidence import (EvidencePresenter, EVIDENCE_CONTRACT_VERSION, EVIDENCE_DISCLAIMER,
                       SELECTION_RULE_VERSION, _token)
from .models import RecommendationAnime
from .public_fingerprint import NativeSHA256
from .quality_readonly import readonly_snapshot
from .resolution import RECORD_FIELDS, LINK_FIELDS

BROWSE_CONTRACT_VERSION = 'recommendations.browse.v1'
CURSOR_SALT = 'recommendations.browse.cursor.v1'
BATCH_SIZE = 24
SNAPSHOT_TTL_SECONDS = 900
MAX_CURSOR_LENGTH = 8192
CATALOG_FIELDS = ('bangumi_id', 'title_zh', 'title_jp', 'tags', 'score', 'season',
                  'status_type', 'cover_url', 'nsfw', 'synopsis', 'updated_at')
PUBLIC_CACHE_NAMESPACE = 'recommendations.browse.public.v1'
PREPARED_CONTRACT = 'recommendations.browse.v2'
PREPARED_SALT = 'recommendations.browse.cursor.v2'


def _scope(recipe=None):
    return (PREPARED_CONTRACT, PREPARED_SALT, recipe['control_policy_version'], recipe['algorithm_version']) if recipe else (
        BROWSE_CONTRACT_VERSION, CURSOR_SALT, CONTROL_POLICY_VERSION, engine.ALGORITHM_VERSION)


class BrowseDisabled(engine.RecommendationUnavailable):
    def __init__(self, *, continuation=False):
        super().__init__('增强 9 已关闭，正在恢复原有 V1 推荐。')
        self.status_code = 409 if continuation else 503
        self.payload = {'code': 'browse_disabled', 'fallback': 'legacy_v1',
                        'algorithm_version': engine.ALGORITHM_VERSION,
                        'detail': str(self.detail)}


def rollout_revision():
    return _token(CURSOR_SALT + '.rollout', str(getattr(settings, 'RECOMMENDATION_BROWSE_EPOCH', '1')))


def capabilities():
    if getattr(settings, 'RECOMMENDATION_ALGORITHM_VERSION', 'content.v1') != 'content.v1':
        raise engine.RecommendationUnavailable('浏览控制仅支持 V1，不会自动切换 V2。')
    return {'contract_version': 'recommendations.capabilities.v1', 'algorithm_version': 'content.v1',
            'browse_enabled': enabled(), 'rollout_revision': rollout_revision(),
            'browse_contract_version': BROWSE_CONTRACT_VERSION, 'legacy_contract_version': engine.CONTRACT_VERSION}


def public_browse_index(revision, metrics, *, recipe=None):
    """Only exact-revision PUBLIC fields/features; never user/profile/results.

    Current transaction always hashes live catalog FIRST. Missing cache or cache
    outage builds fresh; a cache hit cannot bypass current safety/revision reads.
    """
    key = ':'.join((PUBLIC_CACHE_NAMESPACE, revision, rollout_revision(), engine.TAXONOMY_VERSION,
                    engine.FEATURE_VERSION, engine.FEATURE_RULE_VERSION))
    if recipe:
        key = 'recommendations.browse.public.v2:' + _digest([recipe['recipe_revision'], key])
    use_cache = getattr(settings, 'RECOMMENDATION_BROWSE_PUBLIC_CACHE_ENABLED', True)
    metrics['public_cache'] = 'disabled' if not use_cache else 'miss'
    if use_cache:
        try:
            value = cache.get(key)
            if value and value.get('revision') == revision:
                metrics['public_cache'] = 'hit'
                return value['rows'], value['index']
        except Exception:
            metrics['public_cache'] = 'unavailable'
    rows = list(engine.safe_catalog().order_by('bangumi_id').values(
        'bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type', 'season')[:engine.MAX_CATALOG_ROWS + 1])
    index = engine.build_feature_index(rows)
    if use_cache:
        try:
            cache.set(key, {'revision': revision, 'rows': rows, 'index': index}, timeout=60)
        except Exception:
            metrics['public_cache'] = 'unavailable'
    return rows, index


def browse_requested(params, supplied):
    return params.get('browse') is True or any(key in supplied for key in ('style', 'year_from', 'year_to', 'cursor'))


def enabled():
    return getattr(settings, 'RECOMMENDATION_BROWSE_ENABLED', True)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def catalog_revision():
    """Exact live fingerprint, including direct UPDATE without auto_now.

    Transfer compact per-row hashes instead of all public descriptions/tags.
    Hashing unsafe rows too detects reclassification/new catalog rows. No MAX
    timestamp heuristic, external service, extension or persistent DB writes.
    """
    parts = []
    for field in CATALOG_FIELDS:
        value = Coalesce(Cast(field, TextField()), Value('<null>'), output_field=TextField())
        parts.extend((Cast(Length(value), TextField()), Value(':'), value))
    rows = list(RecommendationAnime.objects.order_by('pk').annotate(
        revision=NativeSHA256(Concat(*parts, output_field=TextField()))).values_list(
            'pk', 'revision')[:engine.MAX_CATALOG_ROWS + 1])
    if len(rows) > engine.MAX_CATALOG_ROWS:
        raise engine.RecommendationUnavailable()
    return _digest(rows)


def live_context(user, options, metrics, *, recipe=None):
    started = perf_counter()
    context = engine.user_context(user)
    contract, salt, policy, algorithm = _scope(recipe)
    context['algorithm_version'] = algorithm
    # Read only the authenticated owner's recommendation-relevant fields plus
    # timestamps/progress. NEVER select review/private_description text.
    journal = list(UserAnimeRecord.objects.filter(user=user).order_by('pk').values(
        *RECORD_FIELDS, *LINK_FIELDS, 'updated_at', 'watched_eps', 'total_eps'))
    metrics['profile_journal_ms'] = round((perf_counter() - started) * 1000, 3)
    started = perf_counter()
    catalog = catalog_revision()
    metrics['catalog_revision_ms'] = round((perf_counter() - started) * 1000, 3)
    state = {'user': user.pk, 'profile': context['profile'], 'preferences': context['preferences'],
             'owned': sorted(context['owned'].items()),
             'feedback': {'version': context['feedback']['version'],
                          'hidden_ids': sorted(context['feedback']['hidden_ids'])},
             'journal': journal, 'catalog': catalog, 'options': options, 'rollout_revision': rollout_revision(),
             'algorithm': algorithm, 'policy': policy,
             'feature': engine.FEATURE_VERSION, 'taxonomy': engine.TAXONOMY_VERSION,
             'feature_rule': engine.FEATURE_RULE_VERSION, 'evidence': EVIDENCE_CONTRACT_VERSION,
             'selection': SELECTION_RULE_VERSION,
             'limits': [engine.MAX_RESULTS, engine.CANDIDATE_POOL, BATCH_SIZE]}
    if recipe:
        state['execution_recipe'] = recipe
    return context, _token(salt + '.context', state), catalog


def _decode(cursor, user, *, recipe=None):
    contract, salt, policy, algorithm = _scope(recipe)
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_CURSOR_LENGTH:
        raise ValidationError({'cursor': '浏览游标长度无效。'})
    try:
        body = signing.loads(cursor, salt=salt, max_age=SNAPSHOT_TTL_SECONDS)
    except signing.SignatureExpired:
        raise engine.RecommendationSnapshotChanged('浏览快照已过期，请重新加载第一批。') from None
    except (signing.BadSignature, ValueError, TypeError):
        # Rolling rollback/upgrade: a valid OTHER namespace is stale, never
        # consumed as the current algorithm's continuation.
        try:
            signing.loads(cursor, salt=CURSOR_SALT if recipe else PREPARED_SALT)
        except (signing.BadSignature, ValueError, TypeError):
            pass
        else:
            raise engine.RecommendationSnapshotChanged('浏览版本已变化，请重新加载第一批。') from None
        raise ValidationError({'cursor': '浏览游标无效或已被修改。'}) from None
    try:
        if body['owner'] != _token(salt + '.owner', user.pk):
            raise engine.RecommendationSnapshotChanged()
        if (body['contract'] != contract or body['policy'] != policy
                or recipe is not None and body.get('execution_recipe') != recipe
                or recipe is None and 'execution_recipe' in body
                or body.get('rollout_revision') != rollout_revision()
                or body['expires'] <= int(time.time())):
            raise engine.RecommendationSnapshotChanged('浏览快照已失效，请重新加载第一批。')
        ids = body['ids']
        if (not isinstance(ids, list) or len(ids) > engine.MAX_RESULTS or len(set(ids)) != len(ids)
                or any(type(value) is not int or not 0 < value <= 2147483647 for value in ids)
                or type(body['offset']) is not int or not 0 <= body['offset'] < len(ids)
                or body['batch'] != body['offset'] // BATCH_SIZE + 1):
            raise ValueError()
        if (not re.fullmatch('[a-f0-9]{32}', body['snapshot'])
                or not re.fullmatch('[a-f0-9]{64}', body['revision'])
                or not isinstance(body['options'], dict)
                or set(body['options']) != set(OPTION_KEYS)
                or not isinstance(body['weights'], dict) or len(body['weights']) > 256
                or any(not isinstance(tag, str) or type(weight) not in (int, float)
                       or not math.isfinite(weight) or not 1 <= weight <= 3
                       for tag, weight in body['weights'].items())):
            raise ValueError()
        from .serializers import RecommendationQuerySerializer
        check = RecommendationQuerySerializer(data={
            **{key: value for key, value in body['options'].items() if value is not None}, 'browse': True})
        if not check.is_valid() or _digest(canonical_options(check.validated_data)) != _digest(body['options']):
            raise ValueError()
        return body
    except (KeyError, TypeError, ValueError):
        raise engine.RecommendationSnapshotChanged('浏览快照不兼容或不可用，请重新加载第一批。') from None


def _cursor(body, offset):
    value = signing.dumps({**body, 'offset': offset, 'batch': offset // BATCH_SIZE + 1},
                          salt=PREPARED_SALT if 'execution_recipe' in body else CURSOR_SALT, compress=True)
    if len(value) > MAX_CURSOR_LENGTH:
        raise engine.RecommendationUnavailable('浏览快照超过安全大小限制，请联系管理员。')
    return value


def browse_page(user, validated, supplied, *, metrics=None, _recipe=None):
    # Internal D2 adapter only. Production views NEVER pass a recipe. D3 must
    # separately approve serving; a query parameter cannot select this path.
    if _recipe is not None:
        from .execution import recipe_descriptor
        if _recipe != recipe_descriptor(serving_enabled=_recipe.get('serving_enabled', False)):
            raise engine.RecommendationSnapshotChanged('执行配方已变化，请重新加载。')
        if _recipe['serving_enabled']:
            from .deployment import require_recipe
            if _recipe != require_recipe():
                raise engine.RecommendationSnapshotChanged()
    contract, salt, policy, algorithm = _scope(_recipe)
    metrics = metrics if metrics is not None else {}
    metrics.update(public_cache='not_used', algorithm_version=algorithm)
    if not enabled():
        if getattr(settings, 'RECOMMENDATION_ALGORITHM_VERSION', 'content.v1') != 'content.v1':
            raise engine.RecommendationUnavailable('当前配置不是 V1，不能自动回退。')
        raise BrowseDisabled(continuation='cursor' in validated)
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise engine.RecommendationUnavailable('浏览快照需要独立的 PostgreSQL 只读事务。')
    body = _decode(validated['cursor'], user, recipe=_recipe) if 'cursor' in validated else None
    if getattr(settings, 'RECOMMENDATION_ALGORITHM_VERSION', engine.ALGORITHM_VERSION) != engine.ALGORITHM_VERSION:
        if body:
            raise engine.RecommendationSnapshotChanged('推荐算法已变化，请重新加载。')
        raise engine.RecommendationUnavailable('浏览控制仅支持当前 V1，不会启动 V2。')
    options = canonical_options(validated)
    if body:
        for key in OPTION_KEYS:
            if key in supplied and options[key] != body['options'][key]:
                raise engine.RecommendationSnapshotChanged('浏览条件已变化，请重新加载第一批。')
        options = body['options']
    with readonly_snapshot():
        context, revision, catalog = live_context(user, options, metrics, recipe=_recipe)
        if body and not hmac.compare_digest(revision, body['revision']):
            raise engine.RecommendationSnapshotChanged('画像、手账或目录已变化，请重新加载第一批。')
        if body is None:
            started = perf_counter()
            if context['mode'] == 'cold_start':
                ranked, weights = [], {}
            else:
                rows, index = public_browse_index(catalog, metrics, recipe=_recipe)
                metrics['public_index_ms'] = round((perf_counter() - started) * 1000, 3)
                started = perf_counter()
                if _recipe:
                    from .execution import execute_v2
                    ranked, weights, execution = execute_v2(user, options, index, context=context,
                        serving_enabled=_recipe['serving_enabled'])
                    context['_ranking_trace'] = execution['trace']
                else:
                    ranked, weights = rank_controlled(context, options, filter_index(index, rows, options))
            metrics['rank_ms'] = round((perf_counter() - started) * 1000, 3)
            metrics.update(context.get('_ranking_trace', {}))
            ids = [item['anime'].bangumi_id for item in ranked]
            body = {'contract': contract, 'policy': policy,
                    'owner': _token(salt + '.owner', user.pk), 'revision': revision,
                    'snapshot': uuid.uuid4().hex, 'expires': int(time.time()) + SNAPSHOT_TTL_SECONDS,
                    'rollout_revision': rollout_revision(),
                    'options': options, 'ids': ids, 'offset': 0, 'batch': 1,
                    # Evidence needs exactly the original whole-catalog weights.
                    'weights': weights if isinstance(weights, dict) else {}}
            if _recipe:
                body['execution_recipe'] = _recipe
        else:
            weights = body['weights']
        ids, offset = body['ids'], body['offset']
        current = {row.bangumi_id: row for row in engine.safe_catalog().filter(
            bangumi_id__in=ids).defer('synopsis', 'source_cover_url')}
        if len(current) != len(ids):
            raise engine.RecommendationSnapshotChanged('作品安全状态已变化，请重新加载。')
        presenter = EvidencePresenter(user, context, weights, algorithm,
            execution_scope={'recipe': _recipe, 'browse_revision': revision} if _recipe else None)
        presenter.ensure_safe(list(current.values()))
        batch_start = offset // BATCH_SIZE * BATCH_SIZE
        batch_end = min(batch_start + BATCH_SIZE, len(ids))
        end = min(offset + options['page_size'], batch_end)
        results = []
        from .serializers import RecommendationListAnimeSerializer, EvidenceEnvelopeSerializer
        for identity in ids[offset:end]:
            anime = current[identity]
            data = dict(RecommendationListAnimeSerializer(anime).data)
            data.update(EvidenceEnvelopeSerializer(presenter.present(anime, engine.feature_tags(
                anime.tags, (anime.title_zh, anime.title_jp)))).data)
            data.update(content_rating='non_adult', watch_status=context['owned'].get(identity))
            if _recipe:
                from .v2_browsing import issue_detail_context
                data['detail_context'] = issue_detail_context(body, identity)
            results.append(data)
        return {**({'execution_recipe': _recipe} if _recipe else {}),
                'contract_version': engine.CONTRACT_VERSION, 'browse_contract_version': contract,
                'algorithm_version': algorithm, 'control_policy_version': policy,
                'style': options['style'], 'effective_conditions': options, 'snapshot_id': body['snapshot'],
                'snapshot_expires_at': body['expires'], 'batch': offset // BATCH_SIZE + 1,
                'batch_size': BATCH_SIZE, 'batch_count': batch_end - batch_start,
                'batch_hero_bangumi_id': ids[batch_start] if ids else None,
                'page': (offset - batch_start) // options['page_size'] + 1, 'page_size': options['page_size'],
                'count': len(ids), 'result_limit': engine.MAX_RESULTS,
                'next_page_cursor': _cursor(body, end) if end < batch_end else None,
                'next_batch_cursor': _cursor(body, batch_end) if batch_end < len(ids) else None,
                'batch_exhausted': end >= batch_end, 'exhausted': end >= len(ids),
                'mode': context['mode'], 'readiness': context['profile']['readiness'],
                'cold_start_state': context['profile']['cold_start_state'],
                'feature_version': engine.FEATURE_VERSION, 'taxonomy_version': engine.TAXONOMY_VERSION,
                'evidence_contract_version': EVIDENCE_CONTRACT_VERSION,
                'evidence_context': presenter.versions, 'evidence_disclaimer': EVIDENCE_DISCLAIMER,
                'results': results}
