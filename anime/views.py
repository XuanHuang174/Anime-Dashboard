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
from django.db.models import (
    Avg,
    BooleanField,
    Case,
    CharField,
    Count,
    DecimalField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Substr
from django.http import HttpResponse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.decorators import method_decorator
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit
from rest_framework import filters, generics, mixins, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

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
from .pagination import UserAnimeRecordPagination
from .serializers import (
    AccountDeletionSerializer,
    AdminFeaturedSubmissionSerializer,
    AdminPublicJournalProfileSerializer,
    AdminRejectReasonSerializer,
    AnimeSerializer,
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    FeaturedSubmissionCreateSerializer,
    FeaturedSubmissionSerializer,
    FeaturedQuickFilterSerializer,
    JsonStringMapField,
    JournalRecordImportSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PublicFeaturedSubmissionSerializer,
    PublicJournalLobbySerializer,
    PublicJournalStatusSerializer,
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
from .utils.scraper import MetadataScraper, MetadataScraperError


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


class IsAuthenticatedForExternalMetadata(permissions.IsAuthenticated):
    """Give unauthenticated scraper requests a useful login instruction."""

    message = "请先登录后再使用番剧智能搜索与自动填充功能。"


class ExternalMetadataAPIView(APIView):
    """Shared authentication behavior for third-party metadata endpoints."""

    permission_classes = (IsAuthenticatedForExternalMetadata,)
    authentication_required_message = (
        "请先登录后再使用番剧智能搜索与自动填充功能。"
    )

    def permission_denied(self, request, message=None, code=None):
        if not request.user or not request.user.is_authenticated:
            raise NotAuthenticated(self.authentication_required_message)
        return super().permission_denied(request, message=message, code=code)


class ExternalAnimeSearchView(ExternalMetadataAPIView):
    """Proxy Bangumi subject search without exposing third-party details."""

    @method_decorator(
        ratelimit(
            group="external-anime-search",
            key="user",
            rate="30/m",
            method="GET",
            block=False,
        )
    )
    def get(self, request):
        if getattr(request, "limited", False):
            return Response(
                {
                    "detail": (
                        "你的手速太快啦！键盘要冒烟了，请休息一分钟~"
                    ),
                    "code": "rate_limited",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        keyword = request.query_params.get("q", "").strip()
        if not keyword:
            return Response(
                {"detail": "请输入需要搜索的番剧名称。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(keyword) > 100:
            return Response(
                {"detail": "搜索关键词不能超过 100 个字符。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            results = MetadataScraper.search_bangumi(keyword)
        except MetadataScraperError as exc:
            logger.warning("Bangumi search failed: %s", exc)
            return Response(
                {"detail": "Bangumi 暂时无法访问，请稍后重试。"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(results)


class ExternalAnimeAutofillView(ExternalMetadataAPIView):
    """Return a flat payload that maps directly to AddAnimeModal state."""

    @method_decorator(
        ratelimit(
            group="external-anime-autofill",
            key="user",
            rate="100/h",
            method="GET",
            block=False,
        )
    )
    def get(self, request):
        if getattr(request, "limited", False):
            return Response(
                {
                    "detail": (
                        "电波过载！一小时内录入了太多番剧，请稍后再试。"
                    ),
                    "code": "rate_limited",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        subject_id = request.query_params.get("id", "").strip()
        anime_name = request.query_params.get("name", "").strip()
        if not subject_id:
            return Response(
                {"detail": "缺少 Bangumi 条目 ID。"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if anime_name and len(anime_name) > 200:
            return Response(
                {"detail": "番剧名称不能超过 200 个字符。"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            details = MetadataScraper.get_bangumi_details(subject_id)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except MetadataScraperError as exc:
            logger.warning("Bangumi details request failed: %s", exc)
            return Response(
                {"detail": "Bangumi 条目详情暂时无法访问，请稍后重试。"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        moegirl_keyword = anime_name or details["chinese_title"]
        baike_url = MetadataScraper.get_moegirl_url(moegirl_keyword)
        return Response(
            {
                "bangumiId": details["bangumi_id"],
                "chineseTitle": details["chinese_title"],
                "japaneseTitle": details["japanese_title"],
                "airDate": details["air_date"],
                "airingPeriod": details["airing_period"],
                "studio": details["studio"],
                "episodes": details["episodes"],
                "description": details["description"],
                "posterUrl": details["poster_url"],
                "tags": details["tags"],
                "baikeUrl": baike_url,
            }
        )


PRESET_QUICK_FILTER_TAGS = {
    "yuri": ("真百", "轻百", "真百合", "轻百合"),
    "moe": ("萌系", "日常"),
    "comedy": ("搞笑", "校园"),
    "og": ("原创", "治愈"),
}


def _combined_record_tags(record):
    base_tags = (
        record.get("private_base_tags")
        if record.get("metadata_initialized")
        else record.get("anime__base_tags")
    )
    tags = set(base_tags or [])
    tags.update(record.get("custom_tags") or [])
    return tags


def _record_metadata_value(record, private_field, shared_field):
    if record.get("metadata_initialized"):
        return record.get(private_field)
    return record.get(shared_field)


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
            (
                Q(metadata_initialized=True)
                & (
                    Q(private_chinese_title__icontains=search)
                    | Q(private_japanese_title__icontains=search)
                )
            )
            | (
                Q(metadata_initialized=False)
                & (
                    Q(anime__chinese_title__icontains=search)
                    | Q(anime__japanese_title__icontains=search)
                )
            )
        )
    if year and year != "all" and year.isdigit():
        queryset = queryset.filter(
            Q(
                metadata_initialized=True,
                private_airing_period__startswith=f"{year}-",
            )
            | Q(
                metadata_initialized=False,
                anime__airing_period__startswith=f"{year}-",
            )
        )
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
        "metadata_initialized",
        "private_base_tags",
        "private_chinese_title",
        "custom_tags",
        "anime__base_tags",
        "anime__chinese_title",
    )
    for record in records.iterator(chunk_size=2000):
        tags = _combined_record_tags(record)
        title_upper = (
            _record_metadata_value(
                record,
                "private_chinese_title",
                "anime__chinese_title",
            )
            or ""
        ).upper()

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
        _effective_airing_period=Case(
            When(metadata_initialized=True, then=F("private_airing_period")),
            default=F("anime__airing_period"),
            output_field=CharField(),
        ),
        _effective_title=Case(
            When(metadata_initialized=True, then=F("private_chinese_title")),
            default=F("anime__chinese_title"),
            output_field=CharField(),
        ),
        _airing_year=Cast(
            Substr("_effective_airing_period", 1, 4),
            output_field=IntegerField(),
        ),
        _airing_season=Cast(
            Substr("_effective_airing_period", 6),
            output_field=IntegerField(),
        ),
    )

    orderings = {
        "date-asc": (
            "_unrated_order", "_airing_year", "_airing_season",
            "_completed_order", "_effective_title", "pk",
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
        "_completed_order", "_effective_title", "pk",
    )
    return queryset.order_by(*orderings.get(sort_order, default_ordering))


def _build_filter_options(queryset):
    """Return full-dataset filter choices instead of choices from one page."""

    tags = set()
    years = set()
    for record in queryset.values(
        "metadata_initialized",
        "private_base_tags",
        "private_airing_period",
        "custom_tags",
        "anime__base_tags",
        "anime__airing_period",
    ).iterator(chunk_size=2000):
        tags.update(_combined_record_tags(record))
        period = _record_metadata_value(
            record,
            "private_airing_period",
            "anime__airing_period",
        ) or ""
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
        "metadata_initialized",
        "private_chinese_title",
        "private_airing_period",
        "private_base_tags",
        "custom_tags",
        "anime__chinese_title",
        "anime__airing_period",
        "anime__base_tags",
    )
    for record in records.iterator(chunk_size=2000):
        score = record["personal_score"]
        title = _record_metadata_value(
            record,
            "private_chinese_title",
            "anime__chinese_title",
        ) or ""
        period = _record_metadata_value(
            record,
            "private_airing_period",
            "anime__airing_period",
        ) or ""
        tags = _combined_record_tags(record)

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
        "total_count": queryset.count(),
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


class PublicJournalAccessView(APIView):
    """Apply for or immediately cancel a public-journal pass."""

    permission_classes = (permissions.IsAuthenticated,)

    @transaction.atomic
    def post(self, request):
        profile, _created = UserProfile.objects.select_for_update().get_or_create(
            user=request.user,
        )
        if profile.public_status == UserProfile.PublicStatus.APPROVED:
            return Response(
                {
                    "detail": "你的手账已经公开，无需重复申请。",
                    **PublicJournalStatusSerializer(profile).data,
                },
            )
        if profile.public_status != UserProfile.PublicStatus.PENDING:
            profile.public_status = UserProfile.PublicStatus.PENDING
            profile.is_public = False
            profile.save(update_fields=("public_status", "is_public"))
        return Response(
            {
                "detail": "分享申请已提交，请等待管理员审核。",
                **PublicJournalStatusSerializer(profile).data,
            },
        )

    @transaction.atomic
    def patch(self, request):
        profile, _created = UserProfile.objects.select_for_update().get_or_create(
            user=request.user,
        )
        profile.public_status = UserProfile.PublicStatus.PRIVATE
        profile.is_public = False
        profile.save(update_fields=("public_status", "is_public"))
        return Response(
            {
                "detail": "已取消分享，你的手账已立即恢复私密。",
                **PublicJournalStatusSerializer(profile).data,
            },
        )


def _public_journal_profiles():
    """Build public lobby cards with database aggregates and three poster rows."""

    records = (
        UserAnimeRecord.objects.filter(user_id=OuterRef("user_id"))
        .annotate(
            effective_poster_url=Case(
                When(
                    metadata_initialized=True,
                    then=F("private_poster_url"),
                ),
                default=F("anime__poster_url"),
                output_field=CharField(max_length=1000),
            ),
            effective_poster_image=Case(
                When(
                    metadata_initialized=True,
                    then=F("private_poster_image"),
                ),
                default=F("anime__poster_image"),
                output_field=CharField(max_length=255),
            ),
        )
        .order_by(F("personal_score").desc(nulls_last=True), "-updated_at", "pk")
    )

    private_movie = Q(
        user__anime_records__metadata_initialized=True,
    ) & (
        Q(user__anime_records__private_chinese_title__icontains="剧场版")
        | Q(user__anime_records__private_base_tags__icontains="剧场版")
    )
    shared_movie = Q(
        user__anime_records__metadata_initialized=False,
    ) & (
        Q(user__anime_records__anime__chinese_title__icontains="剧场版")
        | Q(user__anime_records__anime__base_tags__icontains="剧场版")
    )
    private_ova = Q(
        user__anime_records__metadata_initialized=True,
    ) & (
        Q(user__anime_records__private_chinese_title__icontains="OVA")
        | Q(user__anime_records__private_base_tags__icontains="OVA")
    )
    shared_ova = Q(
        user__anime_records__metadata_initialized=False,
    ) & (
        Q(user__anime_records__anime__chinese_title__icontains="OVA")
        | Q(user__anime_records__anime__base_tags__icontains="OVA")
    )
    private_short = Q(
        user__anime_records__metadata_initialized=True,
        user__anime_records__private_base_tags__icontains="泡面番",
    )
    shared_short = Q(
        user__anime_records__metadata_initialized=False,
        user__anime_records__anime__base_tags__icontains="泡面番",
    )

    annotations = {
        "completed_count": Count(
            "user__anime_records",
            filter=Q(
                user__anime_records__watch_status=(
                    UserAnimeRecord.WatchStatus.COMPLETED
                )
            ),
        ),
        "average_score": Avg(
            "user__anime_records__personal_score",
            output_field=DecimalField(max_digits=4, decimal_places=2),
        ),
        "movie_count": Count(
            "user__anime_records",
            filter=private_movie | shared_movie,
        ),
        "ova_count": Count(
            "user__anime_records",
            filter=private_ova | shared_ova,
        ),
        "short_count": Count(
            "user__anime_records",
            filter=private_short | shared_short,
        ),
        "masterpiece_count": Count(
            "user__anime_records",
            filter=Q(user__anime_records__personal_score__gte=9.5),
        ),
    }
    for rank in range(1, 4):
        offset = rank - 1
        annotations[f"top_poster_url_{rank}"] = Subquery(
            records.values("effective_poster_url")[offset:rank],
            output_field=CharField(max_length=1000),
        )
        annotations[f"top_poster_image_{rank}"] = Subquery(
            records.values("effective_poster_image")[offset:rank],
            output_field=CharField(max_length=255),
        )

    return (
        UserProfile.objects.filter(
            is_public=True,
            public_status=UserProfile.PublicStatus.APPROVED,
        )
        .select_related("user")
        .annotate(**annotations)
        .order_by("-masterpiece_count", "-average_score", "user__username")
    )


class PublicJournalLobbyView(generics.ListAPIView):
    """Anonymous lobby containing compact cards for approved public journals."""

    serializer_class = PublicJournalLobbySerializer
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get_queryset(self):
        queryset = _public_journal_profiles()
        search_query = self.request.query_params.get("search", "").strip()
        if search_query:
            queryset = queryset.filter(
                Q(nickname__icontains=search_query)
                | Q(user__username__icontains=search_query)
            )
        return queryset


class PublicJournalDetailView(generics.ListAPIView):
    """Anonymous paginated records for one explicitly public journal."""

    serializer_class = UserAnimeRecordSerializer
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    pagination_class = UserAnimeRecordPagination

    def get_profile(self):
        if not hasattr(self, "_public_profile"):
            self._public_profile = generics.get_object_or_404(
                UserProfile.objects.select_related("user"),
                user__username=self.kwargs["username"],
                is_public=True,
                public_status=UserProfile.PublicStatus.APPROVED,
            )
        return self._public_profile

    def get_base_queryset(self):
        profile = self.get_profile()
        return (
            UserAnimeRecord.objects.filter(user=profile.user)
            .select_related("user", "anime")
            .prefetch_related("tag_preferences")
        )

    def get_queryset(self):
        profile = self.get_profile()
        queryset = _apply_record_filters(
            self.get_base_queryset(),
            self.request,
            filter_owner=profile.user,
        )
        return _order_record_queryset(
            queryset,
            self.request.query_params.get("sort", "date-desc"),
        )


class PublicJournalStatsView(APIView):
    """Aggregate cards for one public journal without exposing private access."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get(self, request, username):
        profile = generics.get_object_or_404(
            UserProfile.objects.select_related("user"),
            user__username=username,
            is_public=True,
            public_status=UserProfile.PublicStatus.APPROVED,
        )
        queryset = (
            UserAnimeRecord.objects.filter(user=profile.user)
            .select_related("user", "anime")
        )
        return Response(_build_record_stats(queryset))


class PublicJournalFilterOptionsView(APIView):
    """Tags and years required by the shared Dashboard filter controls."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def get(self, request, username):
        profile = generics.get_object_or_404(
            UserProfile.objects.select_related("user"),
            user__username=username,
            is_public=True,
            public_status=UserProfile.PublicStatus.APPROVED,
        )
        queryset = UserAnimeRecord.objects.filter(user=profile.user).select_related(
            "anime",
        )
        return Response(_build_filter_options(queryset))


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
            serialized_record = UserAnimeRecordSerializer(
                record,
                context={"request": request},
            ).data
            anime_data = dict(serialized_record["anime"])
            poster_url = anime_data["poster_url"]
            if not poster_url and anime_data["poster_image"]:
                poster_url = anime_data["poster_image"]

            exported_records.append(
                {
                    "anime": {
                        "chinese_title": anime_data["chinese_title"],
                        "japanese_title": anime_data["japanese_title"],
                        "airing_period": anime_data["airing_period"],
                        "studio": anime_data["studio"],
                        "episodes": anime_data["episodes"],
                        "description": anime_data["description"],
                        "poster_url": poster_url,
                        "baike_url": anime_data["baike_url"],
                        "base_tags": anime_data["base_tags"],
                        "tag_colors": anime_data["tag_colors"],
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
                    private_metadata = {**lookup, **anime_data}
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
                            metadata_initialized=True,
                            private_chinese_title=private_metadata["chinese_title"],
                            private_japanese_title=private_metadata["japanese_title"],
                            private_airing_period=private_metadata["airing_period"],
                            private_studio=private_metadata.get("studio", ""),
                            private_episodes=private_metadata.get("episodes", ""),
                            private_description=private_metadata.get("description", ""),
                            private_poster_url=private_metadata.get("poster_url", ""),
                            private_baike_url=private_metadata.get("baike_url", ""),
                            private_base_tags=private_metadata.get("base_tags", []),
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
    """Public reads/creates; only superusers may mutate shared library rows."""

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

    def get_queryset(self):
        queryset = Anime.objects.filter(is_active=True)
        user = self.request.user
        if user.is_authenticated:
            queryset = queryset.annotate(
                is_added=Exists(
                    UserAnimeRecord.objects.filter(
                        user=user,
                        anime_id=OuterRef("pk"),
                    )
                )
            )
        else:
            queryset = queryset.annotate(
                is_added=Value(False, output_field=BooleanField()),
            )

        valid_period = Q(airing_period__regex=r"^\d{4}-(1|4|7|10)$")
        return queryset.annotate(
            _airing_year=Case(
                When(
                    valid_period,
                    then=Cast(Substr("airing_period", 1, 4), IntegerField()),
                ),
                default=Value(-1),
                output_field=IntegerField(),
            ),
            _airing_month=Case(
                When(
                    valid_period,
                    then=Cast(Substr("airing_period", 6), IntegerField()),
                ),
                default=Value(-1),
                output_field=IntegerField(),
            ),
        ).order_by("-_airing_year", "-_airing_month", "chinese_title", "pk")

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        if self.request.method == "POST":
            return [permissions.IsAuthenticated()]
        return [IsSuperUser()]


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
    def perform_destroy(self, instance):
        """Delete featured snapshots derived from this private record as well."""

        FeaturedSubmission.objects.filter(
            user=self.request.user,
            source_anime=instance.anime,
        ).delete()
        instance.delete()

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
        """Update only this user's isolated metadata snapshot and private record."""

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

        metadata_field_map = {
            "chinese_title": "private_chinese_title",
            "japanese_title": "private_japanese_title",
            "airing_period": "private_airing_period",
            "studio": "private_studio",
            "episodes": "private_episodes",
            "description": "private_description",
            "poster_url": "private_poster_url",
            "poster_image": "private_poster_image",
            "baike_url": "private_baike_url",
            "base_tags": "private_base_tags",
        }
        for source_field, target_field in metadata_field_map.items():
            if source_field in anime_serializer.validated_data:
                setattr(
                    record,
                    target_field,
                    anime_serializer.validated_data[source_field],
                )
        record.metadata_initialized = True
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
        metadata = record.effective_anime_metadata()
        year_match = re.search(
            r"(?:19|20|21)\d{2}",
            metadata["airing_period"] or "",
        )
        snapshot = FeaturedSubmission(
            user=request.user,
            source_anime=anime,
            source_record=record,
            chinese_title=metadata["chinese_title"],
            japanese_title=metadata["japanese_title"],
            year=int(year_match.group()) if year_match else None,
            airing_period=metadata["airing_period"],
            studio=metadata["studio"],
            episodes=metadata["episodes"],
            description=metadata["description"],
            baike_url=metadata["baike_url"],
            watch_status=record.watch_status,
            score=record.personal_score,
            review=record.review,
            poster_url=metadata["poster_url"],
            tags=list(
                dict.fromkeys(
                    [
                        *(metadata["base_tags"] or []),
                        *(record.custom_tags or []),
                    ]
                )
            ),
            tag_colors=get_record_tag_colors(record),
            audit_status=FeaturedSubmission.AuditStatus.PENDING,
        )
        poster_image = metadata["poster_image"]
        if poster_image:
            poster_image.open("rb")
            try:
                suffix = Path(poster_image.name).suffix.lower()
                snapshot.poster.save(
                    f"{uuid4().hex}{suffix}",
                    ContentFile(poster_image.read()),
                    save=False,
                )
            finally:
                poster_image.close()
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


class FeaturedQuickFilterViewSet(viewsets.ModelViewSet):
    """Public reads with superuser-only writes for featured quick filters."""

    serializer_class = FeaturedQuickFilterSerializer
    queryset = FeaturedQuickFilter.objects.all()

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [IsSuperUser()]

    @action(detail=False, methods=("get",), url_path="available-tags")
    def available_tags(self, request):
        tags = set()
        submissions = FeaturedSubmission.objects.exclude(
            audit_status=FeaturedSubmission.AuditStatus.REJECTED,
        ).values_list("tags", flat=True)
        for submission_tags in submissions.iterator():
            tags.update(submission_tags or [])
        return Response({"tags": sorted(tags)})


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


class AdminPublicJournalProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """Review, approve, reject, or revoke public-journal passes."""

    serializer_class = AdminPublicJournalProfileSerializer
    permission_classes = (IsSuperUser,)
    http_method_names = ("get", "post", "head", "options")
    filter_backends = (filters.SearchFilter,)
    search_fields = ("=user__username", "nickname", "user__email")

    def get_queryset(self):
        queryset = UserProfile.objects.filter(
            public_status__in=(
                UserProfile.PublicStatus.PENDING,
                UserProfile.PublicStatus.APPROVED,
            )
        ).select_related("user")
        requested_status = self.request.query_params.get("status", "").strip()
        if requested_status in {
            UserProfile.PublicStatus.PENDING,
            UserProfile.PublicStatus.APPROVED,
        }:
            queryset = queryset.filter(public_status=requested_status)
        return queryset.order_by("public_status", "user__username")

    def _get_locked_profile(self, pk):
        return generics.get_object_or_404(
            UserProfile.objects.select_for_update().select_related("user"),
            pk=pk,
        )

    def _response(self, profile, detail):
        return Response(
            {
                "detail": detail,
                "profile": self.get_serializer(profile).data,
            }
        )

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def approve(self, request, pk=None):
        profile = self._get_locked_profile(pk)
        if profile.public_status != UserProfile.PublicStatus.PENDING:
            return Response(
                {"detail": "只有审核中的申请可以发放通行证。"},
                status=status.HTTP_409_CONFLICT,
            )
        profile.public_status = UserProfile.PublicStatus.APPROVED
        profile.save(update_fields=("public_status", "is_public"))
        UserNotification.objects.create(
            user=profile.user,
            message="恭喜，你的个人手账已通过审核并进入番剧共创大厅。",
        )
        return self._response(profile, "通行证已发放，手账现已公开。")

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def reject(self, request, pk=None):
        profile = self._get_locked_profile(pk)
        if profile.public_status != UserProfile.PublicStatus.PENDING:
            return Response(
                {"detail": "只有审核中的申请可以打回。"},
                status=status.HTTP_409_CONFLICT,
            )
        reason_serializer = AdminRejectReasonSerializer(data=request.data)
        reason_serializer.is_valid(raise_exception=True)
        reject_reason = reason_serializer.validated_data["reject_reason"]
        profile.public_status = UserProfile.PublicStatus.PRIVATE
        profile.save(update_fields=("public_status", "is_public"))
        UserNotification.objects.create(
            user=profile.user,
            message=(
                "抱歉，您的手账分享未能通过审核。"
                f"原因：{reject_reason}"
            ),
        )
        return self._response(profile, "申请已打回，手账保持私密。")

    @action(detail=True, methods=("post",))
    @transaction.atomic
    def revoke(self, request, pk=None):
        profile = self._get_locked_profile(pk)
        if (
            profile.public_status != UserProfile.PublicStatus.APPROVED
            or not profile.is_public
        ):
            return Response(
                {"detail": "该用户当前没有可吊销的公开通行证。"},
                status=status.HTTP_409_CONFLICT,
            )
        profile.public_status = UserProfile.PublicStatus.PRIVATE
        profile.save(update_fields=("public_status", "is_public"))
        UserNotification.objects.create(
            user=profile.user,
            message="你的番剧共创通行证已被管理员吊销，手账现已恢复私密。",
        )
        return self._response(profile, "通行证已吊销，该手账已立即停止公开。")


def _get_admin_review_profile(username):
    return generics.get_object_or_404(
        UserProfile.objects.select_related("user"),
        user__username=username,
        public_status__in=(
            UserProfile.PublicStatus.PENDING,
            UserProfile.PublicStatus.APPROVED,
        ),
    )


class AdminPublicJournalDetailView(generics.ListAPIView):
    """Superuser-only live record view for pending and approved journals."""

    serializer_class = UserAnimeRecordSerializer
    permission_classes = (IsSuperUser,)
    pagination_class = UserAnimeRecordPagination

    def get_queryset(self):
        profile = _get_admin_review_profile(self.kwargs["username"])
        queryset = (
            UserAnimeRecord.objects.filter(user=profile.user)
            .select_related("user", "anime")
            .prefetch_related("tag_preferences")
        )
        queryset = _apply_record_filters(
            queryset,
            self.request,
            filter_owner=profile.user,
        )
        return _order_record_queryset(
            queryset,
            self.request.query_params.get("sort", "date-desc"),
        )


class AdminPublicJournalStatsView(APIView):
    permission_classes = (IsSuperUser,)

    def get(self, request, username):
        profile = _get_admin_review_profile(username)
        queryset = UserAnimeRecord.objects.filter(user=profile.user).select_related(
            "anime",
        )
        return Response(_build_record_stats(queryset))


class AdminPublicJournalFilterOptionsView(APIView):
    permission_classes = (IsSuperUser,)

    def get(self, request, username):
        profile = _get_admin_review_profile(username)
        queryset = UserAnimeRecord.objects.filter(user=profile.user).select_related(
            "anime",
        )
        return Response(_build_filter_options(queryset))


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
            message=f"{title}的精选专栏已按你的申请撤下。",
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
        reason_serializer = AdminRejectReasonSerializer(data=request.data)
        reason_serializer.is_valid(raise_exception=True)
        reject_reason = reason_serializer.validated_data["reject_reason"]

        if submission.audit_status == FeaturedSubmission.AuditStatus.PENDING:
            submission.audit_status = FeaturedSubmission.AuditStatus.REJECTED
            submission.save(update_fields=("audit_status", "updated_at"))
            UserNotification.objects.create(
                user=submission.user,
                message=(
                    f"抱歉，您的番剧点评《{submission.chinese_title}》"
                    f"未能入选精选专栏。原因：{reject_reason}"
                ),
            )
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
            message=(
                f"《{submission.chinese_title}》的撤下申请未获批准，"
                f"专栏将继续展示。原因：{reject_reason}"
            ),
        )
        submission.refresh_from_db()
        return Response(
            {
                "detail": "已驳回撤下申请，该专栏将继续展示。",
                "submission": self.get_serializer(submission).data,
            }
        )
