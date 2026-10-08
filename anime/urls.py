from django.urls import path
from rest_framework.routers import DefaultRouter
from .resource_search import AnimeResourceSearchView

from .views import (
    AdminFeaturedSubmissionViewSet,
    AdminPublicJournalDetailView,
    AdminPublicJournalFilterOptionsView,
    AdminPublicJournalProfileViewSet,
    AdminPublicJournalStatsView,
    AdminSessionView,
    AnimeViewSet,
    ChangePasswordView,
    BangumiCollectionBatchImportView,
    BangumiCollectionSummaryView,
    CurrentUserAccountView,
    CurrentUserProfileView,
    DailyPushCronView,
    ExternalAnimeAutofillView,
    ExternalAnimeSearchView,
    FeaturedSubmissionViewSet,
    FeaturedQuickFilterViewSet,
    ImageProxyView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PublicFeaturedSubmissionViewSet,
    PublicJournalAccessView,
    PublicJournalDetailView,
    PublicJournalFilterOptionsView,
    PublicJournalLobbyView,
    PublicJournalStatsView,
    PublicShowcaseProfileView,
    PublicShowcaseQuickFilterViewSet,
    PublicShowcaseViewSet,
    UserAnimeRecordViewSet,
    UserJournalExportView,
    UserJournalImportView,
    UserNotificationViewSet,
    UserQuickFilterViewSet,
    UserRegistrationView,
    VerifyEmailView,
)


router = DefaultRouter()
router.register("animes", AnimeViewSet, basename="animes")
router.register("anime", AnimeViewSet, basename="anime")
router.register("records", UserAnimeRecordViewSet, basename="user-anime-record")
router.register("showcase", PublicShowcaseViewSet, basename="showcase")
router.register(
    "showcase-filters",
    PublicShowcaseQuickFilterViewSet,
    basename="showcase-filter",
)
router.register("quick-filters", UserQuickFilterViewSet, basename="quick-filter")
router.register(
    "featured-submissions",
    FeaturedSubmissionViewSet,
    basename="featured-submission",
)
router.register("notifications", UserNotificationViewSet, basename="notification")
router.register("featured", PublicFeaturedSubmissionViewSet, basename="featured")
router.register(
    "featured-filters",
    FeaturedQuickFilterViewSet,
    basename="featured-filter",
)
router.register(
    "admin/featured-submissions",
    AdminFeaturedSubmissionViewSet,
    basename="admin-featured-submission",
)
router.register(
    "admin/public-journals",
    AdminPublicJournalProfileViewSet,
    basename="admin-public-journal",
)

app_name = "anime_api"
urlpatterns = [
    path("utils/anime-search/", AnimeResourceSearchView.as_view(), name="anime-resource-search"),
    path("utils/image-proxy/", ImageProxyView.as_view(), name="image-proxy"),
    path(
        "cron/trigger-daily-push/",
        DailyPushCronView.as_view(),
        name="cron-trigger-daily-push",
    ),
    path("admin/session/", AdminSessionView.as_view(), name="admin-session"),
    path(
        "admin/public-journals/<str:username>/records/",
        AdminPublicJournalDetailView.as_view(),
        name="admin-public-journal-detail",
    ),
    path(
        "admin/public-journals/<str:username>/stats/",
        AdminPublicJournalStatsView.as_view(),
        name="admin-public-journal-stats",
    ),
    path(
        "admin/public-journals/<str:username>/filter-options/",
        AdminPublicJournalFilterOptionsView.as_view(),
        name="admin-public-journal-filter-options",
    ),
    path("auth/register/", UserRegistrationView.as_view(), name="register"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/activate/", VerifyEmailView.as_view(), name="activate"),
    path(
        "auth/password-reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("user/profile/", CurrentUserProfileView.as_view(), name="user-profile"),
    path(
        "user/change-password/",
        ChangePasswordView.as_view(),
        name="change-password",
    ),
    path("user/account/", CurrentUserAccountView.as_view(), name="user-account"),
    path(
        "external/search/",
        ExternalAnimeSearchView.as_view(),
        name="external-anime-search",
    ),
    path(
        "external/autofill/",
        ExternalAnimeAutofillView.as_view(),
        name="external-anime-autofill",
    ),
    path(
        "external/bangumi-import/summary/",
        BangumiCollectionSummaryView.as_view(),
        name="bangumi-collection-summary",
    ),
    path(
        "external/bangumi-import/batch/",
        BangumiCollectionBatchImportView.as_view(),
        name="bangumi-collection-batch",
    ),
    path(
        "public-journal/status/",
        PublicJournalAccessView.as_view(),
        name="public-journal-status",
    ),
    path(
        "public-journal/users/",
        PublicJournalLobbyView.as_view(),
        name="public-journal-lobby",
    ),
    path(
        "public-journal/users/<str:username>/records/",
        PublicJournalDetailView.as_view(),
        name="public-journal-detail",
    ),
    path(
        "public-journal/users/<str:username>/stats/",
        PublicJournalStatsView.as_view(),
        name="public-journal-stats",
    ),
    path(
        "public-journal/users/<str:username>/filter-options/",
        PublicJournalFilterOptionsView.as_view(),
        name="public-journal-filter-options",
    ),
    path("export/", UserJournalExportView.as_view(), name="journal-export"),
    path("import/", UserJournalImportView.as_view(), name="journal-import"),
    path("showcase-profile/", PublicShowcaseProfileView.as_view(), name="showcase-profile"),
    *router.urls,
]
