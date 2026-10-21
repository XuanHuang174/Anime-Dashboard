from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.conf import settings

from .taxonomy import validate_preference_tags
from .profile_policy import PROFILE_POLICY_VERSION, PROFILE_SCORE_OPTIONS, validate_profile_min_score
from decimal import Decimal


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
    """Own explicit tags and sampling setting; no journal copies or NSFW switch."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='recommendation_preference', verbose_name='所属用户',
    )
    tags = models.JSONField('明确选择的题材', default=list, blank=True,
                            validators=[validate_preference_tags])
    profile_min_score = models.DecimalField('画像取样门槛', max_digits=3, decimal_places=1,
        default=Decimal('8.5'), db_default=Decimal('8.5'), validators=[validate_profile_min_score])
    profile_policy_version = models.CharField('画像取样规则版本', max_length=48,
        default=PROFILE_POLICY_VERSION, db_default=PROFILE_POLICY_VERSION, editable=False)
    version = models.PositiveIntegerField('偏好版本', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '用户推荐偏好'
        verbose_name_plural = '用户推荐偏好'
        constraints = [models.CheckConstraint(condition=models.Q(profile_min_score__in=PROFILE_SCORE_OPTIONS),
                                              name='rec_preference_profile_score_choices')]

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


class UserRecommendationFeedbackState(models.Model):
    """Independent, monotonic per-user feedback revision (not preference version)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='recommendation_feedback_state')
    feedback_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '用户推荐反馈版本'
        verbose_name_plural = verbose_name
        constraints = [models.CheckConstraint(condition=models.Q(feedback_version__lte=2147483647),
                                              name='rec_feedback_state_version_max')]

    def __str__(self):
        return f'用户 {self.user_id} 的反馈版本 {self.feedback_version}'


class UserRecommendationFeedback(models.Model):
    """Exact external work identity; never an Anime PK or journal record PK."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='recommendation_feedback')
    bangumi_id = models.IntegerField(validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=True)
    # Last USER revision which changed THIS work. Tombstones retain undo history.
    feedback_version = models.PositiveIntegerField()
    hidden_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '作品级推荐反馈'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=('user', 'bangumi_id'), name='rec_feedback_user_work_unique'),
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0), name='rec_feedback_positive_bgm'),
            models.CheckConstraint(condition=models.Q(feedback_version__gte=1, feedback_version__lte=2147483647),
                                   name='rec_feedback_version_range'),
            models.CheckConstraint(condition=models.Q(is_active=False) | models.Q(hidden_at__isnull=False),
                                   name='rec_feedback_active_has_time'),
        ]
        indexes = [models.Index(fields=('user', 'is_active', '-hidden_at', '-id'), name='rec_feedback_hidden_idx')]

    def __str__(self):
        return f'用户 {self.user_id} / Bangumi {self.bangumi_id}: {self.is_active}'


class RecommendationFeedbackOperation(models.Model):
    """Minimal immutable receipt: retries cannot resurrect a subsequently undone hide."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='recommendation_feedback_operations')
    operation_id = models.UUIDField()
    bangumi_id = models.IntegerField()
    action = models.CharField(max_length=8, choices=(('hide', '不感兴趣'), ('undo', '撤销')))
    request_fingerprint = models.CharField(max_length=64)
    applied_feedback_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '推荐反馈幂等回执'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=('user', 'operation_id'), name='rec_feedback_operation_unique'),
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0), name='rec_feedback_op_positive_bgm'),
            models.CheckConstraint(condition=models.Q(action__in=('hide', 'undo')), name='rec_feedback_op_action'),
            models.CheckConstraint(condition=models.Q(applied_feedback_version__gte=1,
                                                     applied_feedback_version__lte=2147483647),
                                   name='rec_feedback_op_version_range'),
        ]


