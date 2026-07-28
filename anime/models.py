from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def validate_tag_list(value):
    """Ensure JSON tag fields contain only non-empty strings."""
    if not isinstance(value, list):
        raise ValidationError("标签必须是字符串数组。")

    if any(not isinstance(tag, str) or not tag.strip() for tag in value):
        raise ValidationError("每个标签都必须是非空字符串。")


TAG_COLOR_KEYS = {
    "pink", "rose", "blue", "emerald", "amber", "orange", "indigo",
    "violet", "fuchsia", "yellow", "purple", "cyan", "lime", "sky",
    "slate",
}


class UserProfile(models.Model):
    """Display information owned by exactly one authentication user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="profile",
    )
    nickname = models.CharField("昵称", max_length=50, blank=True, default="")
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

    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = "用户资料"

    def __str__(self):
        return self.nickname or self.user.get_username()


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
        help_text="下架后不会出现在公共番剧搜索中，也不能再被用户添加。",
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
            "base_tags": list(self.private_base_tags or []),
        }

    def save(self, *args, **kwargs):
        if self._state.adding and not self.metadata_initialized and self.anime_id:
            self.initialize_private_metadata()
        return super().save(*args, **kwargs)


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
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "name"),
                name="unique_user_quick_filter_name",
            ),
        ]

    def __str__(self):
        return f"{self.user.get_username()} - {self.name}"
