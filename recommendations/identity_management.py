"""Owner-only corrections, separate from immutable import provenance and public works."""
import re
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from .binding import IdentityConflict as BindingConflict
from .content_scope import catalog_for_scope, ensure_scope, lock_scope
from .matching import catalog_fingerprint, digest, record_fingerprint
from .matching import effective_metadata, metadata_signature
from .models import RecommendationIdentityOverride, RecommendationIdentityCorrection
from .resolution import resolved_records
from anime.models import UserAnimeRecord

SALT = 'journal.identity-management.v1'
FIELDS = ('bangumi_id', 'title_zh', 'title_jp', 'season', 'status_type')


class IdentityConflict(BindingConflict):
    def __init__(self, detail=None):
        super().__init__({'code': 'identity_changed', 'detail': detail or self.default_detail})


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['包含不支持的字段。']})
        return super().to_internal_value(data)


class ManagementQuery(StrictSerializer):
    record_id = serializers.IntegerField(min_value=1, required=False)
    page = serializers.IntegerField(min_value=1, default=1)
    keyword = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CorrectionRequest(StrictSerializer):
    record_id = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=('replace', 'unlink', 'undo'))
    version = serializers.IntegerField(min_value=0, max_value=2147483646)
    operation_id = serializers.UUIDField()
    ticket = serializers.CharField(max_length=4096)
    bangumi_id = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)

    def validate(self, data):
        if ('bangumi_id' in data) != (data['action'] == 'replace'):
            raise serializers.ValidationError('仅更换关联需要作品编号。')
        return data


def base_fingerprint(row):
    return digest([record_fingerprint(row), row['verified_bangumi_id'], row['anime__bgm_id'],
                   *[row.get(key) for key in ('recommendation_identity__catalog_anime__bangumi_id',
                     'recommendation_identity__active', 'recommendation_identity__version')]])


def snapshot(override):
    return {key: getattr(override, key) for key in ('enabled', 'target_id', 'record_fingerprint', 'catalog_fingerprint')} if override else {'enabled': False, 'target_id': None, 'record_fingerprint': '', 'catalog_fingerprint': ''}


def scoped_rows(user, scope, **kwargs):
    return resolved_records(user, allowed_adult_ids=set(catalog_for_scope(scope).filter(nsfw=True).values_list('bangumi_id', flat=True)), **kwargs)


def proof_for(user, row, scope, target=None):
    return signing.dumps({'owner': user.pk, 'record': row['pk'], 'base': base_fingerprint(row),
        'version': row.get('identity_override__version') or 0, 'scope': scope['scope_revision'],
        'target': target.bangumi_id if target else None, 'catalog': catalog_fingerprint(target) if target else None}, salt=SALT)


def management_contract(user, options, scope):
    rows = scoped_rows(user, scope, record_id=options.get('record_id'))
    if options.get('record_id') and not rows:
        raise NotFound('私人记录不存在。')
    keyword = options.get('keyword', '').strip()
    if not options.get('record_id'):
        rows = [r for r in rows if r['identity_state'] != 'unlinked' or r.get('identity_override__version')]
        if keyword:
            rows = [r for r in rows if keyword.casefold() in (r['metadata']['title_zh'] + r['metadata']['title_jp']).casefold()]
    count = len(rows)
    start = (options['page'] - 1) * 10
    selected = rows[start:start + 10]
    results = []
    for row in selected:
        current = {key: row['catalog'][key] for key in FIELDS} if row['catalog'] else None
        version = row.get('identity_override__version') or 0
        latest = RecommendationIdentityCorrection.objects.filter(override__record_id=row['pk']).first()
        item = {'record_id': row['pk'], 'title': row['metadata']['title_zh'], 'original_title': row['metadata']['title_jp'],
            'season': row['metadata']['season'], 'state': row['identity_state'], 'current': current, 'version': version,
            'corrected': bool(row.get('identity_override__enabled')), 'ticket': proof_for(user, row, scope),
            'can_undo': bool(latest and latest.action != 'undo' and latest.applied_version == version), 'candidates': []}
        if row.get('identity_override__enabled') and row.get('identity_override__target__bangumi_id') is None:
            item['state'] = 'user_unlinked'
        if options.get('record_id'):
            query = keyword or row['metadata']['title_zh'] or row['metadata']['title_jp']
            match = re.fullmatch(r'(?:https?://(?:bgm\.tv|bangumi\.tv)/subject/)?([1-9]\d*)/?', query)
            catalog = catalog_for_scope(scope)
            if match:
                identity = int(match[1])
                catalog = catalog.filter(bangumi_id=identity) if identity <= 2147483647 else catalog.none()
            else:
                catalog = catalog.filter(Q(title_zh__icontains=query) | Q(title_jp__icontains=query))
            for target in catalog.order_by('-score', 'bangumi_id')[:10]:
                item['candidates'].append({**{key: getattr(target, key) for key in FIELDS}, 'ticket': proof_for(user, row, scope, target)})
        results.append(item)
    return {'count': count, 'page': options['page'], 'next_page': options['page'] + 1 if start + len(selected) < count else None, 'results': results}


