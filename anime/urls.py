from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AnimeViewSet,
    ChangePasswordView,
    CurrentUserAccountView,
    CurrentUserProfileView,
    FeaturedSubmissionViewSet,
    PasswordResetConfirmView,
    PasswordResetRequestView,
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

app_name = "anime_api"
urlpatterns = [
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
    path("export/", UserJournalExportView.as_view(), name="journal-export"),
    path("import/", UserJournalImportView.as_view(), name="journal-import"),
    path("showcase-profile/", PublicShowcaseProfileView.as_view(), name="showcase-profile"),
    *router.urls,
]
