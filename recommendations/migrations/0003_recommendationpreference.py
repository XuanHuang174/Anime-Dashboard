from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import recommendations.taxonomy


class Migration(migrations.Migration):
    dependencies = [
        ('recommendations', '0002_recommendationanime_nsfw'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='RecommendationPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tags', models.JSONField(blank=True, default=list, validators=[recommendations.taxonomy.validate_preference_tags], verbose_name='明确选择的题材')),
                ('version', models.PositiveIntegerField(default=0, verbose_name='偏好版本')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='recommendation_preference', to=settings.AUTH_USER_MODEL, verbose_name='所属用户')),
            ],
            options={'verbose_name': '用户推荐偏好', 'verbose_name_plural': '用户推荐偏好'},
        ),
    ]
