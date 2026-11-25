from decimal import Decimal
import uuid
from .rating_config import default_rating_preferences, validate_rating_preferences

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone


def validate_tag_list(value):
    """Ensure JSON tag fields contain only non-empty strings."""
    if not isinstance(value, list):
        raise ValidationError("标签必须是字符串数组。")

    if any(not isinstance(tag, str) or not tag.strip() for tag in value):
        raise ValidationError("每个标签都必须是非空字符串。")


def validate_watch_statuses(value):
    if not isinstance(value, list) or any(status not in ('plan', 'watching', 'completed', 'on_hold') for status in value) or len(set(value)) != len(value):
        raise ValidationError('观看状态必须是不重复的有效状态数组。')


TAG_COLOR_KEYS = {
    "pink", "rose", "blue", "emerald", "amber", "orange", "indigo",
    "violet", "fuchsia", "yellow", "purple", "cyan", "lime", "sky",
    "slate",
}


class TagCleanupJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tag_cleanup_jobs')
    request_id = models.UUIDField()
    status = models.CharField(max_length=16, default='scanning')
    scope = models.CharField(max_length=16)
    mode = models.CharField(max_length=16, default='conservative')
    keep_personal = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=0)
    filters = models.JSONField(default=dict)
    record_ids = models.JSONField(default=list)
    selection = models.JSONField(default=list)
    scan_cursor = models.PositiveIntegerField(default=0)
    apply_cursor = models.PositiveIntegerField(default=0)
    undo_cursor = models.PositiveIntegerField(default=0)
    rule_version = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'request_id'), name='tag_cleanup_request_once')]
        indexes = [models.Index(fields=('user', '-created_at'), name='tag_cleanup_owner_created')]


class TagCleanupItem(models.Model):
    job = models.ForeignKey(TagCleanupJob, on_delete=models.CASCADE, related_name='items')
    record_id = models.PositiveBigIntegerField()
    title = models.CharField(max_length=200, blank=True)
    before = models.JSONField(default=list)
    after = models.JSONField(default=list)
    changes = models.JSONField(default=list)
    protected = models.JSONField(default=list)
    uncertain = models.JSONField(default=list)
    details = models.JSONField(default=dict)
    personal_removed = models.PositiveIntegerField(default=0)
    source_note = models.CharField(max_length=200, blank=True)
    before_hash = models.CharField(max_length=64, blank=True)
    plan_hash = models.CharField(max_length=64, blank=True)
    after_hash = models.CharField(max_length=64, blank=True)
    protection_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, default='proposed')
    message = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('job', 'record_id'), name='tag_cleanup_record_once')]
        ordering = ('record_id',)


class UserProfile(models.Model):
    """Display information owned by exactly one authentication user."""

    class PublicStatus(models.TextChoices):
        PRIVATE = "private", "私密"
        PENDING = "pending", "审核中"
        APPROVED = "approved", "已公开"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="profile",
    )
    nickname = models.CharField("昵称", max_length=50, blank=True, default="")
    tag_presets = models.JSONField("个人常用标签", default=list, blank=True, validators=[validate_tag_list])
    rating_preferences = models.JSONField('评分展示与统计', default=default_rating_preferences, validators=[validate_rating_preferences])
    rating_revision = models.PositiveBigIntegerField(default=0)
    showcase_subtitle = models.CharField(
        "展示页副标题",
        max_length=200,
        blank=True,
        default="",
    )
    avatar = models.ImageField(
        "头像",
        upload_to="users/avatars/%Y/%m/",
        blank=True,
        null=True,
    )
    max_featured_limit = models.PositiveSmallIntegerField(
        "精选专栏收录上限",
        default=3,
        validators=[MinValueValidator(0)],
        help_text="该用户最多可同时拥有的已收录精选专栏数量。",
    )
    public_status = models.CharField(
        "共创公开状态",
        max_length=16,
        choices=PublicStatus.choices,
        default=PublicStatus.PRIVATE,
        db_index=True,
    )
    is_public = models.BooleanField(
        "公开手账",
        default=False,
        db_index=True,
        help_text="仅管理员审核通过的手账可以进入番剧共创大厅。",
    )
    is_push_enabled = models.BooleanField(
        "新番推送通知",
        default=False,
        db_index=True,
        help_text="每天晚上八点接收标记为在看的新番更新邮件。",
    )
    last_push_date = models.DateField(
        "最后推送日期",
        null=True,
        blank=True,
        editable=False,
        help_text="用于阻止外部 Cron 在同一天重复发送。",
    )

    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = "用户资料"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_public=False)
                    | models.Q(public_status="approved")
                ),
                name="public_profile_requires_approval",
            ),
        ]

    def __str__(self):
        return self.nickname or self.user.get_username()

    def save(self, *args, **kwargs):
        """Keep the public flag derived from the administrator-owned status."""

        self.is_public = self.public_status == self.PublicStatus.APPROVED
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = tuple(set(update_fields) | {"is_public"})
        return super().save(*args, **kwargs)


