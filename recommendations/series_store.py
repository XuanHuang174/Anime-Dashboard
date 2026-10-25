"""Serialized atomic publication and auditable manual corrections, no HTTP IO."""
from datetime import datetime
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from .series_contract import KINDS, MAPPING_VERSION
from .series_models import (SeriesState, SeriesNode, SeriesObservation, SeriesHead,
                            SeriesAssertion, SeriesOverrideEvent)
from .series_source import digest, MAX_NODES, MAX_EDGES, seed_ids


class SeriesConflict(APIException):
    status_code = 409
    default_detail = '系列资料版本已变化，请重新读取。'


def locked_state():
    SeriesState.objects.get_or_create(pk=1)
    return SeriesState.objects.select_for_update().get(pk=1)


def _basis(head):
    return (head.current_id or head.last_attempt_id) if head else None


@transaction.atomic
def publish_archive(prepared):
    """Only server-local prepare_archive output; never accept request JSON here.

    One bounded batch is all-or-nothing. Existing observations are immutable.
    Replay verifies each root independently so disjoint incremental batches work.
    """
    body = {key: value for key, value in prepared.items() if key != 'fingerprint'}
    if digest(body) != prepared.get('fingerprint') or prepared['mapping_version'] != MAPPING_VERSION:
        raise ValidationError('归档预检内容或映射版本已变化。')
    seeds = seed_ids(prepared['seed_ids'])
    if len(prepared['nodes']) > MAX_NODES or len(prepared['assertions']) > MAX_EDGES:
        raise ValidationError('预检批次超限。')
    state = locked_state()
    source_at = datetime.fromisoformat(prepared['source_at'])
    if state.latest_source_at and source_at < state.latest_source_at:
        raise SeriesConflict('不能用旧归档回退当前公共节点。')
    existing = {row.source_id: row for row in SeriesObservation.objects.filter(
        source_id__in=seeds, snapshot_version=prepared['snapshot_version'])}
    nodes = {row['bangumi_id']: row for row in prepared['nodes']}
    rows = {identity: [row for row in prepared['assertions'] if row['source_bangumi_id'] == identity] for identity in seeds}
    fingerprints = {identity: digest({'archive': prepared['archive_sha256'], 'mapping': MAPPING_VERSION,
        'source': identity, 'nodes': [nodes[key] for key in sorted({identity} | {row['target_bangumi_id'] for row in rows[identity]})],
        'assertions': rows[identity]}) for identity in seeds}
    for identity, observation in existing.items():
        if observation.fingerprint != fingerprints[identity]:
            raise SeriesConflict('同一快照的条目内容不同，拒绝覆盖历史观察。')
    pending = [identity for identity in seeds if identity not in existing]
    if not pending:
        return {'changed': False, 'revision': state.revision, 'published': 0, 'partial': 0, 'replayed': len(seeds)}
    # A partial observation may create audit-only nodes, but not overwrite old metadata.
    node_values = [SeriesNode(**row) for row in prepared['nodes']]
    if prepared['partial']:
        SeriesNode.objects.bulk_create(node_values, ignore_conflicts=True, batch_size=500)
    else:
        SeriesNode.objects.bulk_create(node_values, update_conflicts=True, unique_fields=['bangumi_id'],
            update_fields=[key for key in next(iter(nodes.values())) if key != 'bangumi_id'], batch_size=500)
    SeriesHead.objects.bulk_create([SeriesHead(source_id=identity) for identity in pending], ignore_conflicts=True)
    heads = {row.source_id: row for row in SeriesHead.objects.filter(source_id__in=pending)}
    published = 0
    for identity in pending:
        coverage = 'partial' if prepared['partial'] else ('observed' if rows[identity] else 'empty_observed')
        observation = SeriesObservation.objects.create(source_id=identity,
            snapshot_version=prepared['snapshot_version'], source_at=source_at, observed_at=timezone.now(),
            source_reference=prepared['source_reference'], archive_sha256=prepared['archive_sha256'],
            payload_sha256=prepared['payload_sha256'], mapping_version=MAPPING_VERSION,
            fingerprint=fingerprints[identity], coverage=coverage, diagnostics={
                **prepared['diagnostics'], 'subject_payload_sha256': prepared['subject_payload_sha256']})
        SeriesAssertion.objects.bulk_create([SeriesAssertion(observation=observation,
            target_id=row['target_bangumi_id'], kind=row['kind'], source_relation=row['source_relation'],
            source_order=row['source_order'], requires_review=row['requires_review']) for row in rows[identity]], batch_size=500)
        head = heads[identity]
        head.last_attempt = observation
        if not prepared['partial']:
            head.current = observation
            published += 1
        head.save(update_fields=('current', 'last_attempt'))
    state.revision += 1
    if not prepared['partial']:
        state.latest_source_at = source_at
    state.save(update_fields=('revision', 'latest_source_at'))
    return {'changed': True, 'revision': state.revision, 'published': published,
            'partial': len(pending) - published, 'replayed': len(existing)}


