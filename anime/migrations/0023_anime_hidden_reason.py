from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("anime", "0022_useranimerecord_total_eps_and_more")]

    operations = [
        migrations.AddField(
            model_name="anime",
            name="hidden_reason",
            field=models.CharField(
                "公共隐藏原因", max_length=32, blank=True, default="legacy_unknown", db_default="legacy_unknown",
                choices=[("private_only", "仅私人资料"),
                         ("showcase_removed", "主理人曾移除"),
                         ("admin_retired", "管理员下架"),
                         ("legacy_unknown", "历史原因不明")],
                help_text="仅在非公开时有意义；历史隐藏资料默认待审核，不能自动恢复公开。",
            ),
        ),
        migrations.AlterField(
            model_name="anime",
            name="is_active",
            field=models.BooleanField(
                "在公共库中可用", default=True, db_index=True,
                help_text="关闭后不出现在公共搜索；不代表禁止通过安全的确切身份加入私人手账。",
            ),
        ),
    ]
