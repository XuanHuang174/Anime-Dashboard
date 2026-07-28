import json
import logging
import re
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.files.base import ContentFile
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Cast, Substr
from django.http import HttpResponse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.decorators import method_decorator
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from rest_framework import filters, generics, mixins, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import (
    Anime,
    FeaturedSubmission,
    UserAnimeRecord,
    UserAnimeTag,
    UserNotification,
    UserProfile,
    UserQuickFilter,
)
from .pagination import UserAnimeRecordPagination
from .serializers import (
    AccountDeletionSerializer,
    AdminFeaturedSubmissionSerializer,
    AnimeSerializer,
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    FeaturedSubmissionCreateSerializer,
    FeaturedSubmissionSerializer,
    JsonStringMapField,
    JournalRecordImportSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PublicFeaturedSubmissionSerializer,
    PublicUserProfileSerializer,
    UserRegistrationSerializer,
    UserAnimeRecordAddSerializer,
    UserAnimeRecordSerializer,
    UserNotificationSerializer,
    UserProfileSerializer,
    UserQuickFilterSerializer,
)
from .tag_services import (
    get_record_tag_colors,
    initialize_record_tag_colors,
    replace_record_tag_colors,
)
from .throttles import SensitiveAuthRateThrottle


logger = logging.getLogger(__name__)


class IsSuperUser(permissions.BasePermission):
    """Allow access only to authenticated Django superusers."""

    message = "仅超级管理员可以访问该接口。"

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_superuser
        )


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
    for record in records.iterator(chunk_size=2000):
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
    ).iterator(chunk_size=2000):
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
    for record in records.iterator(chunk_size=2000):
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


