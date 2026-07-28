from django.db import migrations, models
import django.db.models.deletion


def link_featured_submissions_to_records(apps, schema_editor):
    FeaturedSubmission = apps.get_model("anime", "FeaturedSubmission")
    UserAnimeRecord = apps.get_model("anime", "UserAnimeRecord")

    orphan_ids = []
    submissions = FeaturedSubmission.objects.exclude(source_anime_id=None).iterator()
    for submission in submissions:
        record_id = (
            UserAnimeRecord.objects.filter(
                user_id=submission.user_id,
                anime_id=submission.source_anime_id,
            )
            .values_list("id", flat=True)
            .first()
        )
        if record_id is None:
            orphan_ids.append(submission.pk)
            continue
        submission.source_record_id = record_id
        submission.save(update_fields=("source_record",))

    if orphan_ids:
        FeaturedSubmission.objects.filter(pk__in=orphan_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("anime", "0016_useranimerecord_metadata_initialized_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="featuredsubmission",
            name="source_record",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="删除该用户的私人手账记录时，同步删除由它产生的精选专栏内容。",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="featured_submissions",
                to="anime.useranimerecord",
                verbose_name="来源私人记录",
            ),
        ),
        migrations.RunPython(
            link_featured_submissions_to_records,
            migrations.RunPython.noop,
        ),
    ]