def validate_tag_color_map(value):
    """Ensure tag color overrides are safe predefined palette keys."""
    if not isinstance(value, dict):
        raise ValidationError("标签颜色必须是对象。")
    if any(
        not isinstance(tag, str)
        or not tag.strip()
        or color not in TAG_COLOR_KEYS
        for tag, color in value.items()
    ):
        raise ValidationError("标签颜色包含无效的标签名或颜色方案。")


class Anime(models.Model):
    """Shared, user-independent anime metadata."""

    class HiddenReason(models.TextChoices):
        PRIVATE_ONLY = "private_only", "仅私人资料"
        SHOWCASE_REMOVED = "showcase_removed", "主理人曾移除"
        ADMIN_RETIRED = "admin_retired", "管理员下架"
        LEGACY_UNKNOWN = "legacy_unknown", "历史原因不明"

    bgm_id = models.PositiveIntegerField(
        "Bangumi 条目 ID",
        null=True,
        blank=True,
        unique=True,
        help_text="Bangumi subject ID；旧数据允许为空。",
    )
    chinese_title = models.CharField("中文名", max_length=200, db_index=True)
    japanese_title = models.CharField(
        "日文名",
        max_length=200,
        blank=True,
        db_index=True,
    )
    airing_period = models.CharField(
        "放送季度",
        max_length=32,
        db_index=True,
        help_text="例如：2026-1、2025-10、未定档。",
    )
    studio = models.CharField("制作公司", max_length=200, blank=True)
    episodes = models.CharField(
        "集数",
        max_length=50,
        blank=True,
        help_text="支持 12、12+1、未公布等格式。",
    )
    description = models.TextField("剧情简介", blank=True)
    poster_url = models.URLField("海报图 URL", max_length=1000, blank=True)
    poster_image = models.ImageField(
        "上传海报",
        upload_to="anime/posters/%Y/%m/",
        blank=True,
        null=True,
    )
    baike_url = models.URLField("萌百链接", max_length=1000, blank=True)
    base_tags = models.JSONField(
        "基础标签",
        default=list,
        blank=True,
        validators=[validate_tag_list],
        help_text='JSON 字符串数组，例如：["轻百", "日常", "治愈"]。',
    )
    tag_colors = models.JSONField(
        "标签配色",
        default=dict,
        blank=True,
        validators=[validate_tag_color_map],
        help_text='标签到预设颜色的映射，例如：{"日常": "blue"}。',
    )
    is_active = models.BooleanField(
        "在公共库中可用",
        default=True,
        db_index=True,
        help_text="关闭后不出现在公共搜索；不代表禁止通过安全的确切身份加入私人手账。",
    )
    hidden_reason = models.CharField(
        "公共隐藏原因",
        max_length=32,
        choices=HiddenReason.choices,
        default=HiddenReason.LEGACY_UNKNOWN,
        db_default=HiddenReason.LEGACY_UNKNOWN,
        blank=True,
        help_text="仅在非公开时有意义；历史隐藏资料默认待审核，不能自动恢复公开。",
    )
    created_at = models.DateTimeField(
        "创建时间",
        auto_now_add=True,
        db_index=True,
    )
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "公共番剧"
        verbose_name_plural = "公共番剧资料库"
        ordering = ("-airing_period", "chinese_title")
        constraints = [
            models.UniqueConstraint(
                fields=("chinese_title", "japanese_title", "airing_period"),
                name="unique_anime_release",
            ),
        ]
        indexes = [
            models.Index(fields=("studio",), name="anime_studio_idx"),
        ]

    def __str__(self):
        return f"{self.chinese_title}（{self.airing_period}）"