class RecommendationContentEligibility(models.Model):
    """Administrative access record, NOT an age verifier or recommendation switch."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='recommendation_content_eligibility')
    status = models.CharField(max_length=12, default='pending', choices=(
        ('pending', '待审批'), ('active', '已批准（不代表已开放）'), ('rejected', '已拒绝'), ('revoked', '已撤销')))
    authorization_source = models.CharField(max_length=16, default='legacy', editable=False,
        choices=(('legacy', '历史记录（不构成人工授权）'), ('manual', '超级管理员人工授权')))
    requested_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decision_message = models.CharField(max_length=200, blank=True, default='', editable=False)
    eligibility_version = models.PositiveIntegerField(default=0, editable=False)
    purpose = models.CharField(max_length=24, default='internal_test', editable=False)
    granted_at = models.DateTimeField(null=True, blank=True, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+', editable=False)
    assurance_provider = models.CharField(max_length=64, blank=True, default='', editable=False)
    assurance_reference = models.CharField(max_length=128, blank=True, default='', editable=False)
    assurance_version = models.PositiveIntegerField(default=0, editable=False)
    assurance_valid_until = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '受限内容访问申请'
        verbose_name_plural = verbose_name
        permissions = [('manage_content_eligibility', '通过安全服务管理推荐内容访问资格')]
        constraints = [
            models.CheckConstraint(condition=models.Q(eligibility_version__lte=2147483647), name='rec_elig_version_max'),
            models.CheckConstraint(condition=(models.Q(authorization_source='legacy', purpose='internal_test')
                | models.Q(authorization_source='manual', purpose='manual_access')), name='rec_elig_source_purpose'),
            models.CheckConstraint(condition=(
                models.Q(authorization_source='legacy') & (
                models.Q(status='pending', granted_at__isnull=True, expires_at__isnull=True, revoked_at__isnull=True)
                | models.Q(status='revoked', revoked_at__isnull=False, eligibility_version__gte=1)
                | (models.Q(status='active', granted_at__isnull=False, expires_at__isnull=False,
                            revoked_at__isnull=True, assurance_valid_until__isnull=False,
                            assurance_version__gte=1, eligibility_version__gte=1)
                   & ~models.Q(assurance_provider='') & ~models.Q(assurance_reference='')
                   & models.Q(expires_at__gt=models.F('granted_at'))
                   & models.Q(expires_at__lte=models.F('assurance_valid_until'))))
                | (models.Q(authorization_source='manual', eligibility_version__gte=1,
                     assurance_provider='', assurance_reference='', assurance_version=0, assurance_valid_until__isnull=True)
                   & (models.Q(status='pending', requested_at__isnull=False, decided_at__isnull=True,
                         granted_at__isnull=True, expires_at__isnull=True, revoked_at__isnull=True)
                      | (models.Q(status='active', requested_at__isnull=False, decided_at__isnull=False,
                           granted_at__isnull=False, expires_at__isnull=False, revoked_at__isnull=True)
                         & models.Q(expires_at__gt=models.F('granted_at')))
                      | models.Q(status='rejected', requested_at__isnull=False, decided_at__isnull=False,
                           granted_at__isnull=True, expires_at__isnull=True, revoked_at__isnull=True)
                      | models.Q(status='revoked', revoked_at__isnull=False)))), name='rec_elig_state_valid'),
        ]

    def __str__(self):
        return f'用户 {self.user_id} 的访问资格 v{self.eligibility_version}'


class RecommendationEligibilityEvent(models.Model):
    """Service append-only audit/receipt; no raw identity documents or journal copies."""
    eligibility = models.ForeignKey(RecommendationContentEligibility, on_delete=models.CASCADE,
                                    related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    operation_id = models.UUIDField()
    action = models.CharField(max_length=12, choices=(('prepare', '建立待审核'), ('grant', '签发'),
        ('renew', '续期'), ('apply', '用户申请'), ('approve', '人工批准'), ('reject', '拒绝申请'),
        ('revoke', '管理员撤销'), ('self_revoke', '本人停用')))
    request_fingerprint = models.CharField(max_length=64)
    previous_status = models.CharField(max_length=16)
    resulting_status = models.CharField(max_length=12)
    previous_version = models.PositiveIntegerField()
    applied_version = models.PositiveIntegerField()
    changed = models.BooleanField()
    reason = models.CharField(max_length=200)
    resulting_expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '推荐资格操作审计'
        verbose_name_plural = verbose_name
        ordering = ('-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(fields=('eligibility', 'operation_id'), name='rec_elig_operation_unique'),
            models.CheckConstraint(condition=models.Q(action__in=('prepare', 'grant', 'renew', 'apply', 'approve', 'reject', 'revoke', 'self_revoke')),
                                   name='rec_elig_event_action'),
            models.CheckConstraint(condition=models.Q(applied_version__gte=1, applied_version__lte=2147483647)
                                   & models.Q(applied_version__gte=models.F('previous_version')),
                                   name='rec_elig_event_version'),
        ]


class RecommendationServingControl(models.Model):
    """Module 3 switch. Reading an absent singleton means disabled, never creates it."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False, editable=False)
    version = models.PositiveIntegerField(default=0, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='rec_serving_singleton'),
            models.CheckConstraint(condition=models.Q(version__lte=2147483647), name='rec_serving_version_max')]


