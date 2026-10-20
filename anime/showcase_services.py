"""Coordinate public visibility only after an explicit, new showcase-owner add.

This is not an identity resolver or a safety classifier. Callers retain their
existing validation, quota/user locks and recommendation safety checks. Never
call it on a read, an existing-record retry, an edit or an identity binding.
"""
from django.conf import settings
from django.db import transaction

from .models import Anime, UserAnimeRecord


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
    if shared.is_active:
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
