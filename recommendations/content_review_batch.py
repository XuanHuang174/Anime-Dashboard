"""Bounded public-catalog preview and explicit, atomic administrative approval."""
from datetime import timedelta
import codecs
from uuid import UUID, uuid4, uuid5

from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import content_review as review
from .adult_policy import general_adult_boundary
from .features import feature_tags
from .models import (RecommendationAnime, RecommendationContentReview as Review,
    RecommendationContentReviewEvent as Event, RecommendationContentReviewState as State)
from .normalization import clean_id

MAX_ITEMS = 500
MAX_FILE_BYTES = 64 * 1024
MAX_TICKET_LENGTH = 160000
TICKET_AGE = 900
SALT = 'recommendations.content-review.batch.v1'
STATUS_LABELS = {'unreviewed': '未审核', 'pending': '待审核', 'approved': '审核有效',
    'rejected': '已拒绝', 'revoked': '已撤销', 'expired': '已到期', 'stale': '需重新核验',
    'content_excluded': '内容边界拦截', 'unknown_rating': '分级未知',
    'not_required_for_safe_v1': '非 NSFW 作品'}


def parse_id_file(upload):
    """Read no more than 64 KiB; never persist uploads or interpret URLs/code."""
    if not upload.name.lower().endswith('.txt'):
        raise ValueError('请选择 .txt 文件，每行一个 Bangumi ID。')
    if upload.size > MAX_FILE_BYTES:
        raise ValueError('TXT 文件不能超过 64 KiB。')
    raw = upload.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('TXT 文件不能超过 64 KiB。')
    try:
        text = raw.decode('utf-16' if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else 'utf-8-sig')
    except UnicodeError:
        raise ValueError('文件编码无法识别，请保存为 UTF-8 或带 BOM 的 UTF-16。') from None
    ids, duplicates, invalid = set(), 0, []
    lines = [(number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not lines or len(lines) > MAX_ITEMS:
        raise ValueError('每次须导入 1–500 个非空行，重复行也计入行数限制。')
    for number, line in lines:
        try:
            if len(line) > 10:
                raise ValueError()
            identity = clean_id(line)
        except ValueError:
            invalid.append(str(number))
            continue
        duplicates += identity in ids
        ids.add(identity)
    if invalid:
        raise ValueError('以下行不是有效的正整数 Bangumi ID：' + '、'.join(invalid[:10])
            + ('（仅显示前 10 行）' if len(invalid) > 10 else ''))
    return {'ids': sorted(ids), 'duplicates': duplicates}


def _disposition(anime, row):
    if anime is None:
        return 'blocked', '推荐目录中不存在此 ID'
    if anime.nsfw is not True:
        return 'blocked', '不是明确的 NSFW 标记作品'
    if not general_adult_boundary(anime):
        return 'blocked', '命中现有内容边界拦截'
    if not feature_tags(anime.tags, (anime.title_zh, anime.title_jp)):
        return 'blocked', '缺少有效题材'
    if review._decision(anime, row)['review_valid']:
        return 'skip', '已有当前有效审核，保留原依据和期限'
    return 'ready', '可登记本次人工审核结论'


def _preview_rows(ids):
    works = {a.bangumi_id: a for a in RecommendationAnime.objects.filter(
        bangumi_id__in=ids).select_related('content_review').order_by('bangumi_id')}
    rows, bindings = [], []
    for identity in ids:
        anime = works.get(identity)
        row = getattr(anime, 'content_review', None) if anime else None
        disposition, reason = _disposition(anime, row)
        binding = {'id': identity, 'version': row.review_version if row else 0,
            'catalog': review.catalog_revision(anime) if anime else None, 'disposition': disposition}
        bindings.append(binding)
        status = review._decision(anime, row)['effective_status'] if anime else 'missing'
        rows.append({'bangumi_id': identity, 'title_zh': anime.title_zh if anime else '目录中不存在',
            'title_jp': anime.title_jp if anime else '', 'season': anime.season if anime else '',
            'status_type': anime.status_type if anime else '', 'disposition': disposition, 'reason': reason,
            'current_status': status, 'current_status_label': STATUS_LABELS.get(status, '目录中不存在')})
    return rows, bindings


def _validate_ids(ids):
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ITEMS
            or any(type(i) is not int or not 1 <= i <= 2147483647 for i in ids)
            or ids != sorted(set(ids))):
        raise ValidationError('批次 ID 必须为 1–500 个去重并升序排列的正整数。')


def prepare_batch(actor, ids):
    reviewer = review.require_reviewer(actor)
    _validate_ids(ids)
    rows, bindings = _preview_rows(ids)
    counts = {key: sum(r['disposition'] == key for r in rows) for key in ('ready', 'skip', 'blocked')}
    ticket = signing.dumps({'actor': reviewer.pk, 'operation': str(uuid4()),
        'policy': review.POLICY_VERSION, 'items': bindings}, salt=SALT, compress=True)
    return {'rows': rows, 'counts': counts, 'ticket': ticket,
        'can_submit': counts['ready'] > 0 and counts['blocked'] == 0}


def _load_ticket(actor, ticket):
    if not isinstance(ticket, str) or len(ticket) > MAX_TICKET_LENGTH:
        raise signing.BadSignature()
    body = signing.loads(ticket, salt=SALT, max_age=TICKET_AGE)
    if (not isinstance(body, dict) or set(body) != {'actor', 'operation', 'policy', 'items'}
            or body['actor'] != actor.pk or body['policy'] != review.POLICY_VERSION
            or not isinstance(body['items'], list)):
        raise signing.BadSignature()
    try:
        UUID(body['operation'])
        ids = [item['id'] for item in body['items']]
        _validate_ids(ids)
        for item in body['items']:
            if (set(item) != {'id', 'version', 'catalog', 'disposition'}
                    or type(item['version']) is not int or not 0 <= item['version'] <= review.MAX_VERSION
                    or item['disposition'] not in ('ready', 'skip')
                    or not isinstance(item['catalog'], str) or len(item['catalog']) != 64):
                raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
        raise signing.BadSignature() from None
    return body


def restore_preview(actor, ticket):
    reviewer = review.require_reviewer(actor)
    body = _load_ticket(reviewer, ticket)
    rows, bindings = _preview_rows([item['id'] for item in body['items']])
    if bindings != body['items']:
        raise review.ReviewConflict('预检内容已变化，请重新上传文件。')
    counts = {key: sum(r['disposition'] == key for r in rows) for key in ('ready', 'skip', 'blocked')}
    return {'rows': rows, 'counts': counts, 'ticket': ticket,
        'can_submit': counts['ready'] > 0 and counts['blocked'] == 0}


@transaction.atomic
def approve_batch(*, actor, ticket, changes):
    """Catalog -> epoch -> reviews, matching the single-work writer lock order.

    One transaction and per-work receipts: bulk writes avoid 500 network round
    trips of the single-work service. No content inference or automatic approval.
    """
    reviewer = review.require_reviewer(actor)
    body = _load_ticket(reviewer, ticket)
    if not isinstance(changes, dict) or set(changes) != {
            'reason', 'evidence_reference', 'days', 'exclusions_confirmed', 'general_adult_confirmed', 'confirmed'}:
        raise ValidationError('批量审核字段无效。')
    if changes['confirmed'] is not True:
        raise ValidationError('请核对清单并明确确认本批作品的审核操作。')
    reference = changes['evidence_reference']
    if not isinstance(reference, str) or not 1 <= len(reference) <= 100:
        raise ValidationError('批次依据编号须为 1–100 个字符。')
    ready = [item for item in body['items'] if item['disposition'] == 'ready']
    if not ready:
        raise ValidationError('本批没有需要登记的作品。')
    payloads = {}
    for item in ready:
        serializer = review.MutationSerializer(data={
            **{k: v for k, v in changes.items() if k != 'confirmed'},
            'evidence_reference': f'{reference}.bgm.{item["id"]}',
            'expected_version': item['version'], 'expected_catalog_revision': item['catalog'],
            'expected_policy_version': body['policy'],
            'operation_id': uuid5(UUID(body['operation']), str(item['id']))})
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if not payload['exclusions_confirmed'] or not payload['general_adult_confirmed']:
            raise ValidationError('批量通过须确认每部作品均有一般成人内容核验依据，并排除禁止情形。')
        payloads[item['id']] = payload
    ids = [item['id'] for item in body['items']]
    works = list(RecommendationAnime.objects.select_for_update().filter(
        bangumi_id__in=ids).order_by('bangumi_id'))
    if [a.bangumi_id for a in works] != ids:
        raise review.ReviewConflict('批次作品已变化，请重新上传并预检；本批未写入。')
    # A no-op update is never used as an idempotency claim.
    state, _ = State.objects.get_or_create(pk=1)
    state = State.objects.select_for_update().get(pk=1)
    reviewer = review.require_reviewer(actor)  # permissions may change while waiting
    rows = {r.anime_id: r for r in Review.objects.select_for_update().filter(
        anime__bangumi_id__in=ids).order_by('anime_id')}
    receipts = list(Event.objects.filter(operation_id__in=[p['operation_id'] for p in payloads.values()]))
    fingerprints = {identity: review.review_fingerprint(reviewer, identity, 'approve', p)
        for identity, p in payloads.items()}
    if receipts:
        by_operation = {e.operation_id: e for e in receipts}
        row_ids = {a.bangumi_id: rows[a.pk].pk for a in works if a.pk in rows}
        if len(receipts) != len(ready) or any(
                p['operation_id'] not in by_operation
                or by_operation[p['operation_id']].request_fingerprint != fingerprints[identity]
                or by_operation[p['operation_id']].review_id != row_ids.get(identity)
                for identity, p in payloads.items()):
            raise review.ReviewConflict('批次操作回执不一致，请重新读取；本批未写入。')
        return {'approved': len(ready), 'skipped': len(ids) - len(ready),
            'replayed': True, 'operation': body['operation']}
    for anime, item in zip(works, body['items']):
        row = rows.get(anime.pk)
        disposition, _ = _disposition(anime, row)
        if (item['catalog'] != review.catalog_revision(anime)
                or item['version'] != (row.review_version if row else 0)
                or item['disposition'] != disposition):
            raise review.ReviewConflict('作品资料或审核状态已变化，请重新上传并预检；本批未写入。')
    if state.version + len(ready) > review.MAX_VERSION or any(
            item['version'] >= review.MAX_VERSION for item in ready):
        raise review.ReviewConflict('审核版本已达上限，不能覆盖。')
    now = timezone.now()
    created, updated, events = [], [], []
    approved_rows = {}
    for anime in works:
        data = payloads.get(anime.bangumi_id)
        if data is None:
            continue
        row = rows.get(anime.pk)
        if row is None:
            row = Review(anime=anime)
            created.append(row)
        else:
            updated.append(row)
        previous_status, previous_version = row.status if row.pk else 'unreviewed', row.review_version
        row.status = 'approved'; row.review_version = previous_version + 1
        row.policy_version = body['policy']; row.catalog_revision = data['expected_catalog_revision']
        row.evidence_reference = data['evidence_reference']; row.exclusions_confirmed = True
        row.reviewer = reviewer; row.reviewed_at = now; row.never_expires = data['days'] == 0
        row.expires_at = None if row.never_expires else now + timedelta(days=data['days'])
        row.updated_at = now
        approved_rows[anime.bangumi_id] = row
        events.append(Event(review=row, actor=reviewer, operation_id=data['operation_id'], action='approve',
            request_fingerprint=fingerprints[anime.bangumi_id], previous_status=previous_status,
            resulting_status='approved', previous_version=previous_version, applied_version=row.review_version,
            applied_release_version=state.version + len(events) + 1, changed=True, reason=data['reason'],
            evidence_reference=row.evidence_reference, catalog_revision=row.catalog_revision,
            policy_version=row.policy_version, resulting_expires_at=row.expires_at,
            resulting_never_expires=row.never_expires))
    Review.objects.bulk_create(created)
    Review.objects.bulk_update(updated, ['status', 'review_version', 'policy_version', 'catalog_revision',
        'evidence_reference', 'exclusions_confirmed', 'reviewer', 'reviewed_at', 'never_expires', 'expires_at', 'updated_at'])
    # Newly inserted review PKs are now available on PostgreSQL.
    for event, identity in zip(events, payloads):
        event.review = approved_rows[identity]
    Event.objects.bulk_create(events)
    state.version += len(ready); state.save(update_fields=['version'])
    return {'approved': len(ready), 'skipped': len(ids) - len(ready),
        'replayed': False, 'operation': body['operation']}
