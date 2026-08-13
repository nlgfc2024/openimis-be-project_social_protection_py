import logging
import json

from django.apps import AppConfig

from core.module_config_registry import register_reloader

logger = logging.getLogger(__name__)

MODULE_NAME = "project_social_protection"
DEFAULT_MAX_TARGET_BENEFICIARIES = 200

# Project / Activity rights keep the SAME numeric codes they had under
# social_protection so existing role grants continue to work.
DEFAULT_CONFIG = {
    "gql_activity_search_perms": ["208001"],
    "gql_project_search_perms": ["209001"],
    "gql_project_create_perms": ["209002"],
    "gql_project_update_perms": ["209003"],
    "gql_project_delete_perms": ["209004"],
    "gql_project_beneficiary_enroll_perms": ["209005"],
    "gql_project_beneficiary_time_entry_perms": ["209006"],
    "max_target_beneficiaries": DEFAULT_MAX_TARGET_BENEFICIARIES,
}


class ProjectSocialProtectionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = MODULE_NAME
    verbose_name = "Project (Social Protection)"

    gql_activity_search_perms = None
    gql_project_search_perms = None
    gql_project_create_perms = None
    gql_project_update_perms = None
    gql_project_delete_perms = None
    gql_project_beneficiary_enroll_perms = None
    gql_project_beneficiary_time_entry_perms = None
    max_target_beneficiaries = None

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self.__load_config(cfg)
        register_reloader(self.name, self._reload_module_config)

    def _reload_module_config(self, instance):
        db_config = json.loads(instance.config)
        config = {**DEFAULT_CONFIG, **db_config}
        self.__load_config(config)
        logger.info(f"Reloaded app configs for {self.name} module")

    @classmethod
    def __load_config(cls, cfg):
        for field in cfg:
            if hasattr(ProjectSocialProtectionConfig, field):
                setattr(ProjectSocialProtectionConfig, field, cfg[field])
