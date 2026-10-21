"""N2-1 additive schema only. No account grants, permission assignments or backfill."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('recommendations', '0006_preference_profile_sampling'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='RecommendationContentEligibility',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', '待审核'), ('active', '已签发（不代表已开放）'), ('revoked', '已撤销')], default='pending', max_length=12)),
                ('eligibility_version', models.PositiveIntegerField(default=0, editable=False)),
                ('purpose', models.CharField(default='internal_test', editable=False, max_length=24)),
                ('granted_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('expires_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('assurance_provider', models.CharField(blank=True, default='', editable=False, max_length=64)),
                ('assurance_reference', models.CharField(blank=True, default='', editable=False, max_length=128)),
                ('assurance_version', models.PositiveIntegerField(default=0, editable=False)),
                ('assurance_valid_until', models.DateTimeField(blank=True, editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('granted_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='recommendation_content_eligibility', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': '推荐内容访问资格', 'verbose_name_plural': '推荐内容访问资格',
                     'permissions': [('manage_content_eligibility', '通过安全服务管理推荐内容访问资格')]},
        ),
        migrations.CreateModel(
            name='RecommendationEligibilityEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation_id', models.UUIDField()),
                ('action', models.CharField(choices=[('prepare', '建立待审核'), ('grant', '签发'), ('renew', '续期'), ('revoke', '管理员撤销'), ('self_revoke', '本人停用')], max_length=12)),
                ('request_fingerprint', models.CharField(max_length=64)),
                ('previous_status', models.CharField(max_length=16)),
                ('resulting_status', models.CharField(max_length=12)),
                ('previous_version', models.PositiveIntegerField()),
                ('applied_version', models.PositiveIntegerField()),
                ('changed', models.BooleanField()),
                ('reason', models.CharField(max_length=200)),
                ('resulting_expires_at', models.DateTimeField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('eligibility', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='recommendations.recommendationcontenteligibility')),
            ],
            options={'verbose_name': '推荐资格操作审计', 'verbose_name_plural': '推荐资格操作审计', 'ordering': ('-created_at', '-pk')},
        ),
        migrations.AddConstraint(model_name='recommendationcontenteligibility',
            constraint=models.CheckConstraint(condition=models.Q(eligibility_version__lte=2147483647), name='rec_elig_version_max')),
        migrations.AddConstraint(model_name='recommendationcontenteligibility',
            constraint=models.CheckConstraint(condition=models.Q(purpose='internal_test'), name='rec_elig_internal_purpose')),
        migrations.AddConstraint(model_name='recommendationcontenteligibility',
            constraint=models.CheckConstraint(condition=(
                models.Q(status='pending', granted_at__isnull=True, expires_at__isnull=True, revoked_at__isnull=True)
                | models.Q(status='revoked', revoked_at__isnull=False, eligibility_version__gte=1)
                | (models.Q(status='active', granted_at__isnull=False, expires_at__isnull=False,
                            revoked_at__isnull=True, assurance_valid_until__isnull=False,
                            assurance_version__gte=1, eligibility_version__gte=1)
                   & ~models.Q(assurance_provider='') & ~models.Q(assurance_reference='')
                   & models.Q(expires_at__gt=models.F('granted_at'))
                   & models.Q(expires_at__lte=models.F('assurance_valid_until')))), name='rec_elig_state_valid')),
        migrations.AddConstraint(model_name='recommendationeligibilityevent',
            constraint=models.UniqueConstraint(fields=('eligibility', 'operation_id'), name='rec_elig_operation_unique')),
        migrations.AddConstraint(model_name='recommendationeligibilityevent',
            constraint=models.CheckConstraint(condition=models.Q(action__in=('prepare', 'grant', 'renew', 'revoke', 'self_revoke')), name='rec_elig_event_action')),
        migrations.AddConstraint(model_name='recommendationeligibilityevent',
            constraint=models.CheckConstraint(condition=models.Q(applied_version__gte=1, applied_version__lte=2147483647)
                & models.Q(applied_version__gte=models.F('previous_version')), name='rec_elig_event_version')),
    ]
