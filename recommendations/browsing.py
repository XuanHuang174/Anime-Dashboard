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

from django.conf import settings
from django.core import signing
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


def live_context(user, options):
    context = engine.user_context(user)
    context['algorithm_version'] = engine.ALGORITHM_VERSION
    # Read only the authenticated owner's recommendation-relevant fields plus
    # timestamps/progress. NEVER select review/private_description text.
    journal = list(UserAnimeRecord.objects.filter(user=user).order_by('pk').values(
        *RECORD_FIELDS, *LINK_FIELDS, 'updated_at', 'watched_eps', 'total_eps'))
    state = {'user': user.pk, 'profile': context['profile'], 'preferences': context['preferences'],
             'owned': sorted(context['owned'].items()),
             'feedback': {'version': context['feedback']['version'],
                          'hidden_ids': sorted(context['feedback']['hidden_ids'])},
             'journal': journal, 'catalog': catalog_revision(), 'options': options,
             'algorithm': engine.ALGORITHM_VERSION, 'policy': CONTROL_POLICY_VERSION,
             'feature': engine.FEATURE_VERSION, 'taxonomy': engine.TAXONOMY_VERSION,
             'feature_rule': engine.FEATURE_RULE_VERSION, 'evidence': EVIDENCE_CONTRACT_VERSION,
             'selection': SELECTION_RULE_VERSION,
             'limits': [engine.MAX_RESULTS, engine.CANDIDATE_POOL, BATCH_SIZE]}
    return context, _token(CURSOR_SALT + '.context', state)


def _decode(cursor, user):
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_CURSOR_LENGTH:
        raise ValidationError({'cursor': '浏览游标长度无效。'})
    try:
        body = signing.loads(cursor, salt=CURSOR_SALT, max_age=SNAPSHOT_TTL_SECONDS)
    except signing.SignatureExpired:
        raise engine.RecommendationSnapshotChanged('浏览快照已过期，请重新加载第一批。') from None
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError({'cursor': '浏览游标无效或已被修改。'}) from None
    try:
        if body['owner'] != _token(CURSOR_SALT + '.owner', user.pk):
            raise engine.RecommendationSnapshotChanged()
        if (body['contract'] != BROWSE_CONTRACT_VERSION or body['policy'] != CONTROL_POLICY_VERSION
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
                          salt=CURSOR_SALT, compress=True)
    if len(value) > MAX_CURSOR_LENGTH:
        raise engine.RecommendationUnavailable('浏览快照超过安全大小限制，请联系管理员。')
    return value


def browse_page(user, validated, supplied):
    if not enabled():
        raise engine.RecommendationUnavailable('浏览控制暂未开放；请使用原有推荐入口。')
    if connection.vendor != 'postgresql' or connection.in_atomic_block:
        raise engine.RecommendationUnavailable('浏览快照需要独立的 PostgreSQL 只读事务。')
    body = _decode(validated['cursor'], user) if 'cursor' in validated else None
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
        context, revision = live_context(user, options)
        if body and not hmac.compare_digest(revision, body['revision']):
            raise engine.RecommendationSnapshotChanged('画像、手账或目录已变化，请重新加载第一批。')
        if body is None:
            rows = list(engine.safe_catalog().order_by('bangumi_id').values(
                'bangumi_id', 'tags', 'title_zh', 'title_jp', 'score', 'status_type', 'season')[:engine.MAX_CATALOG_ROWS + 1])
            index = engine.build_feature_index(rows)
            ranked, weights = rank_controlled(context, options, filter_index(index, rows, options))
            ids = [item['anime'].bangumi_id for item in ranked]
            body = {'contract': BROWSE_CONTRACT_VERSION, 'policy': CONTROL_POLICY_VERSION,
                    'owner': _token(CURSOR_SALT + '.owner', user.pk), 'revision': revision,
                    'snapshot': uuid.uuid4().hex, 'expires': int(time.time()) + SNAPSHOT_TTL_SECONDS,
                    'options': options, 'ids': ids, 'offset': 0, 'batch': 1,
                    # Evidence needs exactly the original whole-catalog weights.
                    'weights': weights if isinstance(weights, dict) else {}}
        else:
            weights = body['weights']
        ids, offset = body['ids'], body['offset']
        current = {row.bangumi_id: row for row in engine.safe_catalog().filter(
            bangumi_id__in=ids).defer('synopsis', 'source_cover_url')}
        if len(current) != len(ids):
            raise engine.RecommendationSnapshotChanged('作品安全状态已变化，请重新加载。')
        presenter = EvidencePresenter(user, context, weights, engine.ALGORITHM_VERSION)
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
            results.append(data)
        return {'contract_version': engine.CONTRACT_VERSION, 'browse_contract_version': BROWSE_CONTRACT_VERSION,
                'algorithm_version': engine.ALGORITHM_VERSION, 'control_policy_version': CONTROL_POLICY_VERSION,
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
