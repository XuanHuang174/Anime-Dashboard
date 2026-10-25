from django.contrib import admin

from .models import RecommendationAnime
from . import eligibility_admin  # noqa: F401 - register the read-only controlled admin
from . import content_review_admin  # noqa: F401 - single-work controlled review only
from . import series_admin  # noqa: F401 - protected auditable relation corrections
from .models import RecommendationServingControlEvent


@admin.register(RecommendationServingControlEvent)
class RecommendationServingControlEventAdmin(admin.ModelAdmin):
    """Append-only switch receipts. Mutations exclusively use the locked service."""
    actions = None
    list_display = ('created_at', 'actor', 'enabled', 'version', 'operation_id')
    readonly_fields = tuple(field.name for field in RecommendationServingControlEvent._meta.fields)

    def has_module_permission(self, request):
        from .eligibility import can_manage
        return can_manage(request.user)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
