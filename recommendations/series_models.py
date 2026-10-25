"""12B independent public-source storage; append-only observations and decisions."""
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class SeriesReminderState(models.Model):
    """Own presentation choices only; independent of ranking and journal records."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    collapsed = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=0)


class SeriesReminderDismissal(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    bangumi_id = models.PositiveIntegerField()
    hidden = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('user', 'bangumi_id'), name='series_reminder_user_work'),
                       models.CheckConstraint(condition=models.Q(bangumi_id__gt=0, bangumi_id__lte=2147483647), name='series_reminder_id_range')]


class SeriesState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveBigIntegerField(default=0)
    latest_source_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='series_single_state')]


class SeriesNode(models.Model):
    bangumi_id = models.IntegerField(primary_key=True, validators=[MinValueValidator(1), MaxValueValidator(2147483647)])
    subject_type = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=500, blank=True)
    original_title = models.CharField(max_length=500, blank=True)
    release_date = models.CharField(max_length=10, blank=True)
    release_format = models.CharField(max_length=32, blank=True)
    nsfw = models.BooleanField(null=True)
    metadata_revision = models.CharField(max_length=64)

    class Meta:
        verbose_name = '系列公共节点'
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0), name='series_node_positive_id'),
            models.CheckConstraint(condition=models.Q(subject_type__in=(1, 2, 3, 4, 6)), name='series_node_type'),
        ]


class SeriesObservation(models.Model):
    source = models.ForeignKey(SeriesNode, on_delete=models.PROTECT, related_name='observations')
    snapshot_version = models.CharField(max_length=64)
    source_at = models.DateTimeField()
    observed_at = models.DateTimeField()
    source_reference = models.CharField(max_length=500)
    archive_sha256 = models.CharField(max_length=64)
    payload_sha256 = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    mapping_version = models.CharField(max_length=64)
    coverage = models.CharField(max_length=20)
    diagnostics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '系列来源观察'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=('source', 'snapshot_version'), name='series_observation_once'),
            models.CheckConstraint(condition=models.Q(coverage__in=('observed', 'empty_observed', 'partial', 'failed')),
                                   name='series_observation_coverage'),
        ]


class SeriesHead(models.Model):
    source = models.OneToOneField(SeriesNode, primary_key=True, on_delete=models.PROTECT, related_name='series_head')
    current = models.ForeignKey(SeriesObservation, null=True, on_delete=models.PROTECT, related_name='+')
    last_attempt = models.ForeignKey(SeriesObservation, null=True, on_delete=models.PROTECT, related_name='+')


class SeriesAssertion(models.Model):
    observation = models.ForeignKey(SeriesObservation, on_delete=models.PROTECT, related_name='assertions')
    target = models.ForeignKey(SeriesNode, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=32)
    source_relation = models.PositiveIntegerField()
    source_order = models.PositiveIntegerField()
    requires_review = models.BooleanField(default=False)

    class Meta:
        verbose_name = '系列关系断言'
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=('observation', 'target', 'source_relation'), name='series_assertion_once')]


class SeriesOverrideEvent(models.Model):
    source = models.ForeignKey(SeriesNode, on_delete=models.PROTECT, related_name='override_events')
    target = models.ForeignKey(SeriesNode, on_delete=models.PROTECT, related_name='+')
    base_observation = models.ForeignKey(SeriesObservation, on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    action = models.CharField(max_length=12)
    kind = models.CharField(max_length=32, blank=True)
    reason = models.CharField(max_length=240)
    evidence_reference = models.CharField(max_length=500)
    operation_id = models.UUIDField(unique=True)
    request_fingerprint = models.CharField(max_length=64)
    applied_revision = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '系列人工修订审计'
        verbose_name_plural = verbose_name
        permissions = [('review_series_relations', '可核验和修订系列关系')]
        ordering = ('-pk',)
        constraints = [models.CheckConstraint(
            condition=(models.Q(action='replace') & ~models.Q(kind=''))
                | models.Q(action__in=('suppress', 'clear'), kind=''), name='series_override_action')]
