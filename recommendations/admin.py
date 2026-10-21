from django.contrib import admin

from .models import RecommendationAnime
from . import eligibility_admin  # noqa: F401 - register the read-only controlled admin


@admin.register(RecommendationAnime)
class RecommendationAnimeAdmin(admin.ModelAdmin):
    list_display = ('bangumi_id', 'title_zh', 'score', 'season', 'status_type', 'nsfw', 'updated_at')
    list_filter = ('status_type', 'nsfw')
    search_fields = ('=bangumi_id', 'title_zh', 'title_jp')
    readonly_fields = tuple(field.name for field in RecommendationAnime._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
