from django.db import migrations, models

import project_social_protection.models


def reject_intermediate_completion_values(apps, schema_editor):
    """Do not silently reinterpret historical completion percentages."""
    model_names = (
        'BeneficiaryProjectTimeEntry',
        'GroupBeneficiaryProjectTimeEntry',
    )
    invalid_counts = {}

    for model_name in model_names:
        model = apps.get_model('project_social_protection', model_name)
        count = model.objects.exclude(percent_complete__in=(0, 100)).count()
        if count:
            invalid_counts[model._meta.db_table] = count

    if invalid_counts:
        details = ', '.join(
            f'{table}: {count}' for table, count in invalid_counts.items()
        )
        raise RuntimeError(
            'Cannot enforce binary work completion while intermediate values '
            f'exist ({details}). Review and convert those records to 0 or 100, '
            'then run this migration again.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('project_social_protection', '0006_add_project_code'),
    ]

    operations = [
        migrations.RunPython(
            reject_intermediate_completion_values,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='beneficiaryprojecttimeentry',
            name='percent_complete',
            field=models.IntegerField(
                validators=[project_social_protection.models.validate_binary_completion],
            ),
        ),
        migrations.AlterField(
            model_name='groupbeneficiaryprojecttimeentry',
            name='percent_complete',
            field=models.IntegerField(
                validators=[project_social_protection.models.validate_binary_completion],
            ),
        ),
        migrations.AlterField(
            model_name='historicalbeneficiaryprojecttimeentry',
            name='percent_complete',
            field=models.IntegerField(
                validators=[project_social_protection.models.validate_binary_completion],
            ),
        ),
        migrations.AlterField(
            model_name='historicalgroupbeneficiaryprojecttimeentry',
            name='percent_complete',
            field=models.IntegerField(
                validators=[project_social_protection.models.validate_binary_completion],
            ),
        ),
        migrations.AddConstraint(
            model_name='beneficiaryprojecttimeentry',
            constraint=models.CheckConstraint(
                check=models.Q(percent_complete__in=[0, 100]),
                name='beneficiary_time_entry_binary_completion',
            ),
        ),
        migrations.AddConstraint(
            model_name='groupbeneficiaryprojecttimeentry',
            constraint=models.CheckConstraint(
                check=models.Q(percent_complete__in=[0, 100]),
                name='group_time_entry_binary_completion',
            ),
        ),
    ]
