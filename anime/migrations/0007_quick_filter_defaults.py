from django.conf import settings
from django.db import migrations, models

import anime.models


DEFAULT_FILTERS = (
    {"name": "百合 (真/轻)", "tags": ["真百", "轻百"]},
    {"name": "萌系 & 日常", "tags": ["萌系", "日常"]},
    {"name": "搞笑 & 校园", "tags": ["搞笑", "校园"]},
    {"name": "原创 & 治愈", "tags": ["原创", "治愈"]},
    {"name": "剧场版 & OVA & 泡面番", "tags": ["剧场版", "泡面番"], "title_keywords": ["OVA"]},
)


def seed_default_filters(apps, schema_editor):
    user_model = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    quick_filter_model = apps.get_model("anime", "UserQuickFilter")
    for user in user_model.objects.all().iterator():
        for item in DEFAULT_FILTERS:
            quick_filter_model.objects.get_or_create(
                user=user,
                name=item["name"],
                defaults={
                    "tags": item["tags"],
                    "title_keywords": item.get("title_keywords", []),
                    "match_mode": "any",
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("anime", "0006_userquickfilter"),
    ]

    operations = [
        migrations.AddField(
            model_name="userquickfilter",
            name="title_keywords",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="与标签组合共同参与匹配，例如 OVA。",
                validators=[anime.models.validate_tag_list],
                verbose_name="标题关键字",
            ),
        ),
        migrations.RunPython(seed_default_filters, migrations.RunPython.noop),
    ]