class UserAnimeRecord(models.Model):
    """A user's private tracking and review data for an anime."""

    class WatchStatus(models.TextChoices):
        PLAN_TO_WATCH = "plan", "想看"
        WATCHING = "watching", "在看"
        COMPLETED = "completed", "看过"
        ON_HOLD = "on_hold", "搁置"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="anime_records",
        db_index=True,
    )
    anime = models.ForeignKey(
        Anime,
        verbose_name="番剧",
        on_delete=models.PROTECT,
        related_name="user_records",
    )
    metadata_initialized = models.BooleanField(
        "已建立私人资料快照",
        default=False,
        db_index=True,
    )
    private_chinese_title = models.CharField(
        "私人中文名",
        max_length=200,
        blank=True,
        default="",
    )
    private_japanese_title = models.CharField(
        "私人日文名",
        max_length=200,
        blank=True,
        default="",
    )
    private_airing_period = models.CharField(
        "私人放送季度",
        max_length=32,
        blank=True,
        default="",
    )
    private_studio = models.CharField(
        "私人制作公司",
        max_length=200,
        blank=True,
        default="",
    )
    private_episodes = models.CharField(
        "私人话数情况",
        max_length=50,
        blank=True,
        default="",
    )
    private_description = models.TextField(
        "私人剧情简介",
        blank=True,
        default="",
    )
    private_poster_url = models.URLField(
        "私人海报 URL",
        max_length=1000,
        blank=True,
        default="",
    )
    private_poster_image = models.ImageField(
        "私人上传海报",
        upload_to="records/posters/%Y/%m/",
        blank=True,
        null=True,
    )
    private_baike_url = models.URLField(
        "私人萌百链接",
        max_length=1000,
        blank=True,
        default="",
    )
    private_bangumi_url = models.URLField('私人 Bangumi 条目链接', max_length=1000, blank=True, default='')
    verified_bangumi_id = models.PositiveIntegerField('已核验的录入来源 ID', null=True, blank=True, editable=False)
    private_base_tags = models.JSONField(
        "私人基础标签",
        default=list,
        blank=True,
        validators=[validate_tag_list],
    )
    personal_score = models.DecimalField(
        "个人评分",
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.0")),
            MaxValueValidator(Decimal("10.0")),
        ],
        help_text="0.0–10.0，可暂不评分。",
    )
    watch_status = models.CharField(
        "观看状态",
        max_length=16,
        choices=WatchStatus.choices,
        default=WatchStatus.PLAN_TO_WATCH,
        db_index=True,
    )
    watched_eps = models.PositiveIntegerField("已观看集数", default=0)
    total_eps = models.PositiveIntegerField("总集数", default=0)
    watch_revision = models.PositiveIntegerField('观看历程版本', default=0, editable=False)
    watch_history_base = models.PositiveIntegerField(default=0, editable=False)
    watch_clear_operation = models.UUIDField(null=True, editable=False)
    watch_clear_hash = models.CharField(max_length=64, default='', editable=False)
    review = models.TextField("个人长评", blank=True)
    custom_tags = models.JSONField(
        "自定义标签",
        default=list,
        blank=True,
        validators=[validate_tag_list],
        help_text='仅当前用户可见，例如：["年度最佳", "二刷"]。',
    )
    created_at = models.DateTimeField(
        "创建时间",
        auto_now_add=True,
        db_index=True,
    )
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "用户追番记录"
        verbose_name_plural = "用户私人追番手账"
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "anime"),
                name="unique_user_anime_record",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "watch_status"),
                name="record_user_status_idx",
            ),
            models.Index(
                fields=("user", "-updated_at"),
                name="record_user_updated_idx",
            ),
        ]

    def __str__(self):
        title = (
            self.private_chinese_title
            if self.metadata_initialized
            else self.anime.chinese_title
        )
        return f"{self.user.get_username()} - {title}"

    def initialize_private_metadata(self, *, force=False):
        """Copy shared library metadata into this user's isolated snapshot."""

        if self.metadata_initialized and not force:
            return
        anime = self.anime
        self.private_chinese_title = anime.chinese_title
        self.private_japanese_title = anime.japanese_title
        self.private_airing_period = anime.airing_period
        self.private_studio = anime.studio
        self.private_episodes = anime.episodes
        self.private_description = anime.description
        self.private_poster_url = anime.poster_url
        self.private_poster_image.name = (
            anime.poster_image.name if anime.poster_image else None
        )
        self.private_baike_url = anime.baike_url
        self.private_base_tags = list(anime.base_tags or [])
        self.metadata_initialized = True

    def effective_anime_metadata(self):
        """Return the metadata visible only inside this user's journal."""

        if not self.metadata_initialized:
            return {
                "chinese_title": self.anime.chinese_title,
                "japanese_title": self.anime.japanese_title,
                "airing_period": self.anime.airing_period,
                "studio": self.anime.studio,
                "episodes": self.anime.episodes,
                "description": self.anime.description,
                "poster_url": self.anime.poster_url,
                "poster_image": self.anime.poster_image,
                "baike_url": self.anime.baike_url,
                "bangumi_url": self.private_bangumi_url,
                "base_tags": list(self.anime.base_tags or []),
            }
        return {
            "chinese_title": self.private_chinese_title,
            "japanese_title": self.private_japanese_title,
            "airing_period": self.private_airing_period,
            "studio": self.private_studio,
            "episodes": self.private_episodes,
            "description": self.private_description,
            "poster_url": self.private_poster_url,
            "poster_image": self.private_poster_image,
            "baike_url": self.private_baike_url,
            "bangumi_url": self.private_bangumi_url,
            "base_tags": list(self.private_base_tags or []),
        }

    def save(self, *args, **kwargs):
        if self._state.adding and not self.metadata_initialized and self.anime_id:
            self.initialize_private_metadata()
        fields = kwargs.get('update_fields')
        if self._state.adding or fields is not None and not set(fields).intersection(('watch_status', 'watched_eps', 'total_eps')):
            return super().save(*args, **kwargs)
        # All ordinary saves (API, progress, administration, collection import)
        # commit the current values and their historical event together.
        with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
            before = type(self).objects.select_for_update().get(pk=self.pk)
            self.watch_revision = before.watch_revision
            self.watch_history_base = before.watch_history_base
            self.watch_clear_operation = before.watch_clear_operation
            self.watch_clear_hash = before.watch_clear_hash
            result = super().save(*args, **kwargs)
            from .watch_history import observe_change
            persisted = type(self).objects.get(pk=self.pk)
            observe_change(before, persisted)
            self.watch_revision = persisted.watch_revision
            return result


