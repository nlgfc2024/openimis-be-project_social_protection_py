from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0005_alter_project_status_and_fix_micro_catchment_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalproject',
            name='code',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='project',
            name='code',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_deleted', False)),
                fields=('code',),
                name='uniq_live_project_code',
            ),
        ),
    ]
