from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0004_add_project_micro_catchment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicalproject',
            name='status',
            field=models.CharField(choices=[('INITIATED', 'INITIATED'), ('PREPARATION', 'PREPARATION'), ('IN_PROGRESS', 'IN PROGRESS'), ('COMPLETED', 'COMPLETED')], default='PREPARATION', max_length=100),
        ),
        migrations.AlterField(
            model_name='project',
            name='status',
            field=models.CharField(choices=[('INITIATED', 'INITIATED'), ('PREPARATION', 'PREPARATION'), ('IN_PROGRESS', 'IN PROGRESS'), ('COMPLETED', 'COMPLETED')], default='PREPARATION', max_length=100),
        ),
    ]
