"""13B immutable public snapshots; no catalog or journal ownership writes."""
from django.db import models


class CreditsState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='credits_single_state')]


class CreditsSnapshot(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    snapshot_version = models.CharField(max_length=64)
    source_at = models.DateTimeField()
    source_reference = models.CharField(max_length=500)
    archive_sha256 = models.CharField(max_length=64)
    member_hashes = models.JSONField()
    mapping_version = models.CharField(max_length=64)
    alias_version = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('snapshot_version', 'mapping_version', 'alias_version'), name='credits_snapshot_once')]


class CreditsSubject(models.Model):
    snapshot = models.ForeignKey(CreditsSnapshot, on_delete=models.PROTECT)
    bangumi_id = models.PositiveIntegerField(db_index=True)
    subject_type = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=500)
    nsfw = models.BooleanField(null=True)
    source_revision = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('snapshot', 'bangumi_id'), name='credits_subject_once'),
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0, bangumi_id__lte=2147483647), name='credits_subject_id'),
            models.CheckConstraint(condition=models.Q(subject_type__in=(1, 2, 3, 4, 6)), name='credits_subject_type')]


class CreditsPerson(models.Model):
    snapshot = models.ForeignKey(CreditsSnapshot, on_delete=models.PROTECT)
    person_id = models.PositiveIntegerField(db_index=True)
    person_type = models.PositiveSmallIntegerField(db_index=True)
    name = models.CharField(max_length=500)
    careers = models.JSONField(default=list)
    alias_status = models.CharField(max_length=12)
    alias_issues = models.JSONField(default=list)
    source_revision = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('snapshot', 'person_id'), name='credits_person_once'),
            models.CheckConstraint(condition=models.Q(person_id__gt=0, person_id__lte=2147483647), name='credits_person_id'),
            models.CheckConstraint(condition=models.Q(person_type__in=(1, 2, 3)), name='credits_person_type')]


class CreditsCharacter(models.Model):
    snapshot = models.ForeignKey(CreditsSnapshot, on_delete=models.PROTECT)
    character_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=500)
    entity_role = models.PositiveIntegerField()
    source_revision = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('snapshot', 'character_id'), name='credits_character_once'),
            models.CheckConstraint(condition=models.Q(character_id__gt=0, character_id__lte=2147483647), name='credits_character_id')]


class CreditsAlias(models.Model):
    person = models.ForeignKey(CreditsPerson, on_delete=models.PROTECT, related_name='aliases')
    name = models.CharField(max_length=500)
    search_key = models.CharField(max_length=500, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('person', 'name'), name='credits_alias_once')]


class CreditsObservation(models.Model):
    snapshot = models.ForeignKey(CreditsSnapshot, on_delete=models.PROTECT)
    bangumi_id = models.PositiveIntegerField(db_index=True)
    kind = models.CharField(max_length=12)
    coverage = models.CharField(max_length=20)
    fingerprint = models.CharField(max_length=64)
    diagnostics = models.JSONField(default=dict)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('snapshot', 'bangumi_id', 'kind'), name='credits_observation_once'),
            models.CheckConstraint(condition=models.Q(kind__in=('studio', 'voice_actor')), name='credits_observation_kind'),
            models.CheckConstraint(condition=models.Q(coverage__in=('observed', 'empty_observed', 'partial', 'failed')), name='credits_observation_status'),
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0, bangumi_id__lte=2147483647), name='credits_observation_id')]


class CreditsHead(models.Model):
    bangumi_id = models.PositiveIntegerField(db_index=True)
    kind = models.CharField(max_length=12)
    current = models.ForeignKey(CreditsObservation, null=True, on_delete=models.PROTECT, related_name='+')
    last_attempt = models.ForeignKey(CreditsObservation, null=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('bangumi_id', 'kind'), name='credits_head_once'),
            models.CheckConstraint(condition=models.Q(kind__in=('studio', 'voice_actor')), name='credits_head_kind'),
            models.CheckConstraint(condition=models.Q(bangumi_id__gt=0, bangumi_id__lte=2147483647), name='credits_head_id')]


class CreditsAssertion(models.Model):
    observation = models.ForeignKey(CreditsObservation, on_delete=models.PROTECT, related_name='assertions')
    person = models.ForeignKey(CreditsPerson, null=True, on_delete=models.PROTECT)
    character = models.ForeignKey(CreditsCharacter, null=True, on_delete=models.PROTECT)
    source_person_id = models.PositiveIntegerField(db_index=True)
    source_character_id = models.PositiveIntegerField(default=0)
    source_code = models.PositiveIntegerField()
    role_type = models.PositiveIntegerField(null=True)
    source_order = models.PositiveIntegerField(null=True)
    source_note = models.CharField(max_length=2000, blank=True)
    source_eligible = models.BooleanField(default=False, db_index=True)
    review_reasons = models.JSONField(default=list)
    annotation_warnings = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('observation', 'source_person_id', 'source_character_id', 'source_code'), name='credits_assertion_once'),
            models.CheckConstraint(condition=models.Q(source_person_id__gt=0, source_person_id__lte=2147483647), name='credits_assertion_person')]
        indexes = [models.Index(fields=('person', 'source_eligible', 'observation'), name='credits_person_eligible_idx')]
