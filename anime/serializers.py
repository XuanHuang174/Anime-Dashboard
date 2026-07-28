import json
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from PIL import Image, UnidentifiedImageError

from .models import (
    Anime,
    FeaturedSubmission,
    TAG_COLOR_KEYS,
    UserAnimeRecord,
    UserNotification,
    UserProfile,
    UserQuickFilter,
)
from .tag_services import get_record_tag_colors, initialize_record_tag_colors


STRICT_EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"[A-Z]{2,6}$",
    re.IGNORECASE | re.ASCII,
)

KNOWN_PROVIDER_DOMAINS = {
    "qq": {"qq.com", "vip.qq.com"},
    "163": {"163.com", "vip.163.com"},
    "126": {"126.com"},
    "yeah": {"yeah.net"},
    "gmail": {"gmail.com"},
    "outlook": {"outlook.com"},
    "hotmail": {"hotmail.com"},
    "live": {"live.com"},
}

MAX_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def validate_uploaded_image(image, *, label):
    """Validate the actual image stream instead of trusting its file suffix."""

    if image is None:
        return None

    if image.size > MAX_IMAGE_UPLOAD_SIZE:
        raise serializers.ValidationError(f"{label}图片不能超过 5MB。")

    original_position = image.tell()
    try:
        uploaded_image = Image.open(image)
        image_format = (uploaded_image.format or "").upper()
        uploaded_image.verify()
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise serializers.ValidationError(
            f"{label}必须是有效的图片文件。",
        ) from error
    finally:
        image.seek(original_position)

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise serializers.ValidationError(
            f"{label}仅支持 JPG、PNG、WebP 或 GIF 格式。",
        )
    return image


def validate_strict_email(value):
    """Normalize an address and reject malformed or mistyped provider domains."""

    email = get_user_model().objects.normalize_email(value).strip().lower()
    if not STRICT_EMAIL_PATTERN.fullmatch(email):
        raise serializers.ValidationError("请输入正确格式的邮箱地址。")

    domain = email.rsplit("@", 1)[1]
    labels = domain.split(".")
    provider_name = labels[-2] if len(labels) >= 2 else ""
    allowed_domains = KNOWN_PROVIDER_DOMAINS.get(provider_name)
    if allowed_domains is not None and domain not in allowed_domains:
        raise serializers.ValidationError("请输入正确格式的邮箱地址。")

    return email


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Return a Chinese authentication error without changing JWT behavior."""

    default_error_messages = {
        **TokenObtainPairSerializer.default_error_messages,
        "no_active_account": "邮箱或密码不正确，请检查后重试。",
    }


class UserRegistrationSerializer(serializers.Serializer):
    """Register an inactive account whose login name is its email address."""

    email = serializers.EmailField(
        max_length=150,
        error_messages={"invalid": "请输入正确格式的邮箱地址。"},
    )
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        required=False,
        style={"input_type": "password"},
    )

    def validate_email(self, value):
        user_model = get_user_model()
        email = validate_strict_email(value)
        if user_model.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("该邮箱已被注册。")
        if user_model.objects.filter(username__iexact=email).exists():
            raise serializers.ValidationError("该邮箱已被注册。")
        return email

    def validate(self, attrs):
        password_confirm = attrs.pop("password_confirm", None)
        if password_confirm is not None and attrs["password"] != password_confirm:
            raise serializers.ValidationError(
                {"password_confirm": "两次输入的密码不一致。"}
            )

        user_model = get_user_model()
        candidate = user_model(username=attrs["email"], email=attrs["email"])
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as error:
            raise serializers.ValidationError(
                {"password": list(error.messages)}
            ) from error
        return attrs

    def create(self, validated_data):
        user_model = get_user_model()
        email = validated_data["email"]
        return user_model.objects.create_user(
            username=email,
            email=email,
            password=validated_data["password"],
            is_active=False,
        )


class PasswordResetRequestSerializer(serializers.Serializer):
    """Validate the email submitted to the password-reset endpoint."""

    email = serializers.EmailField(
        max_length=254,
        error_messages={"invalid": "请输入正确格式的邮箱地址。"},
    )

    def validate_email(self, value):
        return validate_strict_email(value)


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Validate a reset token payload and the replacement password."""

    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_new_password(self, value):
        user = self.context.get("user")
        try:
            validate_password(value, user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages)) from error
        return value


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    avatar = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = UserProfile
        fields = (
            "username",
            "email",
            "nickname",
            "showcase_subtitle",
            "avatar",
            "max_featured_limit",
        )
        read_only_fields = ("username", "email", "max_featured_limit")

    def validate_avatar(self, avatar):
        return validate_uploaded_image(avatar, label="头像")


