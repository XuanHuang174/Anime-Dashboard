from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("anime", "0003_anime_poster_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="anime",
            name="is_active",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="下架后不会出现在公共番剧搜索中，也不能再被用户添加。",
                verbose_name="在公共库中可用",
            ),
        ),
    ]
