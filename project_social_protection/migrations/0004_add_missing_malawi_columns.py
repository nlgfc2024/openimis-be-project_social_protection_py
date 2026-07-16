from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency('core.User'),
        ('location', '0027_remove_hotspot_villages_hotspotvillage'),
        ('project_social_protection', '0003_project_uniq_live_project_name_per_plan'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                'ALTER TABLE social_protection_project ADD COLUMN IF NOT EXISTS hotspot_id INTEGER;',
                'ALTER TABLE social_protection_project ADD COLUMN IF NOT EXISTS foreman_id UUID;',
                'ALTER TABLE social_protection_project ADD COLUMN IF NOT EXISTS supervisor_id UUID;',
                'ALTER TABLE social_protection_historicalproject ADD COLUMN IF NOT EXISTS hotspot_id INTEGER;',
                'ALTER TABLE social_protection_historicalproject ADD COLUMN IF NOT EXISTS foreman_id UUID;',
                'ALTER TABLE social_protection_historicalproject ADD COLUMN IF NOT EXISTS supervisor_id UUID;',
                'ALTER TABLE social_protection_project ADD CONSTRAINT social_protection_project_hotspot_id_fkey FOREIGN KEY (hotspot_id) REFERENCES "tblHotspots" ("HotspotId") DEFERRABLE INITIALLY DEFERRED;',
                'ALTER TABLE social_protection_project ADD CONSTRAINT social_protection_project_foreman_id_fkey FOREIGN KEY (foreman_id) REFERENCES "core_User" (id) DEFERRABLE INITIALLY DEFERRED;',
                'ALTER TABLE social_protection_project ADD CONSTRAINT social_protection_project_supervisor_id_fkey FOREIGN KEY (supervisor_id) REFERENCES "core_User" (id) DEFERRABLE INITIALLY DEFERRED;',
            ],
            reverse_sql=[
                'ALTER TABLE social_protection_project DROP CONSTRAINT IF EXISTS social_protection_project_hotspot_id_fkey;',
                'ALTER TABLE social_protection_project DROP CONSTRAINT IF EXISTS social_protection_project_foreman_id_fkey;',
                'ALTER TABLE social_protection_project DROP CONSTRAINT IF EXISTS social_protection_project_supervisor_id_fkey;',
                'ALTER TABLE social_protection_project DROP COLUMN IF EXISTS hotspot_id;',
                'ALTER TABLE social_protection_project DROP COLUMN IF EXISTS foreman_id;',
                'ALTER TABLE social_protection_project DROP COLUMN IF EXISTS supervisor_id;',
                'ALTER TABLE social_protection_historicalproject DROP COLUMN IF EXISTS hotspot_id;',
                'ALTER TABLE social_protection_historicalproject DROP COLUMN IF EXISTS foreman_id;',
                'ALTER TABLE social_protection_historicalproject DROP COLUMN IF EXISTS supervisor_id;',
            ],
        ),
    ]
