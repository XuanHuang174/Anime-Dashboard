from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.conf import settings

from .taxonomy import validate_preference_tags


class RecommendationAnime(models.Model):
    """Independent source catalog; never stores or rewrites private journal data."""

    bangumi_id = models.IntegerField(
        'Bangumi ID', unique=True, db_index=True, null=True, blank=True,
        validators=[MinValueValidator(1)],
    )
    title_zh = models.CharField('中文片名', max_length=500, db_index=True)
    title_jp = models.CharField('原名', max_length=500, blank=True, default='')
    score = models.FloatField(
        '综合评分', default=0.0, db_index=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    tags = models.CharField('标签', max_length=4000, blank=True, default='')
    cover_url = models.URLField('封面直链', max_length=2000, blank=True, default='')
    # Essential for incremental imports to retain a successfully hosted cover.
    source_cover_url = models.URLField(
        '原始封面来源', max_length=2000, blank=True, default='', editable=False,
    )
    season = models.CharField('放送季度', max_length=32, blank=True, default='', db_index=True)
    status_type = models.CharField('作品类型', max_length=32, blank=True, default='')
    synopsis = models.TextField('简介', blank=True, default='')
    nsfw = models.BooleanField('成人内容标记', null=True, blank=True, default=None, db_index=True)
    created_at = models.DateTimeField('入库时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '推荐番剧'
        verbose_name_plural = '推荐番剧库'
        ordering = ['-score', 'bangumi_id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(score__gte=0, score__lte=10),
                name='rec_anime_score_range',
            ),
            models.CheckConstraint(
                condition=models.Q(bangumi_id__isnull=True) | models.Q(bangumi_id__gt=0),
                name='rec_anime_positive_bgm_id',
            ),
        ]

    def __str__(self):
        return f'{self.title_zh} (Bangumi: {self.bangumi_id or "未关联"})'


class RecommendationPreference(models.Model):
    """Explicit choices only. No journal copies, inferred scores or NSFW switch."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='recommendation_preference', verbose_name='所属用户',
    )
    tags = models.JSONField('明确选择的题材', default=list, blank=True,
                            validators=[validate_preference_tags])
    version = models.PositiveIntegerField('偏好版本', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '用户推荐偏好'
        verbose_name_plural = '用户推荐偏好'

    def __str__(self):
        return f'用户 {self.user_id} 的推荐偏好（版本 {self.version}）'


class RecommendationRecordIdentity(models.Model):
    """Private alias: many legacy records may reference one canonical work."""
    record = models.OneToOneField('anime.UserAnimeRecord', on_delete=models.CASCADE,
                                  related_name='recommendation_identity')
    catalog_anime = models.ForeignKey(RecommendationAnime, on_delete=models.PROTECT,
                                      related_name='record_identities')
    source = models.CharField(max_length=24, choices=(('auto_strict', '严格匹配'),
                             ('user_confirmed', '用户确认'), ('import_verified', '已验证备份')))
    record_fingerprint = models.CharField(max_length=64)
    catalog_fingerprint = models.CharField(max_length=64)
    matching_version = models.CharField(max_length=48)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '私人番剧身份关联'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'记录 {self.record_id} → 目录 {self.catalog_anime_id}'


class RecommendationIdentityEvent(models.Model):
    """No titles, ratings or reviews in audit payload; rollback never deletes rows."""
    record = models.ForeignKey('anime.UserAnimeRecord', on_delete=models.CASCADE,
                               related_name='recommendation_identity_events')
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)
    action = models.CharField(max_length=16, choices=(('linked', '关联'), ('rollback', '撤销')))
    previous = models.JSONField(default=dict)
    applied_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'pk')
        verbose_name = '番剧身份关联审计'
        verbose_name_plural = verbose_name