class RecommendationServingControlEvent(models.Model):
    operation_id = models.UUIDField(unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    request_fingerprint = models.CharField(max_length=64)
    enabled = models.BooleanField()
    version = models.PositiveIntegerField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)


class RecommendationContentReviewState(models.Model):
    """Managed review epoch; GET never creates the singleton."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    version = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='rec_review_singleton'),
                       models.CheckConstraint(condition=models.Q(version__lte=2147483647), name='rec_review_epoch_max')]


class RecommendationContentReview(models.Model):
    """Internal manual review only; approval does NOT open adult serving."""
    anime = models.OneToOneField(RecommendationAnime, on_delete=models.PROTECT, related_name='content_review')
    status = models.CharField(max_length=12, default='pending', choices=(
        ('pending', '待审核'), ('approved', '审核通过（未开放）'), ('rejected', '审核拒绝'), ('revoked', '已撤销')))
    review_version = models.PositiveIntegerField(default=0, editable=False)
    policy_version = models.CharField(max_length=64, editable=False)
    catalog_revision = models.CharField(max_length=64, editable=False)
    evidence_reference = models.CharField(max_length=128, blank=True, default='', editable=False)
    exclusions_confirmed = models.BooleanField(default=False, editable=False)
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '推荐作品内容审核'
        verbose_name_plural = verbose_name
        permissions = [('manage_content_review', '通过安全服务逐条审核推荐作品')]
        constraints = [
            models.CheckConstraint(condition=models.Q(review_version__lte=2147483647), name='rec_review_version_max'),
            models.CheckConstraint(condition=(models.Q(status='pending')
                | models.Q(status__in=('rejected', 'revoked'), review_version__gte=1)
                | (models.Q(status='approved', review_version__gte=1, reviewed_at__isnull=False,
                            expires_at__isnull=False, exclusions_confirmed=True)
                   & ~models.Q(evidence_reference='') & models.Q(expires_at__gt=models.F('reviewed_at')))),
                name='rec_review_state_valid'),
        ]


class RecommendationContentReviewEvent(models.Model):
    """Append-only in service/admin; no journal or identity documents."""
    review = models.ForeignKey(RecommendationContentReview, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    operation_id = models.UUIDField()
    action = models.CharField(max_length=12, choices=(('prepare', '建立待审核'), ('approve', '审核通过'),
                                                       ('reject', '审核拒绝'), ('revoke', '撤销审核')))
    request_fingerprint = models.CharField(max_length=64)
    previous_status = models.CharField(max_length=16)
    resulting_status = models.CharField(max_length=12)
    previous_version = models.PositiveIntegerField()
    applied_version = models.PositiveIntegerField()
    applied_release_version = models.PositiveIntegerField()
    changed = models.BooleanField()
    reason = models.CharField(max_length=240)
    evidence_reference = models.CharField(max_length=128, blank=True, default='')
    catalog_revision = models.CharField(max_length=64)
    policy_version = models.CharField(max_length=64)
    resulting_expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '推荐作品审核审计'
        verbose_name_plural = verbose_name
        ordering = ('-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(fields=('review', 'operation_id'), name='rec_review_operation_unique'),
            models.CheckConstraint(condition=models.Q(action__in=('prepare', 'approve', 'reject', 'revoke')), name='rec_review_event_action'),
            models.CheckConstraint(condition=models.Q(applied_version__gte=1, applied_version__lte=2147483647,
                                                      applied_release_version__gte=1, applied_release_version__lte=2147483647)
                & models.Q(applied_version__gte=models.F('previous_version')), name='rec_review_event_version'),
        ]
