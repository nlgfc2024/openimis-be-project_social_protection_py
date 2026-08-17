from core.gql.export_mixin import ExportableQueryMixin


class ExportableProjectQueryMixin(ExportableQueryMixin):
    module_name = 'project_social_protection'
    object_type = 'Project'
    related_field = 'benefit_plan'
    exportable_fields = ['project', 'project_history']
    export_patches = {
        'project': [],
    }
