from django.db import migrations, models

import anime.models


class Migration(migrations.Migration):

    dependencies = [
        ("anime", "0017_featuredsubmission_source_record"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeaturedQuickFilter",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=30, unique=True, verbose_name="筛选名称")),
                (
                    "tags",
                    models.JSONField(
                        default=list,
                        validators=[anime.models.validate_tag_list],
                        verbose_name="标签组合",
                    ),
                ),
                (
                    "title_keywords",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="与标签组合共同参与匹配，例如 OVA。",
                        validators=[anime.models.validate_tag_list],
                        verbose_name="标题关键字",
                    ),
                ),
                (
                    "match_mode",
                    models.CharField(
                        choices=[
                            ("any", "匹配任意标签"),
                            ("all", "匹配全部标签"),
                        ],
                        default="any",
                        max_length=8,
                        verbose_name="匹配方式",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "精选专栏快捷筛选",
                "verbose_name_plural": "精选专栏快捷筛选",
                "ordering": ("created_at",),
            },
        ),
    ]
