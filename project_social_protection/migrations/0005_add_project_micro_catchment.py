from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0003_project_uniq_live_project_name_per_plan'),
        ('location', '0027_remove_hotspot_villages_hotspotvillage'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalproject',
            name='micro_catchment',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='location.microcatchment'),
        ),
        migrations.AddField(
            model_name='project',
            name='micro_catchment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='projects', to='location.microcatchment'),
        ),
    ]