class WatchRound(models.Model):
    record = models.ForeignKey(UserAnimeRecord, on_delete=models.CASCADE, related_name='watch_rounds')
    number = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    voided = models.BooleanField(default=False)
    origin = models.CharField(max_length=16, default='baseline')
    status = models.CharField(max_length=16, choices=UserAnimeRecord.WatchStatus.choices)
    watched_eps = models.PositiveIntegerField(default=0)
    total_eps = models.PositiveIntegerField(default=0)
    started_on = models.CharField(max_length=10, blank=True, default='')
    completed_on = models.CharField(max_length=10, blank=True, default='')
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)])
    rating_origin = models.CharField(max_length=24, blank=True, default='')
    note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ('number',)
        constraints = [
            models.UniqueConstraint(fields=('record', 'number'), name='watch_round_number'),
            models.UniqueConstraint(fields=('record',), condition=models.Q(is_current=True), name='watch_round_current'),
            models.CheckConstraint(condition=models.Q(number__gte=1), name='watch_round_positive_number'),
            models.CheckConstraint(condition=models.Q(total_eps=0) | models.Q(watched_eps__lte=models.F('total_eps')), name='watch_round_progress_bound'),
            models.CheckConstraint(condition=models.Q(rating__isnull=True) | models.Q(rating__gte=0, rating__lte=10), name='watch_round_rating_bound'),
        ]


