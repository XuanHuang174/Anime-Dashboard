import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='RecommendationAnime',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bangumi_id', models.IntegerField(blank=True, db_index=True, null=True, unique=True, validators=[django.core.validators.MinValueValidator(1)], verbose_name='Bangumi ID')),
                ('title_zh', models.CharField(db_index=True, max_length=500, verbose_name='中文片名')),
                ('title_jp', models.CharField(blank=True, default='', max_length=500, verbose_name='原名')),
                ('score', models.FloatField(db_index=True, default=0.0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(10)], verbose_name='综合评分')),
                ('tags', models.CharField(blank=True, default='', max_length=4000, verbose_name='标签')),
                ('cover_url', models.URLField(blank=True, default='', max_length=2000, verbose_name='封面直链')),
                ('source_cover_url', models.URLField(blank=True, default='', editable=False, max_length=2000, verbose_name='原始封面来源')),
                ('season', models.CharField(blank=True, db_index=True, default='', max_length=32, verbose_name='放送季度')),
                ('status_type', models.CharField(blank=True, default='', max_length=32, verbose_name='作品类型')),
                ('synopsis', models.TextField(blank=True, default='', verbose_name='简介')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='入库时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '推荐番剧',
                'verbose_name_plural': '推荐番剧库',
                'ordering': ['-score', 'bangumi_id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(score__gte=0, score__lte=10), name='rec_anime_score_range'),
                    models.CheckConstraint(condition=models.Q(bangumi_id__isnull=True) | models.Q(bangumi_id__gt=0), name='rec_anime_positive_bgm_id'),
                ],
            },
        ),
    ]
