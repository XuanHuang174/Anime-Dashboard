from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Cast, Substr
from django.utils.encoding import force_bytes, force_str
from django.utils.decorators import method_decorator
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from rest_framework import filters, generics, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import Anime, UserAnimeRecord, UserProfile, UserQuickFilter
from .pagination import UserAnimeRecordPagination
from .serializers import (
    AccountDeletionSerializer,
    AnimeSerializer,
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PublicUserProfileSerializer,
    UserRegistrationSerializer,
    UserAnimeRecordAddSerializer,
    UserAnimeRecordSerializer,
    UserProfileSerializer,
    UserQuickFilterSerializer,
)
from .throttles import SensitiveAuthRateThrottle


PRESET_QUICK_FILTER_TAGS = {
    "yuri": ("真百", "轻百", "真百合", "轻百合"),
    "moe": ("萌系", "日常"),
    "comedy": ("搞笑", "校园"),
    "og": ("原创", "治愈"),
}


def _combined_record_tags(record):
    tags = set(record.get("anime__base_tags") or [])
    tags.update(record.get("custom_tags") or [])
    return tags


def _apply_record_filters(queryset, request, filter_owner):
    """Filter the complete record collection before DRF paginates it."""

    search = request.query_params.get("search", "").strip()
    tag = request.query_params.get("tag", "").strip()
    year = request.query_params.get("year", "").strip()
    status_value = request.query_params.get("status", "").strip()
    type_filter = request.query_params.get("type_filter", "all").strip()
    quick_filter_id = request.query_params.get("quick_filter", "").strip()

    if search:
        queryset = queryset.filter(
            Q(anime__chinese_title__icontains=search)
            | Q(anime__japanese_title__icontains=search)
        )
    if year and year != "all" and year.isdigit():
        queryset = queryset.filter(anime__airing_period__startswith=f"{year}-")
    valid_statuses = {choice for choice, _label in UserAnimeRecord.WatchStatus.choices}
    if status_value in valid_statuses:
        queryset = queryset.filter(watch_status=status_value)

    custom_filter = None
    if quick_filter_id.isdigit():
        custom_filter = UserQuickFilter.objects.filter(
            pk=int(quick_filter_id),
            user=filter_owner,
        ).first()

    needs_tag_scan = (
        (tag and tag != "all")
        or (type_filter and type_filter != "all")
        or custom_filter is not None
    )
    if not needs_tag_scan:
        return queryset

    matched_ids = []
    records = queryset.values(
        "pk",
        "custom_tags",
        "anime__base_tags",
        "anime__chinese_title",
    )
    for record in records.iterator():
        tags = _combined_record_tags(record)
        title_upper = (record["anime__chinese_title"] or "").upper()

        if tag and tag != "all" and tag not in tags:
            continue
        if type_filter == "special":
            if not ({"剧场版", "泡面番"} & tags or "OVA" in title_upper):
                continue
        elif type_filter in PRESET_QUICK_FILTER_TAGS:
            if not any(value in tags for value in PRESET_QUICK_FILTER_TAGS[type_filter]):
                continue

        if custom_filter is not None:
            filter_tags = custom_filter.tags or []
            keywords = custom_filter.title_keywords or []
            tag_matches = [value in tags for value in filter_tags]
            keyword_matches = [value.upper() in title_upper for value in keywords]
            matches = tag_matches + keyword_matches
            if custom_filter.match_mode == UserQuickFilter.MatchMode.ALL:
                if not all(matches):
                    continue
            elif not any(matches):
                continue

        matched_ids.append(record["pk"])

    return queryset.filter(pk__in=matched_ids)