class WatchEvent(models.Model):
    record = models.ForeignKey(UserAnimeRecord, on_delete=models.CASCADE, related_name='watch_events')
    revision = models.PositiveIntegerField()
    kind = models.CharField(max_length=24)
    source = models.CharField(max_length=24, default='record_update')
    occurred_on = models.CharField(max_length=10, blank=True, default='')
    note = models.TextField(blank=True, default='')
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    operation_id = models.UUIDField(null=True, blank=True)
    request_hash = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ('revision',)
        constraints = [
            models.UniqueConstraint(fields=('record', 'revision'), name='watch_event_revision'),
            models.UniqueConstraint(fields=('record', 'operation_id'), name='watch_event_operation'),
        ]


class UserAnimeTag(models.Model):
    """A tag color owned by one user's private anime record."""

    record = models.ForeignKey(
        UserAnimeRecord,
        verbose_name="私人手账记录",
        on_delete=models.CASCADE,
        related_name="tag_preferences",
    )
    name = models.CharField("标签名称", max_length=50)
    color = models.CharField(
        "标签配色",
        max_length=16,
        choices=tuple((key, key) for key in sorted(TAG_COLOR_KEYS)),
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "用户番剧标签"
        verbose_name_plural = "用户私人标签配色"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("record", "name"),
                name="unique_record_tag_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=("record", "name"),
                name="record_tag_lookup_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.record.user.get_username()} - "
            f"{self.record.anime.chinese_title} - {self.name}"
        )