@transaction.atomic
def correct_identity(user, data, scope):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    record = UserAnimeRecord.objects.select_for_update().filter(user=user, pk=data['record_id']).first()
    if record is None:
        raise NotFound('私人记录不存在。')
    scope = lock_scope(user, scope)
    override = RecommendationIdentityOverride.objects.select_for_update().filter(record=record).first()
    request_hash = digest({key: str(value) for key, value in data.items()})
    receipt = RecommendationIdentityCorrection.objects.filter(override=override, operation_id=data['operation_id']).first() if override else None
    if receipt:
        if receipt.request_hash != request_hash:
            raise IdentityConflict('该操作编号已被其他请求使用。')
        return {'record_id': record.pk, 'version': override.version, 'status': 'already_applied'}
    row = scoped_rows(user, scope, record_id=record.pk)[0]
    try:
        proof = signing.loads(data['ticket'], salt=SALT, max_age=900)
    except signing.BadSignature:
        raise IdentityConflict('核对凭据失效，请刷新后重新选择。') from None
    if (proof.get('owner') != user.pk or proof.get('record') != record.pk or proof.get('base') != base_fingerprint(row)
        or proof.get('version') != data['version'] or data['version'] != (override.version if override else 0)
        or proof.get('scope') != scope['scope_revision']):
        raise IdentityConflict()
    previous = snapshot(override)
    values = {'enabled': True, 'target_id': None, 'record_fingerprint': record_fingerprint(row), 'catalog_fingerprint': ''}
    if data['action'] == 'replace':
        target = catalog_for_scope(scope).select_for_update().filter(bangumi_id=data['bangumi_id']).first()
        if target is None or proof.get('target') != target.bangumi_id or proof.get('catalog') != catalog_fingerprint(target):
            raise IdentityConflict('候选作品已变化，请重新搜索。')
        values.update(target_id=target.pk, catalog_fingerprint=catalog_fingerprint(target))
    elif data['action'] == 'undo':
        latest = RecommendationIdentityCorrection.objects.filter(override=override).first()
        if not latest or latest.action == 'undo' or latest.applied_version != data['version'] or override.record_fingerprint != record_fingerprint(row):
            raise IdentityConflict('只能撤销尚未发生其他变化的最近一次更正。')
        values = latest.previous
        if values.get('enabled') and values.get('target_id'):
            target = catalog_for_scope(scope).select_for_update().filter(pk=values['target_id']).first()
            if target is None or catalog_fingerprint(target) != values['catalog_fingerprint']:
                raise IdentityConflict('之前的作品已变化，不能自动恢复，请重新选择。')
        if not values.get('enabled'):
            original = row['verified_bangumi_id'] or row['anime__bgm_id'] or row.get('recommendation_identity__catalog_anime__bangumi_id')
            if original and not catalog_for_scope(scope).select_for_update().filter(bangumi_id=original).exists():
                raise IdentityConflict('之前的标准作品当前不可用，请重新选择。')
    ensure_scope(user, scope)
    if override is None:
        override = RecommendationIdentityOverride(record=record, version=0)
    for key, value in values.items():
        setattr(override, key, value)
    override.version += 1
    override.save()
    RecommendationIdentityCorrection.objects.create(override=override, operation_id=data['operation_id'], request_hash=request_hash,
        action=data['action'], previous=previous, applied_version=override.version, target_id=override.target_id)
    return {'record_id': record.pk, 'version': override.version, 'status': 'applied'}


def export_correction(user, record):
    override = getattr(record, 'identity_override', None)
    if not override or not override.enabled:
        return None
    metadata = record.effective_anime_metadata()
    row = {'anime_id': record.anime_id, 'metadata_initialized': True,
        'private_chinese_title': metadata['chinese_title'], 'private_japanese_title': metadata['japanese_title'],
        'private_airing_period': metadata['airing_period'], 'private_base_tags': []}
    payload = {'owner': user.pk, 'target': override.target.bangumi_id if override.target_id else None,
        'metadata': digest(metadata_signature(effective_metadata(row))), 'catalog': override.catalog_fingerprint,
        'record_valid': override.record_fingerprint == record_fingerprint(row)}
    return {'bangumi_id': payload['target'], 'proof': signing.dumps(payload, salt=SALT + '.backup')}


def restore_correction(user, record, reference):
    """Invalid explicit corrections abort the atomic import; never silently fall back."""
    if reference is None:
        return
    try:
        proof = signing.loads(reference['proof'], salt=SALT + '.backup')
    except (signing.BadSignature, KeyError, TypeError):
        raise serializers.ValidationError('私人关联更正凭据无效，请使用原账号的完整备份。') from None
    from .resolution import record_row
    row = record_row(record)
    if (proof.get('owner') != user.pk or proof.get('target') != reference['bangumi_id']
        or proof.get('metadata') != digest(metadata_signature(effective_metadata(row)))):
        raise serializers.ValidationError('私人关联更正与账号或作品资料不一致。')
    target = None
    if proof['target'] is not None:
        from .models import RecommendationAnime
        target = RecommendationAnime.objects.filter(bangumi_id=proof['target']).first()
        if target is None:
            raise serializers.ValidationError('备份关联的标准作品已不存在，请先核对。')
    RecommendationIdentityOverride.objects.update_or_create(record=record, defaults={'enabled': True, 'target': target,
        'record_fingerprint': record_fingerprint(row) if proof.get('record_valid', True) else '',
        'catalog_fingerprint': proof['catalog'], 'version': 1})