class PublicUserProfileSerializer(serializers.ModelSerializer):
    """Public-safe showcase identity; deliberately excludes the email address."""

    username = serializers.CharField(source="user.username", read_only=True)
    avatar = serializers.ImageField(read_only=True)

    class Meta:
        model = UserProfile
        fields = ("username", "nickname", "showcase_subtitle", "avatar")
        read_only_fields = fields


class ChangePasswordSerializer(serializers.Serializer):
    """Validate the authenticated user's current and replacement passwords."""

    old_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("当前密码不正确。")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        try:
            validate_password(value, user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages)) from error
        return value

    def validate(self, attrs):
        if attrs["old_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "新密码不能与当前密码相同。"},
            )
        return attrs


class AccountDeletionSerializer(serializers.Serializer):
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )
    confirmation = serializers.CharField(write_only=True)

    def validate_password(self, password):
        request = self.context["request"]
        if not request.user.check_password(password):
            raise serializers.ValidationError("当前密码不正确。")
        return password

    def validate_confirmation(self, confirmation):
        if confirmation != "DELETE":
            raise serializers.ValidationError("缺少有效的永久注销确认。")
        return confirmation


class JsonStringListField(serializers.JSONField):
    """Accept a list or a JSON-encoded list from multipart forms."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise serializers.ValidationError("必须是合法的 JSON 数组。") from error

        value = super().to_internal_value(data)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise serializers.ValidationError("必须是字符串数组。")
        return value


class JsonStringMapField(serializers.JSONField):
    """Accept a string-to-string mapping or its JSON-encoded representation."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise serializers.ValidationError("必须是合法的 JSON 对象。") from error

        value = super().to_internal_value(data)
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise serializers.ValidationError("必须是字符串到字符串的映射。")
        if any(not key.strip() or item not in TAG_COLOR_KEYS for key, item in value.items()):
            raise serializers.ValidationError("包含无效的标签名或颜色方案。")
        return value


class AnimeBasicSerializer(serializers.ModelSerializer):
    """Compact anime representation embedded in private user records."""

    class Meta:
        model = Anime
        fields = (
            "id",
            "chinese_title",
            "japanese_title",
            "airing_period",
            "studio",
            "episodes",
            "description",
            "poster_url",
            "poster_image",
            "baike_url",
            "base_tags",
            "tag_colors",
        )
        read_only_fields = fields


class AnimeSerializer(serializers.ModelSerializer):
    """Public anime library representation; writes require authentication."""

    base_tags = JsonStringListField(required=False)
    tag_colors = JsonStringMapField(required=False)

    class Meta:
        model = Anime
        fields = (
            "id",
            "chinese_title",
            "japanese_title",
            "airing_period",
            "studio",
            "episodes",
            "description",
            "poster_url",
            "poster_image",
            "baike_url",
            "base_tags",
            "tag_colors",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_poster_image(self, poster_image):
        return validate_uploaded_image(poster_image, label="海报")

    def update(self, instance, validated_data):
        if "tag_colors" in validated_data:
            raise serializers.ValidationError(
                {
                    "tag_colors": (
                        "标签配色属于用户私人手账，请通过追番记录详情接口修改。"
                    )
                }
            )
        return super().update(instance, validated_data)


class JournalAnimeImportSerializer(serializers.Serializer):
    """Portable anime metadata accepted by the journal import endpoint."""

    chinese_title = serializers.CharField(max_length=200)
    japanese_title = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
    )
    airing_period = serializers.CharField(max_length=32)
    studio = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
    )
    episodes = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        default="",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    poster_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
        default="",
    )
    baike_url = serializers.URLField(
        max_length=1000,
        required=False,
        allow_blank=True,
        default="",
    )
    base_tags = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=False),
        required=False,
        default=list,
        max_length=100,
    )
    tag_colors = serializers.DictField(
        child=serializers.ChoiceField(choices=sorted(TAG_COLOR_KEYS)),
        required=False,
        default=dict,
    )

    def validate_base_tags(self, tags):
        return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))

    def validate_tag_colors(self, tag_colors):
        normalized = {}
        for tag, color in tag_colors.items():
            normalized_tag = tag.strip()
            if not normalized_tag:
                raise serializers.ValidationError("标签名称不能为空。")
            normalized[normalized_tag] = color
        return normalized


