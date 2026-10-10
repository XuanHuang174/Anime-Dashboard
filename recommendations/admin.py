from django.contrib import admin

from .models import RecommendationAnime


@admin.register(RecommendationAnime)
class RecommendationAnimeAdmin(admin.ModelAdmin):
    list_display = ('bangumi_id', 'title_zh', 'score', 'season', 'status_type', 'updated_at')
    list_filter = ('status_type',)
    search_fields = ('=bangumi_id', 'title_zh', 'title_jp')
    readonly_fields = tuple(field.name for field in RecommendationAnime._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
