"""Explicit, audited writes to private aliases only. No Anime/journal metadata mutation."""
import logging
from django.core import signing
from django.db import DatabaseError, transaction
from rest_framework.exceptions import APIException, NotFound

from anime.models import UserAnimeRecord
from .matching import (MATCHING_VERSION, catalog_fingerprint, catalog_matcher, digest,
                       effective_metadata, metadata_signature, record_fingerprint)
from .models import RecommendationAnime, RecommendationIdentityEvent, RecommendationRecordIdentity
from .resolution import record_row

logger = logging.getLogger(__name__)
SALT = 'recommendations.identity-candidate.v1'


class IdentityConflict(APIException):
    status_code = 409
    default_detail = '记录或作品资料已变化，请重新读取候选后确认。'


def link_payload(link):
    if link is None:
        return {}
    return {field: getattr(link, field) for field in ('catalog_anime_id', 'source', 'record_fingerprint',
            'catalog_fingerprint', 'matching_version', 'active')}


def candidate_ticket(row, target):
    return signing.dumps({'owner': row['user_id'], 'record': row['pk'], 'target': target['bangumi_id'],
                          'record_fp': record_fingerprint(row), 'catalog_fp': catalog_fingerprint(target),
                          'version': row.get('identity_version', 0), 'rule': MATCHING_VERSION}, salt=SALT)


@transaction.atomic
def bind_record(*, record_id, user_id, target_id, source, expected_version=None, ticket=None, batch_id=None, expected_catalog_fp=None):
    record = UserAnimeRecord.objects.select_for_update().filter(pk=record_id, user_id=user_id).first()
    if record is None:
        raise NotFound('记录不存在。')
    row = record_row(record)
    target = RecommendationAnime.objects.select_for_update().filter(bangumi_id=target_id, nsfw=False).first()
    if target is None:
        raise NotFound('该作品不可用于推荐。')
    direct = row['anime__bgm_id']
    if direct:
        if direct != target_id:
            raise IdentityConflict('已有权威身份，不能用私人关联覆盖。')
        return {'status': 'authoritative', 'version': 0}
    link = RecommendationRecordIdentity.objects.select_for_update().filter(record=record).first()
    fp, target_fp = record_fingerprint(row), catalog_fingerprint(target)
    if expected_catalog_fp is not None and target_fp != expected_catalog_fp:
        raise IdentityConflict()
    version = link.version if link else 0
    if ticket is not None:
        try:
            proof = signing.loads(ticket, salt=SALT, max_age=900)
        except signing.BadSignature:
            raise IdentityConflict() from None
        if (proof.get('owner') != user_id or proof.get('record') != record_id or proof.get('target') != target_id
            or proof.get('record_fp') != fp or proof.get('catalog_fp') != target_fp
            or proof.get('rule') != MATCHING_VERSION or proof.get('version') != expected_version):
            raise IdentityConflict()
    # Safe retry after a committed request whose response was lost; do not create another event.
    if link and link.active and link.catalog_anime_id == target.pk and link.record_fingerprint == fp and link.catalog_fingerprint == target_fp:
        return {'status': 'already_linked', 'version': version}
    if expected_version is not None and version != expected_version:
        raise IdentityConflict()
    if version >= 2_147_483_647:
        raise IdentityConflict()
    previous = link_payload(link)
    values = {'catalog_anime': target, 'source': source, 'record_fingerprint': fp,
              'catalog_fingerprint': target_fp, 'matching_version': MATCHING_VERSION,
              'active': True, 'version': version + 1}
    if link is None:
        link = RecommendationRecordIdentity.objects.create(record=record, **values)
    else:
        for field, value in values.items():
            setattr(link, field, value)
        link.save(update_fields=[*values, 'updated_at'])
    RecommendationIdentityEvent.objects.create(record=record, batch_id=batch_id, action='linked',
                                               previous=previous, applied_version=link.version)
    return {'status': 'linked', 'version': link.version}