class FeaturedSubmission(models.Model):
    """A user-authored snapshot submitted to the curated featured column."""
    bangumi_url = models.URLField('Bangumi 条目链接快照', max_length=1000, blank=True, default='')
    rating_preferences = models.JSONField('投稿评分展示快照', default=default_rating_preferences, validators=[validate_rating_preferences])

    class AuditStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已收录"
        REJECTED = "rejected", "未获收录"
        REMOVAL_REQUESTED = "removal_requested", "请求撤下"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="投稿用户",
        on_delete=models.CASCADE,
        related_name="featured_submissions",
        db_index=True,
    )
    source_anime = models.ForeignKey(
        Anime,
        verbose_name="来源番剧",
        on_delete=models.SET_NULL,
        related_name="featured_submissions",
        null=True,
        blank=True,
        db_index=True,
        help_text="仅用于关联用户手账中的原番剧；专栏展示内容仍使用独立快照。",
    )
    source_record = models.ForeignKey(
        UserAnimeRecord,
        verbose_name="来源私人记录",
        on_delete=models.CASCADE,
        related_name="featured_submissions",
        null=True,
        blank=True,
        db_index=True,
        help_text="删除该用户的私人手账记录时，同步删除由它产生的精选专栏内容。",
    )
    chinese_title = models.CharField("中文名", max_length=200, db_index=True)
    japanese_title = models.CharField("日文名", max_length=200, blank=True)
    year = models.PositiveSmallIntegerField(
        "年份",
        null=True,
        blank=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2200)],
        db_index=True,
    )
    airing_period = models.CharField(
        "放送季度",
        max_length=32,
        blank=True,
        default="",
        help_text="投稿时从公共番剧资料复制的完整季度快照，例如：2026-1。",
    )
    studio = models.CharField("制作公司", max_length=200, blank=True, default="")
    episodes = models.CharField("话数情况", max_length=50, blank=True, default="")
    description = models.TextField("剧情简介", blank=True, default="")
    baike_url = models.URLField(
        "萌百链接",
        max_length=1000,
        blank=True,
        default="",
    )
    watch_status = models.CharField(
        "观看状态",
        max_length=16,
        choices=UserAnimeRecord.WatchStatus.choices,
        default=UserAnimeRecord.WatchStatus.PLAN_TO_WATCH,
        db_index=True,
    )
    score = models.DecimalField(
        "评分",
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.0")),
            MaxValueValidator(Decimal("10.0")),
        ],
    )
    review = models.TextField("长评文本", blank=True)
    poster = models.ImageField(
        "海报图",
        upload_to="featured/posters/%Y/%m/",
        blank=True,
        null=True,
    )
    poster_url = models.URLField("海报图 URL", max_length=1000, blank=True)
    tags = models.JSONField(
        "标签列表",
        default=list,
        blank=True,
        validators=[validate_tag_list],
    )
    tag_colors = models.JSONField(
        "标签配色快照",
        default=dict,
        blank=True,
        validators=[validate_tag_color_map],
    )
    audit_status = models.CharField(
        "审核状态",
        max_length=24,
        choices=AuditStatus.choices,
        default=AuditStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "精选专栏投稿"
        verbose_name_plural = "精选专栏投稿"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("user", "audit_status"),
                name="featured_user_audit_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user.get_username()} - {self.chinese_title}"


class UserNotification(models.Model):
    """An in-app notification visible only to its owning user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    message = models.TextField("消息内容")
    is_read = models.BooleanField("是否已读", default=False, db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "用户通知"
        verbose_name_plural = "用户通知"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("user", "is_read", "-created_at"),
                name="notice_user_read_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user.get_username()} - {self.message[:30]}"


class UserQuickFilter(models.Model):
    """A reusable tag combination owned by one user."""

    class MatchMode(models.TextChoices):
        ANY = "any", "匹配任意标签"
        ALL = "all", "匹配全部标签"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="anime_quick_filters",
    )
    name = models.CharField("筛选名称", max_length=30)
    sort_order = models.PositiveIntegerField('显示顺序', default=0)
    year_from = models.PositiveSmallIntegerField("起始年份", null=True, blank=True, validators=[MinValueValidator(1000), MaxValueValidator(9999)])
    year_to = models.PositiveSmallIntegerField("结束年份", null=True, blank=True, validators=[MinValueValidator(1000), MaxValueValidator(9999)])
    score_min = models.DecimalField("最低个人评分", max_digits=3, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    score_max = models.DecimalField("最高个人评分", max_digits=3, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    score_state = models.CharField("评分状态", max_length=8, default='any', choices=(('any', '不限'), ('rated', '仅已评分'), ('unrated', '仅未评分')))
    watch_statuses = models.JSONField("观看状态组合", default=list, blank=True, validators=[validate_watch_statuses])
    review_state = models.CharField("长评状态", max_length=8, default='any', choices=(('any', '不限'), ('with', '有长评'), ('without', '无长评')))
    exclude_tags = models.JSONField("排除标签", default=list, blank=True, validators=[validate_tag_list])
    exclude_title_keywords = models.JSONField("排除标题词", default=list, blank=True, validators=[validate_tag_list])
    tags = models.JSONField(
        "标签组合",
        default=list,
        validators=[validate_tag_list],
    )
    title_keywords = models.JSONField(
        "标题关键字",
        default=list,
        blank=True,
        validators=[validate_tag_list],
        help_text="与标签组合共同参与匹配，例如 OVA。",
    )
    match_mode = models.CharField(
        "匹配方式",
        max_length=8,
        choices=MatchMode.choices,
        default=MatchMode.ANY,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "用户快捷筛选"
        verbose_name_plural = "用户快捷筛选"
        ordering = ('sort_order', 'pk')
        constraints = [
            models.UniqueConstraint(
                fields=("user", "name"),
                name="unique_user_quick_filter_name",
            ),
            models.CheckConstraint(condition=models.Q(year_from__isnull=True) | models.Q(year_to__isnull=True) | models.Q(year_from__lte=models.F('year_to')), name='user_quick_filter_year_order'),
            models.CheckConstraint(condition=models.Q(score_min__isnull=True) | models.Q(score_max__isnull=True) | models.Q(score_min__lte=models.F('score_max')), name='user_quick_filter_score_order'),
            models.CheckConstraint(condition=~models.Q(score_state='unrated') | (models.Q(score_min__isnull=True) & models.Q(score_max__isnull=True)), name='user_quick_filter_unrated_bounds'),
        ]

    def __str__(self):
        return f"{self.user.get_username()} - {self.name}"


class FeaturedQuickFilter(models.Model):
    """A global featured-column filter maintained by superusers."""

    class MatchMode(models.TextChoices):
        ANY = "any", "匹配任意标签"
        ALL = "all", "匹配全部标签"

    name = models.CharField("筛选名称", max_length=30, unique=True)
    sort_order = models.PositiveIntegerField('显示顺序', default=0)
    year_from = models.PositiveSmallIntegerField("起始年份", null=True, blank=True, validators=[MinValueValidator(1000), MaxValueValidator(9999)])
    year_to = models.PositiveSmallIntegerField("结束年份", null=True, blank=True, validators=[MinValueValidator(1000), MaxValueValidator(9999)])
    score_min = models.DecimalField("最低个人评分", max_digits=3, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    score_max = models.DecimalField("最高个人评分", max_digits=3, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    score_state = models.CharField("评分状态", max_length=8, default='any', choices=(('any', '不限'), ('rated', '仅已评分'), ('unrated', '仅未评分')))
    watch_statuses = models.JSONField("观看状态组合", default=list, blank=True, validators=[validate_watch_statuses])
    review_state = models.CharField("长评状态", max_length=8, default='any', choices=(('any', '不限'), ('with', '有长评'), ('without', '无长评')))
    exclude_tags = models.JSONField("排除标签", default=list, blank=True, validators=[validate_tag_list])
    exclude_title_keywords = models.JSONField("排除标题词", default=list, blank=True, validators=[validate_tag_list])
    tags = models.JSONField(
        "标签组合",
        default=list,
        validators=[validate_tag_list],
    )
    title_keywords = models.JSONField(
        "标题关键字",
        default=list,
        blank=True,
        validators=[validate_tag_list],
        help_text="与标签组合共同参与匹配，例如 OVA。",
    )
    match_mode = models.CharField(
        "匹配方式",
        max_length=8,
        choices=MatchMode.choices,
        default=MatchMode.ANY,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "精选专栏快捷筛选"
        verbose_name_plural = "精选专栏快捷筛选"
        ordering = ('sort_order', 'pk')
        constraints = [
            models.CheckConstraint(condition=models.Q(year_from__isnull=True) | models.Q(year_to__isnull=True) | models.Q(year_from__lte=models.F('year_to')), name='featured_quick_filter_year_order'),
            models.CheckConstraint(condition=models.Q(score_min__isnull=True) | models.Q(score_max__isnull=True) | models.Q(score_min__lte=models.F('score_max')), name='featured_quick_filter_score_order'),
            models.CheckConstraint(condition=~models.Q(score_state='unrated') | (models.Q(score_min__isnull=True) & models.Q(score_max__isnull=True)), name='featured_quick_filter_unrated_bounds'),
        ]

    def __str__(self):
        return self.name