class UserJournalExportView(APIView):
    """Download the authenticated user's journal without internal identifiers."""

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        records = (
            UserAnimeRecord.objects.filter(user=request.user)
            .select_related("anime")
            .prefetch_related("tag_preferences")
            .order_by("pk")
        )
        exported_records = []
        for record in records:
            anime = record.anime
            private_tag_colors = get_record_tag_colors(record)
            poster_url = anime.poster_url
            if not poster_url and anime.poster_image:
                poster_url = request.build_absolute_uri(anime.poster_image.url)

            exported_records.append(
                {
                    "anime": {
                        "chinese_title": anime.chinese_title,
                        "japanese_title": anime.japanese_title,
                        "airing_period": anime.airing_period,
                        "studio": anime.studio,
                        "episodes": anime.episodes,
                        "description": anime.description,
                        "poster_url": poster_url,
                        "baike_url": anime.baike_url,
                        "base_tags": anime.base_tags,
                        "tag_colors": private_tag_colors,
                    },
                    "personal_score": (
                        str(record.personal_score)
                        if record.personal_score is not None
                        else None
                    ),
                    "watch_status": record.watch_status,
                    "review": record.review,
                    "custom_tags": record.custom_tags,
                }
            )

        payload = {
            "format": "anime-journal",
            "version": 2,
            "exported_at": timezone.now().isoformat(),
            "records": exported_records,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        filename = timezone.localdate().strftime("anime-journal-%Y%m%d.json")
        response = HttpResponse(
            content,
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class UserJournalImportView(APIView):
    """Atomically replace the authenticated user's journal from portable JSON."""

    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (
        parsers.JSONParser,
        parsers.FormParser,
        parsers.MultiPartParser,
    )
    max_file_size = 5 * 1024 * 1024

    def _read_payload(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is not None:
            if uploaded_file.size > self.max_file_size:
                raise ValueError("JSON 文件不能超过 5MB。")
            try:
                return json.loads(uploaded_file.read().decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("上传的文件不是有效的 UTF-8 JSON 文件。") from error

        payload = request.data
        if isinstance(payload, dict) and isinstance(payload.get("data"), str):
            try:
                return json.loads(payload["data"])
            except json.JSONDecodeError as error:
                raise ValueError("提交的数据不是有效的 JSON。") from error
        return payload

    def post(self, request):
        try:
            payload = self._read_payload(request)
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if isinstance(payload, list):
            record_data = payload
        elif isinstance(payload, dict):
            record_data = payload.get("records")
        else:
            record_data = None

        if not isinstance(record_data, list):
            return Response(
                {"detail": "JSON 顶层必须是记录数组，或包含 records 数组。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(record_data) > settings.USER_ANIME_RECORD_LIMIT:
            return Response(
                {"detail": "您的追番记录已达到容量上限"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = JournalRecordImportSerializer(data=record_data, many=True)
        serializer.is_valid(raise_exception=True)
        validated_records = serializer.validated_data

        anime_keys = [
            (
                item["anime"]["chinese_title"],
                item["anime"]["japanese_title"],
                item["anime"]["airing_period"],
            )
            for item in validated_records
        ]
        if len(anime_keys) != len(set(anime_keys)):
            return Response(
                {"detail": "导入文件中包含重复的番剧记录。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                replacement_records = []
                private_colors_by_anime_id = {}
                for item in validated_records:
                    anime_data = dict(item["anime"])
                    private_tag_colors = anime_data.pop("tag_colors", {})
                    lookup = {
                        "chinese_title": anime_data.pop("chinese_title"),
                        "japanese_title": anime_data.pop("japanese_title"),
                        "airing_period": anime_data.pop("airing_period"),
                    }
                    anime, _created = Anime.objects.get_or_create(
                        **lookup,
                        defaults=anime_data,
                    )
                    replacement_records.append(
                        UserAnimeRecord(
                            user=request.user,
                            anime=anime,
                            personal_score=item["personal_score"],
                            watch_status=item["watch_status"],
                            review=item["review"],
                            custom_tags=item["custom_tags"],
                        )
                    )
                    private_colors_by_anime_id[anime.pk] = private_tag_colors

                # Replacing a showcase owner's private journal must not make the
                # intermediate empty state delete shared Anime rows.
                from .signals import suppress_showcase_cleanup

                with suppress_showcase_cleanup():
                    # Delete the newly introduced child rows explicitly before
                    # replacing records. This keeps SQLite's deferred foreign-key
                    # checks valid even when a long-running development server
                    # loaded the old relationship graph before the migration.
                    UserAnimeTag.objects.filter(
                        record__user=request.user,
                    ).delete()
                    UserAnimeRecord.objects.filter(user=request.user).delete()
                UserAnimeRecord.objects.bulk_create(replacement_records)
                created_records = UserAnimeRecord.objects.filter(
                    user=request.user,
                    anime_id__in=private_colors_by_anime_id,
                )
                UserAnimeTag.objects.bulk_create(
                    [
                        UserAnimeTag(record=record, name=name, color=color)
                        for record in created_records
                        for name, color in private_colors_by_anime_id[
                            record.anime_id
                        ].items()
                    ]
                )
        except Exception:
            logger.exception(
                "Journal import failed for user %s; transaction rolled back.",
                request.user.pk,
            )
            return Response(
                {"detail": "导入失败，原有手账数据已完整保留。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "手账数据导入成功。",
                "imported_count": len(replacement_records),
            },
            status=status.HTTP_200_OK,
        )


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
            .prefetch_related("tag_preferences")
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
            .prefetch_related("tag_preferences")
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
        record = serializer.save(user=self.request.user)
        initialize_record_tag_colors(
            record=record,
            user=self.request.user,
        )

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

    @action(
        detail=True,
        methods=("patch",),
        url_path="details",
        parser_classes=(parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser),
    )
    @transaction.atomic
    def update_details(self, request, pk=None):
        """Atomically update shared anime metadata and this user's record."""

        record = self.get_object()
        anime_payload = request.data.get("anime", {})
        if isinstance(anime_payload, str):
            try:
                anime_payload = json.loads(anime_payload)
            except json.JSONDecodeError:
                anime_payload = None
        if not isinstance(anime_payload, dict):
            return Response(
                {"anime": "必须是对象。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        anime_payload = dict(anime_payload)
        poster_image = request.FILES.get("poster_image")
        if poster_image is not None:
            anime_payload["poster_image"] = poster_image
        elif anime_payload.get("poster_url"):
            # URL and uploaded files are mutually exclusive display sources.
            # Explicitly saving a URL switches away from any previous upload.
            anime_payload["poster_image"] = None
        raw_tag_colors = anime_payload.pop("tag_colors", None)
        private_tag_colors = None
        if raw_tag_colors is not None:
            private_tag_colors = JsonStringMapField().run_validation(raw_tag_colors)

        record_payload = request.data.get("record")
        if record_payload is not None:
            if isinstance(record_payload, str):
                try:
                    record_payload = json.loads(record_payload)
                except json.JSONDecodeError:
                    record_payload = None
            if not isinstance(record_payload, dict):
                return Response(
                    {"record": "必须是对象。"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            record_payload = {
                key: value
                for key, value in request.data.items()
                if key not in {"anime", "poster_image"}
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
        if private_tag_colors is not None:
            replace_record_tag_colors(
                record=record,
                user=request.user,
                colors=private_tag_colors,
            )
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


class FeaturedSubmissionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """User-isolated reads plus the guarded snapshot submission workflow."""

    serializer_class = FeaturedSubmissionSerializer
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (parsers.JSONParser, parsers.FormParser, parsers.MultiPartParser)

    def get_queryset(self):
        return FeaturedSubmission.objects.filter(
            user=self.request.user,
        ).select_related("user")

    @action(detail=False, methods=("get",), url_path="state")
    def state(self, request):
        profile, _created = UserProfile.objects.get_or_create(user=request.user)
        actionable_statuses = (
            FeaturedSubmission.AuditStatus.PENDING,
            FeaturedSubmission.AuditStatus.APPROVED,
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED,
        )
        submissions = self.get_queryset().filter(
            audit_status__in=actionable_statuses,
        )
        approved_count = submissions.filter(
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        ).count()
        return Response(
            {
                "max_featured_limit": profile.max_featured_limit,
                "approved_count": approved_count,
                "is_at_limit": approved_count >= profile.max_featured_limit,
                "submissions": self.get_serializer(submissions, many=True).data,
            }
        )

    @action(
        detail=False,
        methods=("post",),
        url_path="submit",
        parser_classes=(parsers.JSONParser,),
    )
    @transaction.atomic
    def submit(self, request):
        input_serializer = FeaturedSubmissionCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        profile, _created = UserProfile.objects.select_for_update().get_or_create(
            user=request.user,
        )
        approved_count = FeaturedSubmission.objects.filter(
            user=request.user,
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        ).count()
        if approved_count >= profile.max_featured_limit:
            return Response(
                {"detail": "您已达到精选专栏收录数量上限。"},
                status=status.HTTP_403_FORBIDDEN,
            )

        active_audit_statuses = (
            FeaturedSubmission.AuditStatus.PENDING,
            FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED,
        )
        if FeaturedSubmission.objects.filter(
            user=request.user,
            audit_status__in=active_audit_statuses,
        ).exists():
            return Response(
                {"detail": "您已有正在处理的精选专栏申请，请等待处理完成。"},
                status=status.HTTP_409_CONFLICT,
            )

        record = generics.get_object_or_404(
            UserAnimeRecord.objects.select_related("anime"),
            user=request.user,
            anime_id=input_serializer.validated_data["anime_id"],
        )
        anime = record.anime
        year_match = re.search(r"(?:19|20|21)\d{2}", anime.airing_period or "")
        snapshot = FeaturedSubmission(
            user=request.user,
            source_anime=anime,
            chinese_title=anime.chinese_title,
            japanese_title=anime.japanese_title,
            year=int(year_match.group()) if year_match else None,
            watch_status=record.watch_status,
            score=record.personal_score,
            review=record.review,
            poster_url=anime.poster_url,
            tags=list(
                dict.fromkeys(
                    [*(anime.base_tags or []), *(record.custom_tags or [])]
                )
            ),
            audit_status=FeaturedSubmission.AuditStatus.PENDING,
        )
        if anime.poster_image:
            anime.poster_image.open("rb")
            try:
                suffix = Path(anime.poster_image.name).suffix.lower()
                snapshot.poster.save(
                    f"{uuid4().hex}{suffix}",
                    ContentFile(anime.poster_image.read()),
                    save=False,
                )
            finally:
                anime.poster_image.close()
        snapshot.save()

        output_serializer = self.get_serializer(
            snapshot,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=("post",),
        url_path="request-removal",
    )
    @transaction.atomic
    def request_removal(self, request, pk=None):
        submission = generics.get_object_or_404(
            FeaturedSubmission.objects.select_for_update(),
            pk=pk,
            user=request.user,
        )
        if submission.audit_status != FeaturedSubmission.AuditStatus.APPROVED:
            return Response(
                {"detail": "只有已收录的精选专栏可以申请撤下。"},
                status=status.HTTP_409_CONFLICT,
            )

        submission.audit_status = FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED
        submission.save(update_fields=("audit_status", "updated_at"))
        return Response(self.get_serializer(submission).data)


class UserNotificationViewSet(viewsets.ModelViewSet):
    """List and mark the current user's notifications as read."""

    serializer_class = UserNotificationSerializer
    permission_classes = (permissions.IsAuthenticated,)
    http_method_names = ("get", "patch", "head", "options")

    def get_queryset(self):
        return UserNotification.objects.filter(user=self.request.user)

    @action(detail=False, methods=("get",), url_path="poll")
    @transaction.atomic
    def poll(self, request):
        notifications = list(
            UserNotification.objects.select_for_update().filter(
                user=request.user,
                is_read=False,
            )
        )
        payload = self.get_serializer(notifications, many=True).data
        if notifications:
            UserNotification.objects.filter(
                pk__in=[notification.pk for notification in notifications],
                user=request.user,
                is_read=False,
            ).update(is_read=True)
        return Response(payload)


class PublicFeaturedSubmissionViewSet(viewsets.ReadOnlyModelViewSet):
    """Anonymous read-only access to approved featured columns."""

    serializer_class = PublicFeaturedSubmissionSerializer
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get_queryset(self):
        queryset = FeaturedSubmission.objects.filter(
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
        ).select_related("user", "user__profile")
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(chinese_title__icontains=search)
                | Q(japanese_title__icontains=search)
            )
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        tag = request.query_params.get("tag", "").strip()
        if tag:
            matching_ids = [
                submission_id
                for submission_id, tags in queryset.values_list("pk", "tags")
                if tag in (tags or [])
            ]
            queryset = queryset.filter(pk__in=matching_ids)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(queryset, many=True).data)


class AdminSessionView(APIView):
    """Verify that the current JWT belongs to a superuser."""

    permission_classes = (IsSuperUser,)

    def get(self, request):
        return Response(
            {
                "id": request.user.pk,
                "username": request.user.username,
                "email": request.user.email,
                "is_superuser": True,
            }
        )


class AdminFeaturedSubmissionViewSet(viewsets.ReadOnlyModelViewSet):
    """Superuser-only queue and audit actions for featured submissions."""

    serializer_class = AdminFeaturedSubmissionSerializer
    permission_classes = (IsSuperUser,)
    http_method_names = ("get", "post", "head", "options")

    def get_queryset(self):
        return FeaturedSubmission.objects.filter(
            audit_status__in=(
                FeaturedSubmission.AuditStatus.PENDING,
                FeaturedSubmission.AuditStatus.REMOVAL_REQUESTED,
            )
        ).select_related("user", "user__profile", "source_anime")

    def _get_locked_submission(self, pk):
        return generics.get_object_or_404(
            self.get_queryset().select_for_update(),
            pk=pk,
        )

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def approve(self, request, pk=None):
        submission = self._get_locked_submission(pk)

        if submission.audit_status == FeaturedSubmission.AuditStatus.PENDING:
            submission.audit_status = FeaturedSubmission.AuditStatus.APPROVED
            submission.save(update_fields=("audit_status", "updated_at"))
            return Response(
                {
                    "detail": "已通过该精选专栏投稿。",
                    "submission": self.get_serializer(submission).data,
                }
            )

        submission_id = submission.pk
        title = submission.chinese_title
        user = submission.user
        UserNotification.objects.create(
            user=user,
            message=f"《{title}》的精选专栏已按你的申请撤下。",
        )
        submission.delete()
        return Response(
            {
                "detail": "已批准撤下该精选专栏。",
                "submission_id": submission_id,
            }
        )

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def reject(self, request, pk=None):
        submission = self._get_locked_submission(pk)

        if submission.audit_status == FeaturedSubmission.AuditStatus.PENDING:
            submission.audit_status = FeaturedSubmission.AuditStatus.REJECTED
            submission.save(update_fields=("audit_status", "updated_at"))
            return Response(
                {
                    "detail": "已打回该精选专栏投稿。",
                    "submission": self.get_serializer(submission).data,
                }
            )

        FeaturedSubmission.objects.filter(pk=submission.pk).update(
            audit_status=FeaturedSubmission.AuditStatus.APPROVED,
            updated_at=timezone.now(),
        )
        UserNotification.objects.create(
            user=submission.user,
            message=f"《{submission.chinese_title}》的撤下申请未获批准，专栏将继续展示。",
        )
        submission.refresh_from_db()
        return Response(
            {
                "detail": "已驳回撤下申请，该专栏将继续展示。",
                "submission": self.get_serializer(submission).data,
            }
        )
