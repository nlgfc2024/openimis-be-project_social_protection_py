from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0006_add_project_code'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='project',
            name='uniq_live_project_code',
        ),
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(
                fields=('code',),
                name='uniq_live_project_code',
            ),
        ),
    ]
