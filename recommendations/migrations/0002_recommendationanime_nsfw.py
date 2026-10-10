from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('recommendations', '0001_initial')]
    operations = [
        migrations.AddField(
            model_name='recommendationanime', name='nsfw',
            field=models.BooleanField(blank=True, db_index=True, default=None, null=True, verbose_name='成人内容标记'),
        ),
    ]
