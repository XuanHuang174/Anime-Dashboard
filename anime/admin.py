from django.contrib import admin

from .models import Anime, UserAnimeRecord, UserProfile, UserQuickFilter


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "nickname", "showcase_subtitle", "avatar")
    search_fields = ("user__username", "user__email", "nickname", "showcase_subtitle")
    autocomplete_fields = ("user",)


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
    readonly_fields = ("created_at", "updated_at")
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
        ("追番信息", {"fields": ("watch_status", "personal_score", "custom_tags")}),
        ("个人评价", {"fields": ("review",)}),
        ("系统信息", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(UserQuickFilter)
class UserQuickFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "match_mode", "updated_at")
    list_filter = ("match_mode",)
    search_fields = ("name", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
