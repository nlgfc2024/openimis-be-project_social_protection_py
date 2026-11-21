from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('project_social_protection', '0006_add_project_code')]

    operations = [
        migrations.AddField(model_name=model_name, name=field_name, field=field)
        for model_name in ('project', 'historicalproject')
        for field_name, field in (
            ('project_type', models.CharField(choices=[('WORK', 'WORK'), ('TRAINING', 'TRAINING')], default='WORK', max_length=8)),
            ('session_start_date', models.DateField(blank=True, null=True)),
            ('session_end_date', models.DateField(blank=True, null=True)),
        )
    ]