class JournalRecordImportSerializer(serializers.Serializer):
    """Validate one portable record without accepting user or database IDs."""

    anime = JournalAnimeImportSerializer()
    personal_score = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=Decimal("0.0"),
        max_value=Decimal("10.0"),
        required=False,
        allow_null=True,
        default=None,
    )
    watch_status = serializers.ChoiceField(
        choices=UserAnimeRecord.WatchStatus.choices,
        default=UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
    )
    review = serializers.CharField(required=False, allow_blank=True, default="")
    custom_tags = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=False),
        required=False,
        default=list,
        max_length=100,
    )

    def validate_custom_tags(self, tags):
        return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


class UserAnimeRecordSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source="user.username", read_only=True)
    anime = serializers.SerializerMethodField()
    anime_id = serializers.PrimaryKeyRelatedField(
        source="anime",
        queryset=Anime.objects.filter(is_active=True),
        write_only=True,
    )
    watch_status_display = serializers.CharField(
        source="get_watch_status_display",
        read_only=True,
    )

    class Meta:
        model = UserAnimeRecord
        fields = (
            "id",
            "user",
            "anime",
            "anime_id",
            "personal_score",
            "watch_status",
            "watch_status_display",
            "review",
            "custom_tags",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "user", "anime", "created_at", "updated_at")

    def validate(self, attrs):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return attrs

        anime = attrs.get("anime")
        if anime is None and self.instance is not None:
            anime = self.instance.anime

        records = UserAnimeRecord.objects.filter(user=request.user, anime=anime)
        if self.instance is not None:
            records = records.exclude(pk=self.instance.pk)

        if anime is not None and records.exists():
            raise serializers.ValidationError(
                {"anime_id": "你已经为这部番剧创建过追番记录。"}
            )

        return attrs

    def get_anime(self, record):
        data = AnimeBasicSerializer(
            record.anime,
            context=self.context,
        ).data
        # Keep the existing frontend contract while replacing the shared map
        # with colors owned exclusively by this user's private record.
        data["tag_colors"] = get_record_tag_colors(record)
        return data


