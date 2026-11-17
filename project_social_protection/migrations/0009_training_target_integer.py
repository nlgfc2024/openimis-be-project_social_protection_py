from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('project_social_protection', '0008_optional_training_target')]
    operations = [
        migrations.AlterField(model_name=name, name='target_beneficiaries',
                              field=models.IntegerField(blank=True, null=True))
        for name in ('project', 'historicalproject')
    ]
