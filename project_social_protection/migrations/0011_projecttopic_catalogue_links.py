from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('project_social_protection', '0009_training_topic_snapshots')]

    operations = [
        migrations.RemoveField(model_name=model, name=field)
        for model in ('projecttopic', 'historicalprojecttopic')
        for field in ('code', 'name', 'module_name', 'module')
    ]
