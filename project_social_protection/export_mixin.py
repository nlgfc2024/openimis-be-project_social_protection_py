from social_protection.export_mixin import ExportableSocialProtectionQueryMixin


class ExportableProjectQueryMixin(ExportableSocialProtectionQueryMixin):
    module_name = 'project_social_protection'
    object_type = 'Project'
    related_field = 'benefit_plan'
    exportable_fields = ['project']
