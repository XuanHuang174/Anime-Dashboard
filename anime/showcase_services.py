"""Automatic visibility for new owner adds; separate explicit per-work approval.

This is not an identity resolver or a safety classifier. Callers retain their
existing validation, quota/user locks and recommendation safety checks. Never
call automatic publication on a read, existing-record retry, edit or binding.
Historical unknown visibility needs the separate signed confirmation endpoint.
"""
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from .models import Anime, UserAnimeRecord
from .showcase_policy import publication_content, UNSET

PUBLICATION_CONTRACT = "showcase.publication.v1"
PUBLICATION_SALT = "showcase.explicit-publication.v1"


class PublicationConflict(APIException):
    status_code = 409
    default_detail = "作品资料或公开状态已变化，请重新读取待公开列表后确认。"


def _publication_state(record, content=None):
    shared = record.anime
    return {"owner": record.user_id, "record": record.pk, "anime": shared.pk,
            "bangumi": shared.bgm_id, "shared_updated": shared.updated_at.isoformat(),
            "record_updated": record.updated_at.isoformat(), "hidden_reason": shared.hidden_reason,
            "active": shared.is_active, "content": content}


def publication_candidate(record, *, catalog=UNSET, identity_conflict=None):
    """Owner-only minimal read contract; never reads scores/reviews/tags."""
    shared = record.anime
    allowed = shared.hidden_reason in (Anime.HiddenReason.PRIVATE_ONLY,
        Anime.HiddenReason.SHOWCASE_REMOVED, Anime.HiddenReason.LEGACY_UNKNOWN)
    content = publication_content(shared, catalog)
    code, detail = 'showcase_confirmation_required', '确认后，本账号这部作品的手账信息会直接进入公共展示。'
    if not allowed:
        code = 'showcase_admin_retired' if shared.hidden_reason == Anime.HiddenReason.ADMIN_RETIRED else 'showcase_hidden_reason_unknown'
        detail = '作品已被管理员下架或隐藏原因无法识别，不能通过此入口恢复。'
    elif not content['allowed']:
        allowed, code, detail = False, content['code'], content['detail']
    else:
        if identity_conflict is None:
            from recommendations.models import RecommendationRecordIdentity
            identity_conflict = RecommendationRecordIdentity.objects.filter(record=record, active=True).exclude(
                catalog_anime__bangumi_id=shared.bgm_id).exists()
        if identity_conflict:
            allowed, code, detail = False, 'showcase_identity_conflict', '作品存在不同 Bangumi ID 的身份关联，请先核对身份。'
    return {"record_id": record.pk, "anime_id": shared.pk, "bangumi_id": shared.bgm_id,
            "title": record.private_chinese_title if record.metadata_initialized else shared.chinese_title,
            "original": record.private_japanese_title if record.metadata_initialized else shared.japanese_title,
            "season": record.private_airing_period if record.metadata_initialized else shared.airing_period,
            "requires_confirmation": allowed,
            "code": code, "content_rating": 'adult' if content['signature'] else 'non_adult' if content['allowed'] else 'unavailable',
            "detail": detail,
            "ticket": signing.dumps(_publication_state(record, content['signature']), salt=PUBLICATION_SALT) if allowed else None}


def publication_candidates(records):
    """Bounded bulk preflight for a page; never reads journal scores/reviews."""
    from recommendations.models import RecommendationAnime, RecommendationRecordIdentity
    records = list(records)
    catalogs = {a.bangumi_id: a for a in RecommendationAnime.objects.select_related('content_review').filter(
        bangumi_id__in=[r.anime.bgm_id for r in records if r.anime.bgm_id is not None])}
    linked = {}
    for record_id, identity in RecommendationRecordIdentity.objects.filter(
            record_id__in=[r.pk for r in records], active=True).values_list('record_id', 'catalog_anime__bangumi_id'):
        linked[record_id] = identity
    return [publication_candidate(record, catalog=catalogs.get(record.anime.bgm_id),
        identity_conflict=record.pk in linked and linked[record.pk] != record.anime.bgm_id) for record in records]