class UserQuickFilterSerializer(serializers.ModelSerializer):
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=False),
        allow_empty=False,
        max_length=8,
    )
    title_keywords = serializers.ListField(
        child=serializers.CharField(max_length=30, allow_blank=False),
        required=False,
        max_length=4,
    )

    class Meta:
        model = UserQuickFilter
        fields = (
            "id", "name", "tags", "title_keywords", "match_mode",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_tags(self, tags):
        normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if not normalized:
            raise serializers.ValidationError("请至少选择一个标签。")
        return normalized


class FeaturedSubmissionSerializer(serializers.ModelSerializer):
    """User-facing featured submission data; audit decisions are server-owned."""

    user = serializers.CharField(source="user.username", read_only=True)
    source_anime_id = serializers.IntegerField(read_only=True)
    tags = JsonStringListField(required=False)
    poster = serializers.ImageField(required=False, allow_null=True)
    watch_status_display = serializers.CharField(
        source="get_watch_status_display",
        read_only=True,
    )
    audit_status_display = serializers.CharField(
        source="get_audit_status_display",
        read_only=True,
    )

    class Meta:
        model = FeaturedSubmission
        fields = (
            "id",
            "user",
            "source_anime_id",
            "chinese_title",
            "japanese_title",
            "year",
            "watch_status",
            "watch_status_display",
            "score",
            "review",
            "poster",
            "poster_url",
            "tags",
            "audit_status",
            "audit_status_display",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "user",
            "source_anime_id",
            "audit_status",
            "audit_status_display",
            "created_at",
            "updated_at",
        )

    def validate_poster(self, poster):
        return validate_uploaded_image(poster, label="精选专栏海报")

    def validate_tags(self, tags):
        normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if len(normalized) > 20:
            raise serializers.ValidationError("标签最多允许 20 个。")
        return normalized


class FeaturedSubmissionCreateSerializer(serializers.Serializer):
    anime_id = serializers.IntegerField(min_value=1)


class PublicFeaturedSubmissionSerializer(serializers.ModelSerializer):
    """Public-safe approved-column representation."""

    author_name = serializers.SerializerMethodField()
    watch_status_display = serializers.CharField(
        source="get_watch_status_display",
        read_only=True,
    )

    class Meta:
        model = FeaturedSubmission
        fields = (
            "id",
            "author_name",
            "chinese_title",
            "japanese_title",
            "year",
            "watch_status",
            "watch_status_display",
            "score",
            "review",
            "poster",
            "poster_url",
            "tags",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_author_name(self, submission):
        profile = getattr(submission.user, "profile", None)
        if profile and profile.nickname:
            return profile.nickname
        return "手账主理人"


class UserNotificationSerializer(serializers.ModelSerializer):
    """Notifications are immutable except for the read flag."""

    class Meta:
        model = UserNotification
        fields = ("id", "message", "is_read", "created_at")
        read_only_fields = ("id", "message", "created_at")


class JsonObjectField(serializers.JSONField):
    """Accept a JSON object or a JSON-encoded string from multipart forms."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise serializers.ValidationError("必须是合法的 JSON 对象。") from error

        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("必须是 JSON 对象。")
        return value


class UserAnimeRecordAddSerializer(serializers.Serializer):
    """Create a record from an existing anime or atomically create a new one."""

    anime_id = serializers.PrimaryKeyRelatedField(
        source="existing_anime",
        queryset=Anime.objects.filter(is_active=True),
        required=False,
    )
    anime_data = JsonObjectField(required=False, write_only=True)
    poster_image = serializers.ImageField(
        required=False,
        allow_null=True,
        write_only=True,
    )
    personal_score = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=Decimal("0.0"),
        max_value=Decimal("10.0"),
        required=False,
        allow_null=True,
    )
    watch_status = serializers.ChoiceField(
        choices=UserAnimeRecord.WatchStatus.choices,
        default=UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
    )
    review = serializers.CharField(required=False, allow_blank=True, default="")
    custom_tags = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        default=list,
    )

    def validate_poster_image(self, image):
        return validate_uploaded_image(image, label="海报")

    def validate(self, attrs):
        existing_anime = attrs.get("existing_anime")
        anime_data = attrs.get("anime_data")
        poster_image = attrs.get("poster_image")

        if bool(existing_anime) == bool(anime_data):
            raise serializers.ValidationError(
                "anime_id 和 anime_data 必须且只能提供一个。"
            )
        if existing_anime and poster_image:
            raise serializers.ValidationError(
                {"poster_image": "关联已有番剧时不能同时上传新海报。"}
            )

        request = self.context["request"]
        if existing_anime and UserAnimeRecord.objects.filter(
            user=request.user,
            anime=existing_anime,
        ).exists():
            raise serializers.ValidationError(
                {"anime_id": "这部番剧已经在你的手账中。"}
            )

        if anime_data:
            payload = dict(anime_data)
            if poster_image:
                payload["poster_image"] = poster_image
            anime_serializer = AnimeSerializer(
                data=payload,
                context=self.context,
            )
            anime_serializer.is_valid(raise_exception=True)
            attrs["_anime_serializer"] = anime_serializer

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        anime_serializer = validated_data.pop("_anime_serializer", None)
        validated_data.pop("anime_data", None)
        validated_data.pop("poster_image", None)
        anime = validated_data.pop("existing_anime", None)
        if anime is None:
            anime = anime_serializer.save()

        record = UserAnimeRecord.objects.create(
            user=request.user,
            anime=anime,
            **validated_data,
        )
        initialize_record_tag_colors(record=record, user=request.user)
        return record
