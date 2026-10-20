"""P1 additive settings only; no private journal backfill or score rewriting."""
from decimal import Decimal

from django.db import migrations, models
import recommendations.profile_policy


class Migration(migrations.Migration):
    dependencies = [('recommendations', '0005_recommendation_feedback')]

    operations = [
        migrations.AddField(
            model_name='recommendationpreference', name='profile_min_score',
            field=models.DecimalField(decimal_places=1, max_digits=3, default=Decimal('8.5'), db_default=Decimal('8.5'),
                validators=[recommendations.profile_policy.validate_profile_min_score], verbose_name='画像取样门槛'),
        ),
        migrations.AddField(
            model_name='recommendationpreference', name='profile_policy_version',
            field=models.CharField(max_length=48, default='profile-sampling.v1', db_default='profile-sampling.v1', editable=False, verbose_name='画像取样规则版本'),
        ),
        migrations.AddConstraint(
            model_name='recommendationpreference',
            constraint=models.CheckConstraint(condition=models.Q(profile_min_score__in=(
                Decimal('6.0'), Decimal('6.5'), Decimal('7.0'), Decimal('7.5'), Decimal('8.0'),
                Decimal('8.5'), Decimal('9.0'), Decimal('9.5'), Decimal('10.0'))),
                name='rec_preference_profile_score_choices'),
        ),
    ]