@transaction.atomic
def confirm_showcase_publication(*, user, record_id, ticket):
    """Explicit per-work approval, separate from idempotent journal creation."""
    from django.contrib.auth import get_user_model
    from recommendations.models import RecommendationAnime, RecommendationRecordIdentity
    if not user.is_authenticated or not get_user_model().objects.select_for_update().filter(
        pk=user.pk, username=settings.PUBLIC_SHOWCASE_USERNAME, is_active=True).exists():
        raise PermissionDenied("只有公共番剧库主理人可以确认公开。")
    try:
        proof = signing.loads(ticket, salt=PUBLICATION_SALT, max_age=600)
    except (signing.BadSignature, TypeError, ValueError):
        raise PublicationConflict() from None
    if not isinstance(proof, dict) or proof.get("owner") != user.pk or proof.get("record") != record_id:
        raise PublicationConflict()
    record = UserAnimeRecord.objects.select_for_update().filter(pk=record_id, user=user).only(
        "pk", "user_id", "anime_id", "updated_at").first()
    if record is None:
        raise NotFound("手账记录不存在。")
    if proof.get("anime") != record.anime_id:
        raise PublicationConflict()
    # Like explicit identity binding, lock the existing record after the owner;
    # catalog then shared. The owner lock serializes this with want/import.
    target = None
    if proof.get("bangumi") is not None:
        target = RecommendationAnime.objects.select_for_update().filter(bangumi_id=proof["bangumi"]).first()
    shared = Anime.objects.select_for_update().get(pk=record.anime_id)
    record.anime = shared
    if shared.bgm_id != proof.get("bangumi"):
        raise PublicationConflict()
    content = publication_content(shared, target)
    if not content['allowed']:
        raise PermissionDenied({'code': content['code'], 'detail': content['detail']})
    if proof.get('content') != content['signature']:
        raise PublicationConflict('作品内容审核已变化，请重新读取后确认。')
    if RecommendationRecordIdentity.objects.filter(record=record, active=True).exclude(
        catalog_anime__bangumi_id=shared.bgm_id).exists():
        raise PublicationConflict("作品身份存在待核对的关联，不能公开。")
    if shared.is_active:
        # Retry after a committed success is a read-only acknowledgement.
        return {"contract_version": PUBLICATION_CONTRACT, "record_id": record.pk,
                "anime_id": shared.pk, "status": "already_public"}
    if shared.hidden_reason not in (Anime.HiddenReason.PRIVATE_ONLY,
        Anime.HiddenReason.SHOWCASE_REMOVED, Anime.HiddenReason.LEGACY_UNKNOWN):
        raise PermissionDenied("管理员下架或未识别的隐藏原因不可通过此入口恢复。")
    if proof != _publication_state(record, content['signature']):
        raise PublicationConflict()
    shared.is_active = True
    shared.hidden_reason = ""
    shared.updated_at = timezone.now()
    shared.save(update_fields=("is_active", "hidden_reason", "updated_at"))
    return {"contract_version": PUBLICATION_CONTRACT, "record_id": record.pk,
            "anime_id": shared.pk, "status": "published"}


def imported_visibility_defaults(user):
    """New recommendation/Bangumi rows are private unless created by XHcode."""
    public = user.username == settings.PUBLIC_SHOWCASE_USERNAME
    return {
        "is_active": public,
        "hidden_reason": "" if public else Anime.HiddenReason.PRIVATE_ONLY,
    }


@transaction.atomic
def publish_new_showcase_record(*, record, created):
    if not created or record.user.username != settings.PUBLIC_SHOWCASE_USERNAME:
        return None
    # Verify persisted ownership, not a nickname/client-supplied user identifier.
    if not UserAnimeRecord.objects.filter(
        pk=record.pk, anime_id=record.anime_id,
        user__username=settings.PUBLIC_SHOWCASE_USERNAME,
    ).exists():
        return None
    shared = Anime.objects.select_for_update().get(pk=record.anime_id)
    # Ordinary new manual works need not exist in the recommendation directory.
    # Known restrictions and any existing classification still take precedence.
    content = publication_content(shared, allow_missing=True)
    if not content['allowed']:
        if shared.is_active:
            shared.is_active = False
            shared.hidden_reason = Anime.HiddenReason.PRIVATE_ONLY
            shared.save(update_fields=('is_active', 'hidden_reason', 'updated_at'))
        result = {'status': 'review_required', 'code': content['code'], 'detail': content['detail']}
    elif shared.is_active:
        result = {"status": "already_public", "detail": "作品已在公共库中可用。"}
    elif shared.hidden_reason in (
        Anime.HiddenReason.PRIVATE_ONLY, Anime.HiddenReason.SHOWCASE_REMOVED,
    ):
        shared.is_active = True
        shared.hidden_reason = ""
        shared.save(update_fields=("is_active", "hidden_reason", "updated_at"))
        result = {"status": "published", "detail": "作品已进入公共番剧库。"}
    else:
        result = {
            "status": "review_required",
            "code": "showcase_visibility_review_required",
            "detail": "已加入私人手账；公共资料已下架或隐藏原因待审核，暂未公开。",
        }
    # Additive notice on this owner's CREATE response only. Never serialize a
    # hidden reason or another user's private data into public reads.
    record._showcase_publication = result
    record.anime.is_active = shared.is_active
    record.anime.hidden_reason = shared.hidden_reason
    return result


@transaction.atomic
def withdraw_showcase_anime(anime_id):
    shared = Anime.objects.select_for_update().filter(pk=anime_id).first()
    if shared is None:
        return
    # A concurrent re-add must not be hidden by an older deletion callback.
    if shared.user_records.filter(user__username=settings.PUBLIC_SHOWCASE_USERNAME).exists():
        return
    if shared.user_records.exists():
        if shared.is_active:
            shared.is_active = False
            shared.hidden_reason = Anime.HiddenReason.SHOWCASE_REMOVED
            shared.save(update_fields=("is_active", "hidden_reason", "updated_at"))
        # Never relabel an admin ban/unknown hidden row as eligible for restore.
        return
    shared.delete()
