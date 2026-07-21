from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0005_add_project_micro_catchment'),
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
        migrations.RunSQL(
            sql=[
                'ALTER TABLE social_protection_project DROP CONSTRAINT IF EXISTS "social_protection_pr_micro_catchment_id_08e7edff_fk_tblLocati" CASCADE;',
                'ALTER TABLE social_protection_project ADD CONSTRAINT social_protection_project_micro_catchment_id_fkey FOREIGN KEY (micro_catchment_id) REFERENCES "tblMicroCatchments" ("MicroCatchmentId") DEFERRABLE INITIALLY DEFERRED;',
            ],
            reverse_sql=[
                'ALTER TABLE social_protection_project DROP CONSTRAINT IF EXISTS social_protection_project_micro_catchment_id_fkey CASCADE;',
                'ALTER TABLE social_protection_project ADD CONSTRAINT "social_protection_pr_micro_catchment_id_08e7edff_fk_tblLocati" FOREIGN KEY (micro_catchment_id) REFERENCES "tblLocations" ("LocationId") DEFERRABLE INITIALLY DEFERRED;',
            ],
        ),
    ]
