"""Additive internal review schema only: no backfill, approvals or permissions assignment."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('recommendations', '0007_content_eligibility'),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='RecommendationContentReview', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('status', models.CharField(choices=[('pending','待审核'),('approved','审核通过（未开放）'),('rejected','审核拒绝'),('revoked','已撤销')], default='pending', max_length=12)),
            ('review_version', models.PositiveIntegerField(default=0, editable=False)),
            ('policy_version', models.CharField(editable=False, max_length=64)),
            ('catalog_revision', models.CharField(editable=False, max_length=64)),
            ('evidence_reference', models.CharField(blank=True, default='', editable=False, max_length=128)),
            ('exclusions_confirmed', models.BooleanField(default=False, editable=False)),
            ('reviewed_at', models.DateTimeField(blank=True, editable=False, null=True)),
            ('expires_at', models.DateTimeField(blank=True, editable=False, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('anime', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='content_review', to='recommendations.recommendationanime')),
            ('reviewer', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
        ], options={'verbose_name':'推荐作品内容审核','verbose_name_plural':'推荐作品内容审核',
                    'permissions':[('manage_content_review','通过安全服务逐条审核推荐作品')]}),
        migrations.CreateModel(name='RecommendationContentReviewEvent', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('operation_id', models.UUIDField()),
            ('action', models.CharField(choices=[('prepare','建立待审核'),('approve','审核通过'),('reject','审核拒绝'),('revoke','撤销审核')], max_length=12)),
            ('request_fingerprint', models.CharField(max_length=64)),
            ('previous_status', models.CharField(max_length=16)),
            ('resulting_status', models.CharField(max_length=12)),
            ('previous_version', models.PositiveIntegerField()),
            ('applied_version', models.PositiveIntegerField()),
            ('applied_release_version', models.PositiveIntegerField()),
            ('changed', models.BooleanField()),
            ('reason', models.CharField(max_length=240)),
            ('evidence_reference', models.CharField(blank=True, default='', max_length=128)),
            ('catalog_revision', models.CharField(max_length=64)),
            ('policy_version', models.CharField(max_length=64)),
            ('resulting_expires_at', models.DateTimeField(null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ('review', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='events', to='recommendations.recommendationcontentreview')),
        ], options={'verbose_name':'推荐作品审核审计','verbose_name_plural':'推荐作品审核审计','ordering':('-created_at','-pk')}),
        migrations.CreateModel(name='RecommendationContentReviewState', fields=[
            ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ('version', models.PositiveIntegerField(default=0, editable=False)),
        ], options={'constraints':[
            models.CheckConstraint(condition=models.Q(id=1), name='rec_review_singleton'),
            models.CheckConstraint(condition=models.Q(version__lte=2147483647), name='rec_review_epoch_max')]}),
        migrations.AddConstraint(model_name='recommendationcontentreview', constraint=models.CheckConstraint(
            condition=models.Q(review_version__lte=2147483647), name='rec_review_version_max')),
        migrations.AddConstraint(model_name='recommendationcontentreview', constraint=models.CheckConstraint(
            condition=(models.Q(status='pending') | models.Q(status__in=('rejected','revoked'), review_version__gte=1)
                | (models.Q(status='approved', review_version__gte=1, reviewed_at__isnull=False, expires_at__isnull=False, exclusions_confirmed=True)
                   & ~models.Q(evidence_reference='') & models.Q(expires_at__gt=models.F('reviewed_at')))), name='rec_review_state_valid')),
        migrations.AddConstraint(model_name='recommendationcontentreviewevent', constraint=models.UniqueConstraint(
            fields=('review','operation_id'), name='rec_review_operation_unique')),
        migrations.AddConstraint(model_name='recommendationcontentreviewevent', constraint=models.CheckConstraint(
            condition=models.Q(action__in=('prepare','approve','reject','revoke')), name='rec_review_event_action')),
        migrations.AddConstraint(model_name='recommendationcontentreviewevent', constraint=models.CheckConstraint(
            condition=models.Q(applied_version__gte=1, applied_version__lte=2147483647,
                               applied_release_version__gte=1, applied_release_version__lte=2147483647)
                & models.Q(applied_version__gte=models.F('previous_version')), name='rec_review_event_version')),
    ]