@transaction.atomic
def record_failed_attempt(seeds):
    """Generic receipt on EXISTING verified sources only, never store raw errors."""
    seeds = seed_ids(seeds)
    heads = list(SeriesHead.objects.filter(source_id__in=seeds))
    if not heads:
        return
    state = locked_state()
    for head in heads:
        observation = SeriesObservation.objects.create(source_id=head.source_id,
            snapshot_version=f'failed-{uuid.uuid4()}', source_at=timezone.now(), observed_at=timezone.now(),
            source_reference='', archive_sha256='', payload_sha256='', fingerprint='',
            mapping_version=MAPPING_VERSION, coverage='failed', diagnostics={'code': 'archive_preparation_failed'})
        # Re-read under the shared lock; never restore a stale current pointer.
        SeriesHead.objects.filter(pk=head.pk).update(last_attempt=observation)
    state.revision += 1
    state.save(update_fields=('revision',))


@transaction.atomic
def revise_relation(*, actor, source_id, target_id, action, kind, reason, evidence_reference,
                    operation_id, expected_revision):
    actor = get_user_model().objects.select_for_update().filter(pk=getattr(actor, 'pk', None)).first()
    if not (actor and actor.is_active and actor.is_staff and actor.has_perm('recommendations.review_series_relations')):
        raise PermissionDenied('没有系列关系修订权限。')
    seeds, target_id = seed_ids([source_id]), seed_ids([target_id])[0]
    source_id = seeds[0]
    if source_id == target_id or action not in ('replace', 'suppress', 'clear'):
        raise ValidationError('修订动作或目标无效。')
    if ((action == 'replace' and kind not in {row.key for row in KINDS})
            or (action != 'replace' and kind != '')):
        raise ValidationError('关系种类无效。')
    for text, maximum in ((reason, 240), (evidence_reference, 500)):
        if not isinstance(text, str) or not text.strip() or len(text) > maximum or any(ord(c) < 32 for c in text):
            raise ValidationError('必须填写有效的核验原因及依据。')
    try:
        operation_id = uuid.UUID(str(operation_id))
    except ValueError:
        raise ValidationError('操作编号无效。') from None
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValidationError('资料版本无效。')
    fingerprint = digest([actor.pk, source_id, target_id, action, kind, reason, evidence_reference, expected_revision])
    state = locked_state()
    previous = SeriesOverrideEvent.objects.filter(operation_id=operation_id).first()
    if previous:
        if previous.request_fingerprint != fingerprint:
            raise SeriesConflict('操作编号已用于不同修订。')
        return previous
    if state.revision != expected_revision:
        raise SeriesConflict()
    head = SeriesHead.objects.filter(source_id=source_id).first()
    if not head or not _basis(head) or SeriesNode.objects.filter(
            bangumi_id__in=(source_id, target_id), subject_type=2).count() != 2:
        raise ValidationError('来源观察和两端动画身份必须先核验。')
    state.revision += 1
    event = SeriesOverrideEvent.objects.create(source_id=source_id, target_id=target_id,
        base_observation_id=_basis(head), actor=actor, action=action, kind=kind, reason=reason,
        evidence_reference=evidence_reference, operation_id=operation_id, request_fingerprint=fingerprint,
        applied_revision=state.revision)
    state.save(update_fields=('revision',))
    return event
