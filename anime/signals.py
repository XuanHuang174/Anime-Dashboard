from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from .showcase_services import withdraw_showcase_anime

from .models import (
    FeaturedSubmission,
    UserAnimeRecord,
    UserNotification,
    UserProfile,
    UserQuickFilter,
)


DEFAULT_QUICK_FILTERS = (
    {"name": "百合 (真/轻)", "tags": ["真百", "轻百"]},
    {"name": "萌系 & 日常", "tags": ["萌系", "日常"]},
    {"name": "搞笑 & 校园", "tags": ["搞笑", "校园"]},
    {"name": "原创 & 治愈", "tags": ["原创", "治愈"]},
    {
        "name": "剧场版 & OVA & 泡面番",
        "tags": ["剧场版", "泡面番"],
        "title_keywords": ["OVA"],
    },
)

_suppress_showcase_cleanup = ContextVar("suppress_showcase_cleanup", default=False)


@contextmanager
def suppress_showcase_cleanup():
    """Preserve shared Anime rows while an entire user account is deleted."""

    token = _suppress_showcase_cleanup.set(True)
    try:
        yield
    finally:
        _suppress_showcase_cleanup.reset(token)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def initialize_new_user(sender, instance, created, raw=False, **kwargs):
    # Fixtures already contain the related profile and quick filters. Creating
    # defaults while loaddata is restoring raw rows causes duplicate unique
    # constraints and makes cross-database migrations impossible.
    if raw or not created:
        return
    UserProfile.objects.get_or_create(user=instance)
    UserQuickFilter.objects.bulk_create(
        [UserQuickFilter(user=instance, **item) for item in DEFAULT_QUICK_FILTERS],
        ignore_conflicts=True,
    )


@receiver(pre_save, sender=FeaturedSubmission)
def remember_featured_audit_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_audit_status = None
        return
    instance._previous_audit_status = FeaturedSubmission.objects.filter(
        pk=instance.pk,
    ).values_list("audit_status", flat=True).first()


@receiver(post_save, sender=FeaturedSubmission)
def notify_featured_audit_result(sender, instance, created, **kwargs):
    if created or getattr(instance, "_previous_audit_status", None) == instance.audit_status:
        return

    if instance.audit_status == FeaturedSubmission.AuditStatus.APPROVED:
        message = f"恭喜，你对{instance.chinese_title}的番剧点评已被精选专栏收录。"
    elif instance.audit_status == FeaturedSubmission.AuditStatus.REJECTED:
        message = f"抱歉，你对{instance.chinese_title}的番剧点评未能入选精选专栏。"
    else:
        return

    UserNotification.objects.create(user=instance.user, message=message)


@receiver(post_delete, sender=UserAnimeRecord)
def clean_public_anime_when_showcase_owner_deletes_record(sender, instance, **kwargs):
    """Delete an unreferenced title removed by the public showcase owner."""

    if _suppress_showcase_cleanup.get():
        return
    if instance.user.username != settings.PUBLIC_SHOWCASE_USERNAME:
        return

    withdraw_showcase_anime(instance.anime_id)
