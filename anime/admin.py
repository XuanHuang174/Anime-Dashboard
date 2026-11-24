from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Anime,
    FeaturedQuickFilter,
    FeaturedSubmission,
    UserAnimeRecord,
    UserAnimeTag,
    UserNotification,
    UserProfile,
    UserQuickFilter,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "nickname",
        "public_status",
        "is_public",
        "max_featured_limit",
        "is_push_enabled",
        "last_push_date",
        "showcase_subtitle",
        "avatar",
    )
    list_filter = ("public_status", "is_public", "is_push_enabled")
    search_fields = ("user__username", "user__email", "nickname", "showcase_subtitle")
    autocomplete_fields = ("user",)
    readonly_fields = ("last_push_date", "rating_preferences", "rating_revision")

    def save_model(self, request, obj, form, change):
        if change:
            # Unrelated administrative edits cannot replace account-owned settings.
            if form.changed_data:
                obj.save(update_fields=form.changed_data)
        else:
            super().save_model(request, obj, form, change)


@admin.register(Anime)
class AnimeAdmin(admin.ModelAdmin):
    list_display = (
        "chinese_title",
        "japanese_title",
        "airing_period",
        "studio",
        "episodes",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "airing_period", "studio")
    search_fields = ("chinese_title", "japanese_title", "studio", "description")
    ordering = ("-airing_period", "chinese_title")
    readonly_fields = ("created_at", "updated_at", "hidden_reason")
    list_per_page = 30
    fieldsets = (
        (
            "基础资料",
            {
                "fields": (
                    "chinese_title",
                    "japanese_title",
                    "airing_period",
                    "studio",
                    "episodes",
                    "is_active",
                    "hidden_reason",
                ),
            },
        ),
        (
            "内容信息",
            {
                "fields": (
                    "poster_url",
                    "poster_image",
                    "baike_url",
                    "base_tags",
                    "tag_colors",
                    "description",
                )
            },
        ),
        ("系统信息", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        """Treat the admin library as the configured showcase owner's catalog."""

        return (
            super()
            .get_queryset(request)
            .filter(
                user_records__user__username=settings.PUBLIC_SHOWCASE_USERNAME,
            )
            .distinct()
        )

    def save_model(self, request, obj, form, change):
        # Explicit moderation is not the same as a private-only metadata row.
        if not change or "is_active" in form.changed_data:
            obj.hidden_reason = "" if obj.is_active else Anime.HiddenReason.ADMIN_RETIRED
        super().save_model(request, obj, form, change)


@admin.register(UserAnimeRecord)
class UserAnimeRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "anime",
        "watch_status",
        "personal_score",
        "updated_at",
    )
    list_filter = ("watch_status", "created_at", "updated_at")
    search_fields = (
        "user__username",
        "user__email",
        "anime__chinese_title",
        "anime__japanese_title",
        "review",
    )
    autocomplete_fields = ("user", "anime")
    list_select_related = ("user", "anime")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 30
    fieldsets = (
        ("归属关系", {"fields": ("user", "anime")}),
        ("追番信息", {"fields": (
            "watch_status", "watched_eps", "total_eps", "personal_score", "custom_tags",
        )}),
        ("个人评价", {"fields": ("review",)}),
        ("系统信息", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(UserAnimeTag)
class UserAnimeTagAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "record", "record_owner", "updated_at")
    list_filter = ("color",)
    search_fields = (
        "name",
        "record__user__username",
        "record__user__email",
        "record__anime__chinese_title",
        "record__anime__japanese_title",
    )
    autocomplete_fields = ("record",)
    list_select_related = ("record__user", "record__anime")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="用户", ordering="record__user__username")
    def record_owner(self, obj):
        return obj.record.user


@admin.register(UserQuickFilter)
class UserQuickFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "match_mode", "updated_at")
    list_filter = ("match_mode",)
    search_fields = ("name", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(FeaturedQuickFilter)
class FeaturedQuickFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "match_mode", "updated_at")
    list_filter = ("match_mode",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(FeaturedSubmission)
class FeaturedSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "chinese_title",
        "user",
        "source_anime",
        "airing_period",
        "studio",
        "watch_status",
        "score",
        "poster_link",
        "audit_status",
        "created_at",
    )
    list_filter = ("audit_status", "watch_status", "year", "airing_period")
    search_fields = (
        "chinese_title",
        "japanese_title",
        "user__username",
        "user__email",
        "review",
    )
    autocomplete_fields = ("user", "source_anime", "source_record")
    readonly_fields = ("created_at", "updated_at", "rating_preferences", "bangumi_url")
    list_select_related = ("user", "source_anime")
    list_per_page = 30
    show_full_result_count = False
    fieldsets = (
        ('来源与审核', {'fields': ('user', 'source_anime', 'source_record', 'audit_status')}),
        ('作品快照', {'fields': ('chinese_title', 'japanese_title', 'year', 'airing_period', 'studio', 'episodes', 'baike_url', 'bangumi_url', 'poster', 'poster_url')}),
        ('观看与评价', {'fields': ('watch_status', 'score', 'review')}),
        ('标签与简介', {'fields': ('tags', 'tag_colors', 'description'), 'classes': ('collapse',)}),
        ('评分配置与系统信息', {'fields': ('rating_preferences', 'created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def get_readonly_fields(self, request, obj=None):
        # Existing snapshot provenance must not enumerate all private records or
        # be accidentally reassigned while moderating a submission.
        return self.readonly_fields + (('user', 'source_anime', 'source_record') if obj else ())

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'source_anime', 'source_record__user', 'source_record__anime')

    @admin.display(description='海报')
    def poster_link(self, obj):
        url = obj.poster.url if obj.poster else obj.poster_url
        try:
            parsed = urlsplit(url)
        except ValueError:
            return '未设置'
        if not url or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            return '未设置'
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">打开海报 ↗</a>', url)


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "message_preview", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("user__username", "user__email", "message")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at",)
    list_select_related = ("user",)

    @admin.display(description="消息内容")
    def message_preview(self, obj):
        return obj.message[:50]
