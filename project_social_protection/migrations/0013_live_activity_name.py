from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('project_social_protection', '0012_merge_training_configuration_and_topic_links')]
    operations = [
        migrations.AlterField(model_name='activity', name='name', field=models.CharField(max_length=255, db_index=True)),
        migrations.AddConstraint(model_name='activity', constraint=models.UniqueConstraint(
            fields=('name',), condition=models.Q(is_deleted=False), name='uniq_live_activity_name')),
    ]