def _order_record_queryset(queryset, sort_order):
    """Apply one stable global ordering before slicing into pages."""

    queryset = queryset.annotate(
        _unrated_order=Case(
            When(personal_score__isnull=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        _completed_order=Case(
            When(
                watch_status=UserAnimeRecord.WatchStatus.COMPLETED,
                then=Value(0),
            ),
            default=Value(1),
            output_field=IntegerField(),
        ),
        _airing_year=Cast(
            Substr("anime__airing_period", 1, 4),
            output_field=IntegerField(),
        ),
        _airing_season=Cast(
            Substr("anime__airing_period", 6),
            output_field=IntegerField(),
        ),
    )

    orderings = {
        "date-asc": (
            "_unrated_order", "_airing_year", "_airing_season",
            "_completed_order", "anime__chinese_title", "pk",
        ),
        "score-desc": (
            "_unrated_order", "-personal_score", "_completed_order",
            "-_airing_year", "-_airing_season", "pk",
        ),
        "score-asc": (
            "_unrated_order", "personal_score", "_completed_order",
            "-_airing_year", "-_airing_season", "pk",
        ),
        "updated-desc": (
            "_unrated_order", "-updated_at", "_completed_order", "pk",
        ),
        "updated-asc": (
            "_unrated_order", "updated_at", "_completed_order", "pk",
        ),
    }
    default_ordering = (
        "_unrated_order", "-_airing_year", "-_airing_season",
        "_completed_order", "anime__chinese_title", "pk",
    )
    return queryset.order_by(*orderings.get(sort_order, default_ordering))


def _build_filter_options(queryset):
    """Return full-dataset filter choices instead of choices from one page."""

    tags = set()
    years = set()
    for record in queryset.values(
        "custom_tags",
        "anime__base_tags",
        "anime__airing_period",
    ).iterator():
        tags.update(_combined_record_tags(record))
        period = record["anime__airing_period"] or ""
        year = period[:4]
        if year.isdigit():
            years.add(year)
    return {
        "tags": sorted(tags),
        "years": sorted(years, reverse=True),
    }


def _build_record_stats(queryset):
    """Calculate summary cards from the complete record queryset."""

    completed_count = 0
    scored_count = 0
    score_total = 0
    movie_count = 0
    ova_count = 0
    short_count = 0
    masterpiece_count = 0
    pending_count = 0

    records = queryset.values(
        "personal_score",
        "watch_status",
        "custom_tags",
        "anime__chinese_title",
        "anime__airing_period",
        "anime__base_tags",
    )
    for record in records.iterator():
        score = record["personal_score"]
        title = record["anime__chinese_title"] or ""
        period = record["anime__airing_period"] or ""
        tags = set(record["anime__base_tags"] or [])
        tags.update(record["custom_tags"] or [])

        if record["watch_status"] == UserAnimeRecord.WatchStatus.COMPLETED:
            completed_count += 1
        if score is not None:
            scored_count += 1
            score_total += score
            if score >= 9.5:
                masterpiece_count += 1
        if "剧场版" in title or "剧场版" in tags:
            movie_count += 1
        if "OVA" in title.upper() or "OVA" in tags:
            ova_count += 1
        if "泡面番" in tags:
            short_count += 1
        if score is None or "待定" in period or period == "未定档":
            pending_count += 1

    average_score = (
        f"{score_total / scored_count:.2f}"
        if scored_count
        else "N/A"
    )
    return {
        "completed_count": completed_count,
        "average_score": average_score,
        "movie_count": movie_count,
        "ova_count": ova_count,
        "short_count": short_count,
        "masterpiece_count": masterpiece_count,
        "pending_count": pending_count,
    }


class CustomTokenObtainPairView(TokenObtainPairView):
    """JWT login endpoint with Chinese errors and strict per-IP throttling."""

    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = (AnonRateThrottle, SensitiveAuthRateThrottle)


class UserRegistrationView(APIView):
    """Create an inactive user and send a one-time activation link."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    throttle_classes = (AnonRateThrottle, SensitiveAuthRateThrottle)

    @transaction.atomic
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        query = urlencode({"uid": uid, "token": token})
        activation_url = f"{settings.FRONTEND_URL}/activate?{query}"

        try:
            send_mail(
                subject="激活你的个人番剧汇总账户",
                message=(
                    "欢迎注册XuanHuang的番剧汇总网站。\n\n"
                    "请点击下面的链接完成邮箱验证：\n"
                    f"{activation_url}\n\n"
                    "如果这不是你的操作，请忽略本邮件。"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            # Do not leave an unusable inactive account when delivery fails.
            transaction.set_rollback(True)
            return Response(
                {"detail": "激活邮件发送失败，请稍后重试。"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"detail": "注册成功，请前往邮箱点击激活链接。"},
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    """Activate an account from the uid/token received by the React page."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        user_model = get_user_model()

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = user_model.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            return Response(
                {"detail": "激活链接无效或已损坏。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_active:
            return Response({"detail": "账号已经激活，可以直接登录。"})
        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "激活链接无效或已过期。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = True
        user.save(update_fields=("is_active",))
        return Response({"detail": "账号激活成功，现在可以登录。"})


# Backward-compatible name for code that imported the original activation view.
UserActivationView = VerifyEmailView


class PasswordResetRequestView(APIView):
    """Send a short-lived password-reset link when the email exists."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    throttle_classes = (AnonRateThrottle, SensitiveAuthRateThrottle)

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = get_user_model().objects.filter(email__iexact=email).first()

        # Always return the same response so this public endpoint cannot be used
        # to discover which email addresses have registered accounts.
        response_data = {
            "detail": "如果该邮箱已注册，密码重置邮件将很快发送。",
        }
        if user is None:
            return Response(response_data, status=status.HTTP_200_OK)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        query = urlencode({"uid": uid, "token": token})
        reset_url = f"{settings.FRONTEND_URL}/reset-password?{query}"

        try:
            send_mail(
                subject="重置你的个人番剧汇总账户密码",
                message=(
                    "我们收到了重置你账户密码的请求。\n\n"
                    "请点击下面的链接设置新密码：\n"
                    f"{reset_url}\n\n"
                    "如果这不是你的操作，请忽略本邮件，你的密码不会发生改变。"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            return Response(
                {"detail": "密码重置邮件发送失败，请稍后重试。"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(response_data, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    """Replace a password after validating its uid/token pair."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        user_model = get_user_model()

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = user_model.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            return Response(
                {"detail": "密码重置链接无效或已损坏。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "密码重置链接无效或已过期。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PasswordResetConfirmSerializer(
            data=request.data,
            context={"user": user},
        )
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=("password",))
        return Response({"detail": "密码重置成功，请使用新密码登录。"})


class CurrentUserProfileView(generics.RetrieveUpdateAPIView):
    """Retrieve or partially update only the authenticated user's profile."""

    serializer_class = UserProfileSerializer
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser)
    http_method_names = ("get", "patch", "options")

    def get_object(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile


class ChangePasswordView(APIView):
    """Change the authenticated user's password after checking the old one."""

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=("password",))
        return Response({"detail": "密码修改成功，请使用新密码重新登录。"})


class CurrentUserAccountView(APIView):
    """Permanently delete the authenticated user and all private data."""

    permission_classes = (permissions.IsAuthenticated,)

    @transaction.atomic
    def delete(self, request):
        serializer = AccountDeletionSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        profile = UserProfile.objects.filter(user=user).first()
        avatar_name = profile.avatar.name if profile and profile.avatar else ""
        avatar_storage = profile.avatar.storage if avatar_name else None

        # Account deletion must remove private rows without treating every record
        # as an individual showcase-library deletion.
        from .signals import suppress_showcase_cleanup

        with suppress_showcase_cleanup():
            user.delete()

        if avatar_name and avatar_storage:
            transaction.on_commit(
                lambda: avatar_storage.delete(avatar_name),
                robust=True,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(never_cache, name="dispatch")
class AnimeViewSet(viewsets.ModelViewSet):
    """Public reads; authenticated users may maintain shared anime metadata."""

    queryset = Anime.objects.filter(is_active=True)
    serializer_class = AnimeSerializer
    parser_classes = (parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser)
    filter_backends = (filters.SearchFilter,)
    search_fields = (
        "chinese_title",
        "japanese_title",
        "studio",
        "airing_period",
        "description",
    )

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]


class PublicShowcaseViewSet(viewsets.ReadOnlyModelViewSet):
    """Public homepage data backed by the configured owner's private journal."""

    serializer_class = UserAnimeRecordSerializer
    permission_classes = (permissions.AllowAny,)
    pagination_class = UserAnimeRecordPagination

    def _base_queryset(self):
        return (
            UserAnimeRecord.objects.filter(
                user__username=settings.PUBLIC_SHOWCASE_USERNAME,
                anime__is_active=True,
            )
            .select_related("user", "anime")
        )

    def get_queryset(self):
        queryset = _apply_record_filters(
            self._base_queryset(),
            self.request,
            filter_owner=get_user_model().objects.filter(
                username=settings.PUBLIC_SHOWCASE_USERNAME,
            ).first(),
        )
        return _order_record_queryset(
            queryset,
            self.request.query_params.get("sort", "date-desc"),
        )

    @action(detail=False, methods=("get",), url_path="stats")
    def stats(self, request):
        """Return aggregate showcase statistics independent of pagination."""

        return Response(_build_record_stats(self._base_queryset()))

    @action(detail=False, methods=("get",), url_path="filter-options")
    def filter_options(self, request):
        return Response(_build_filter_options(self._base_queryset()))


class PublicShowcaseProfileView(generics.RetrieveAPIView):
    """Expose the configured showcase owner's display name and avatar only."""

    serializer_class = PublicUserProfileSerializer
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get_object(self):
        return generics.get_object_or_404(
            UserProfile.objects.select_related("user"),
            user__username=settings.PUBLIC_SHOWCASE_USERNAME,
        )


class PublicShowcaseQuickFilterViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only quick filters configured by the public showcase owner."""

    serializer_class = UserQuickFilterSerializer
    permission_classes = (permissions.AllowAny,)

    def get_queryset(self):
        return UserQuickFilter.objects.filter(
            user__username=settings.PUBLIC_SHOWCASE_USERNAME,
        )


class UserAnimeRecordViewSet(viewsets.ModelViewSet):
    """Authenticated user's private anime tracking records."""

    serializer_class = UserAnimeRecordSerializer
    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = UserAnimeRecordPagination

    def _lock_user_and_enforce_quota(self):
        """Serialize creates per user and reject records beyond the quota."""

        get_user_model().objects.select_for_update().get(pk=self.request.user.pk)
        record_count = UserAnimeRecord.objects.filter(
            user=self.request.user,
        ).count()
        if record_count >= settings.USER_ANIME_RECORD_LIMIT:
            raise PermissionDenied("您的追番记录已达到容量上限")

    def _base_queryset(self):
        if not self.request.user.is_authenticated:
            return UserAnimeRecord.objects.none()

        return (
            UserAnimeRecord.objects.filter(user=self.request.user)
            .select_related("user", "anime")
        )

    def get_queryset(self):
        queryset = _apply_record_filters(
            self._base_queryset(),
            self.request,
            filter_owner=self.request.user,
        )
        return _order_record_queryset(
            queryset,
            self.request.query_params.get("sort", "date-desc"),
        )

    @action(detail=False, methods=("get",), url_path="stats")
    def stats(self, request):
        """Return this user's aggregate statistics independent of pagination."""

        return Response(_build_record_stats(self._base_queryset()))

    @action(detail=False, methods=("get",), url_path="filter-options")
    def filter_options(self, request):
        return Response(_build_filter_options(self._base_queryset()))

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create through the standard endpoint without exceeding the quota."""

        self._lock_user_and_enforce_quota()
        return super().create(request, *args, **kwargs)

    @action(
        detail=False,
        methods=("post",),
        url_path="add",
        parser_classes=(parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser),
    )
    @transaction.atomic
    def add(self, request):
        """Link an existing anime or create a new anime and record atomically."""

        self._lock_user_and_enforce_quota()

        input_serializer = UserAnimeRecordAddSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        input_serializer.is_valid(raise_exception=True)
        record = input_serializer.save()
        output_serializer = UserAnimeRecordSerializer(
            record,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=("patch",), url_path="details")
    @transaction.atomic
    def update_details(self, request, pk=None):
        """Atomically update shared anime metadata and this user's record."""

        record = self.get_object()
        anime_payload = request.data.get("anime", {})
        if not isinstance(anime_payload, dict):
            return Response(
                {"anime": "必须是对象。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record_payload = {
            key: value
            for key, value in request.data.items()
            if key != "anime"
        }
        anime_serializer = AnimeSerializer(
            record.anime,
            data=anime_payload,
            partial=True,
            context=self.get_serializer_context(),
        )
        record_serializer = self.get_serializer(
            record,
            data=record_payload,
            partial=True,
        )
        anime_serializer.is_valid(raise_exception=True)
        record_serializer.is_valid(raise_exception=True)
        anime_serializer.save()
        record_serializer.save()
        record.refresh_from_db()
        return Response(self.get_serializer(record).data)


class UserQuickFilterViewSet(viewsets.ModelViewSet):
    """User-isolated CRUD for reusable tag combinations."""

    serializer_class = UserQuickFilterSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        return UserQuickFilter.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
