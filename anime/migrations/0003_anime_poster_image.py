from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("anime", "0002_anime_description_baike_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="anime",
            name="poster_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="anime/posters/%Y/%m/",
                verbose_name="上传海报",
            ),
        ),
    ]
