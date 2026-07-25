from django.db import migrations, models

import anime.models


class Migration(migrations.Migration):
    dependencies = [
        ("anime", "0004_anime_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="anime",
            name="tag_colors",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='标签到预设颜色的映射，例如：{"日常": "blue"}。',
                validators=[anime.models.validate_tag_color_map],
                verbose_name="标签配色",
            ),
        ),
    ]
