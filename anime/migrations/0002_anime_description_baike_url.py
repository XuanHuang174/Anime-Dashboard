from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("anime", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="anime",
            old_name="synopsis",
            new_name="description",
        ),
        migrations.AddField(
            model_name="anime",
            name="baike_url",
            field=models.URLField(blank=True, max_length=1000, verbose_name="萌百链接"),
        ),
    ]
