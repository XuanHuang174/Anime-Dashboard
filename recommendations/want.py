"""3C: authenticated, lossless and idempotent private journal creation only."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.exceptions import APIException, NotFound

from anime.models import Anime, UserAnimeRecord
from anime.tag_services import initialize_record_tag_colors
from .identity import public_cover_url, recommendation_identity, safe_catalog
from .taxonomy import canonical_tags
from .normalization import clean_id
from .matching import IdentityCatalogUnavailable

CONTRACT_VERSION = 'recommendations.want.v1'


class WantConflict(APIException):
    status_code = 409

    def __init__(self, code, message, **extra):
        self.payload = {'code': code, 'detail': message, **extra}
        super().__init__(self.payload)


class WantCapacityExceeded(WantConflict):
    status_code = 403


def want_contract(record, bangumi_id, created):
    # Public identity, shared PK and private record PK are separate namespaces.
    # Never return personal score, review, other-user IDs or inferred replacements.
    return {'contract_version': CONTRACT_VERSION, 'bangumi_id': bangumi_id,
            'anime_id': record.anime_id, 'record_id': record.pk,
            'watch_status': record.watch_status, 'created': created,
            'outcome': 'created' if created else 'existing', 'content_rating': 'non_adult'}


@transaction.atomic
def add_want(*, user, bangumi_id):
    if not getattr(user, 'is_authenticated', False):
        raise NotFound('该作品不可用于推荐。')
    try:
        bangumi_id = clean_id(bangumi_id)
    except ValueError:
        raise NotFound('该作品不可用于推荐。') from None
    # Same per-user lock as standard journal add/create and JSON replacement.
    # Catalog lock also serializes different users creating the same shared ID,
    # and prevents its safety classification from changing mid-transaction.
    if get_user_model().objects.select_for_update().only('pk').filter(pk=user.pk, is_active=True).first() is None:
        raise NotFound('账号不可用。')
    catalog = safe_catalog().select_for_update().filter(bangumi_id=bangumi_id).first()
    if catalog is None:
        raise NotFound('该作品不可用于推荐。')
    direct = UserAnimeRecord.objects.filter(user=user, anime__bgm_id=bangumi_id).order_by('pk').first()
    if direct is not None:
        return want_contract(direct, bangumi_id, False)
    identity = recommendation_identity(user=user, bangumi_id=bangumi_id)
    if identity['record_id']:
        existing = UserAnimeRecord.objects.filter(user=user, pk=identity['record_id']).first()
        if existing is None:
            raise WantConflict('identity_changed', '手账记录已变化，请重新读取后重试。')
        return want_contract(existing, bangumi_id, False)
    if not identity['identity_service_ready']:
        raise IdentityCatalogUnavailable('身份关联服务尚未部署，暂不能安全新增；已有手账不受影响。')
    if identity['requires_identity_confirmation']:
        if not identity['possible_legacy_record']:
            raise WantConflict('public_identity_conflict', '公共资料已有不同或缺失的作品身份，请联系管理员核对；本次没有新增或合并。')
        raise WantConflict('identity_confirmation_required',
                           '手账可能已有这部作品，请先核对旧记录的原名、季度及版本；本次没有新增或合并。',
                           requires_identity_confirmation=True)
    if not identity['journal_compatible']:
        raise WantConflict('journal_field_conflict', '作品资料超过手账字段长度，无法无损创建。请联系管理员核对；不会截断标题。',
                           field_conflicts=identity['field_conflicts'])
    if UserAnimeRecord.objects.filter(user=user).count() >= settings.USER_ANIME_RECORD_LIMIT:
        raise WantCapacityExceeded('capacity_exceeded', '您的追番记录已达到容量上限，请先整理手账。')
    title = catalog.title_zh or catalog.title_jp or f'未命名番剧 {bangumi_id}'
    tags = canonical_tags(catalog.tags.split(','))
    if catalog.status_type in ('TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画') and catalog.status_type not in tags:
        tags.append(catalog.status_type)
    metadata = {'chinese_title': title, 'japanese_title': catalog.title_jp,
                'airing_period': catalog.season or '未定档', 'description': catalog.synopsis,
                'poster_url': public_cover_url(catalog), 'base_tags': tags}
    try:
        # Savepoint keeps the enclosing transaction usable after unique-key races.
        # NEVER title-merge, update_or_create, truncate, or assign an ID to an old row.
        with transaction.atomic():
            shared, _ = Anime.objects.get_or_create(bgm_id=bangumi_id, defaults=metadata)
    except IntegrityError:
        raise WantConflict('public_identity_conflict', '公共资料存在不同或缺失的作品身份，请联系管理员核对；本次未创建记录。') from None
    if not shared.is_active:
        raise WantConflict('work_unavailable', '该作品在公共库中已下架，暂不能新增。')
    record, created = UserAnimeRecord.objects.get_or_create(user=user, anime=shared, defaults={
        'watch_status': UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
        'personal_score': None, 'review': '', 'watched_eps': 0, 'total_eps': 0,
        'metadata_initialized': True,
        'private_chinese_title': title, 'private_japanese_title': catalog.title_jp,
        'private_airing_period': metadata['airing_period'], 'private_studio': shared.studio,
        'private_episodes': shared.episodes, 'private_description': catalog.synopsis,
        'private_poster_url': metadata['poster_url'], 'private_base_tags': tags,
    })
    if created:
        initialize_record_tag_colors(record=record, user=user)
    return want_contract(record, bangumi_id, created)
