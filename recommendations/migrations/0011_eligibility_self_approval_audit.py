from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('recommendations', '0010_controlled_serving_m2')]

    operations = [
        migrations.AddField(
            model_name='recommendationeligibilityevent', name='is_self_approval',
            field=models.BooleanField(default=False, db_default=False, verbose_name='超级管理员本人授权')),
        migrations.AddConstraint(
            model_name='recommendationeligibilityevent',
            constraint=models.CheckConstraint(
                condition=models.Q(is_self_approval=False) | models.Q(action='approve'),
                name='rec_elig_self_approve_action')),
    ]