def auto_link(record, *, matcher=None, batch_id=None):
    """For explicit create/import/worker operations only, never GET or general save signals."""
    if record.anime.bgm_id:
        return {'status': 'authoritative'}
    with transaction.atomic():
        locked = UserAnimeRecord.objects.select_for_update().get(pk=record.pk)
        if RecommendationRecordIdentity.objects.filter(record=locked).exists():
            return {'status': 'existing_link'}  # Never rebind stale, revoked or user-confirmed aliases.
        row = record_row(locked)
        result = (matcher or catalog_matcher(force=True)).match(effective_metadata(row))
        if result['status'] != 'strong_unique':
            return {'status': result['status']}
        # Recheck the target's current safety/metadata in the write transaction.
        target = RecommendationAnime.objects.filter(bangumi_id=result['target']['bangumi_id'], nsfw=False).first()
        if target is None or catalog_fingerprint(target) != catalog_fingerprint(result['target']):
            raise IdentityConflict()
        return bind_record(record_id=locked.pk, user_id=locked.user_id, target_id=target.bangumi_id,
                           source='auto_strict', expected_version=0, batch_id=batch_id,
                           expected_catalog_fp=catalog_fingerprint(result['target']))


def try_auto_link(record, matcher=None):
    # Optional recommendation metadata must not break an otherwise valid journal write.
    try:
        with transaction.atomic():
            return auto_link(record, matcher=matcher)
    except (DatabaseError, APIException):
        logger.warning('Optional identity resolution deferred for record %s', record.pk)
        return {'status': 'pending'}


@transaction.atomic
def rollback_event(event):
    record = UserAnimeRecord.objects.select_for_update().filter(pk=event.record_id).first()
    if record is None:
        return 'missing_record'
    link = RecommendationRecordIdentity.objects.select_for_update().filter(record=record).first()
    if link is None or link.version != event.applied_version or link.version >= 2_147_483_647:
        return 'changed_link'
    if record_fingerprint(record_row(record)) != link.record_fingerprint:
        return 'changed_metadata'
    previous = link_payload(link)
    if event.previous:
        prior = RecommendationAnime.objects.filter(pk=event.previous['catalog_anime_id']).first()
        if prior is None:
            return 'missing_previous_catalog'
        if event.previous.get('active') and (prior.nsfw is not False or
                event.previous['catalog_fingerprint'] != catalog_fingerprint(prior)):
            return 'changed_previous_catalog'
        for field, value in event.previous.items():
            setattr(link, field, value)
    else:
        link.active = False  # Recoverable revocation, NOT bulk deletion.
    link.version += 1
    link.save()
    RecommendationIdentityEvent.objects.create(record=record, action='rollback', previous=previous,
                                               applied_version=link.version)
    return 'rolled_back'


def export_reference(user, row):
    if row is None or row['identity_state'] != 'linked':
        return None
    payload = {'owner': user.pk, 'target': row['resolved_bangumi_id'],
               'metadata': digest(metadata_signature(row['metadata'])), 'catalog_fp': catalog_fingerprint(row['catalog'])}
    return {'bangumi_id': payload['target'], 'proof': signing.dumps(payload, salt='recommendations.backup-reference.v1')}


def restore_reference(user, record, reference):
    if not reference:
        return False
    try:
        proof = signing.loads(reference.get('proof', ''), salt='recommendations.backup-reference.v1', max_age=365*86400)
    except signing.BadSignature:
        return False
    row = record_row(record)
    target = RecommendationAnime.objects.filter(bangumi_id=reference.get('bangumi_id'), nsfw=False).first()
    if (target is None or proof.get('owner') != user.pk or proof.get('target') != target.bangumi_id
        or proof.get('metadata') != digest(metadata_signature(effective_metadata(row)))
        or proof.get('catalog_fp') != catalog_fingerprint(target)):
        return False
    bind_record(record_id=record.pk, user_id=user.pk, target_id=target.bangumi_id, source='import_verified',
                expected_catalog_fp=proof['catalog_fp'])
    return True


def import_links_best_effort(user, records, references):
    try:
        with transaction.atomic():
            matcher = catalog_matcher()
    except (DatabaseError, APIException):
        logger.warning('Optional identity matching deferred during import')
        return
    for record, reference in zip(records, references):
        try:
            with transaction.atomic():
                restored = restore_reference(user, record, reference)
        except (DatabaseError, APIException):
            restored = False
        if not restored:
            try_auto_link(record, matcher=matcher)
