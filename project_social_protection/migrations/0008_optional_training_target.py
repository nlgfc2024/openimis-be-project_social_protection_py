from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('project_social_protection', '0007_training_sessions')]
    operations = [
        migrations.AlterField(model_name=name, name='target_beneficiaries',
                              field=models.SmallIntegerField(blank=True, null=True))
        for name in ('project', 'historicalproject')
    ]
