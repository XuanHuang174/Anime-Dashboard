"""8A additive feedback schema only. No backfill or journal data mutation."""
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('recommendations', '0004_recommendationidentityevent_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='RecommendationFeedbackOperation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation_id', models.UUIDField()),
                ('bangumi_id', models.IntegerField()),
                ('action', models.CharField(choices=[('hide', '不感兴趣'), ('undo', '撤销')], max_length=8)),
                ('request_fingerprint', models.CharField(max_length=64)),
                ('applied_feedback_version', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recommendation_feedback_operations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '推荐反馈幂等回执', 'verbose_name_plural': '推荐反馈幂等回执',
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'operation_id'), name='rec_feedback_operation_unique'),
                    models.CheckConstraint(condition=models.Q(bangumi_id__gt=0), name='rec_feedback_op_positive_bgm'),
                    models.CheckConstraint(condition=models.Q(action__in=('hide', 'undo')), name='rec_feedback_op_action'),
                    models.CheckConstraint(condition=models.Q(applied_feedback_version__gte=1, applied_feedback_version__lte=2147483647), name='rec_feedback_op_version_range'),
                ],
            },
        ),
        migrations.CreateModel(
            name='UserRecommendationFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bangumi_id', models.IntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('is_active', models.BooleanField(default=True)),
                ('feedback_version', models.PositiveIntegerField()),
                ('hidden_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recommendation_feedback', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '作品级推荐反馈', 'verbose_name_plural': '作品级推荐反馈',
                'indexes': [models.Index(fields=['user', 'is_active', '-hidden_at', '-id'], name='rec_feedback_hidden_idx')],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'bangumi_id'), name='rec_feedback_user_work_unique'),
                    models.CheckConstraint(condition=models.Q(bangumi_id__gt=0), name='rec_feedback_positive_bgm'),
                    models.CheckConstraint(condition=models.Q(feedback_version__gte=1, feedback_version__lte=2147483647), name='rec_feedback_version_range'),
                    models.CheckConstraint(condition=models.Q(is_active=False) | models.Q(hidden_at__isnull=False), name='rec_feedback_active_has_time'),
                ],
            },
        ),
        migrations.CreateModel(
            name='UserRecommendationFeedbackState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('feedback_version', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='recommendation_feedback_state', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '用户推荐反馈版本', 'verbose_name_plural': '用户推荐反馈版本',
                'constraints': [models.CheckConstraint(condition=models.Q(feedback_version__lte=2147483647), name='rec_feedback_state_version_max')],
            },
        ),
    ]
