from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import anime.models


class Migration(migrations.Migration):
    dependencies = [
        ("anime", "0005_anime_tag_colors"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserQuickFilter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=30, verbose_name="筛选名称")),
                ("tags", models.JSONField(default=list, validators=[anime.models.validate_tag_list], verbose_name="标签组合")),
                ("match_mode", models.CharField(choices=[("any", "匹配任意标签"), ("all", "匹配全部标签")], default="any", max_length=8, verbose_name="匹配方式")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="anime_quick_filters", to=settings.AUTH_USER_MODEL, verbose_name="用户")),
            ],
            options={
                "verbose_name": "用户快捷筛选",
                "verbose_name_plural": "用户快捷筛选",
                "ordering": ("created_at",),
                "constraints": [models.UniqueConstraint(fields=("user", "name"), name="unique_user_quick_filter_name")],
            },
        ),
    ]
