import logging
import json

from django.apps import AppConfig
from django.core.exceptions import ValidationError

from core.module_config_registry import register_reloader, register_validator
from project_social_protection.training_form_config import normalize_training_form

logger = logging.getLogger(__name__)

MODULE_NAME = "project_social_protection"
DEFAULT_MAX_TARGET_BENEFICIARIES = 200

# Project / Activity rights keep the SAME numeric codes they had under
# social_protection so existing role grants continue to work.
DEFAULT_CONFIG = {
    "project_type": "WORK",
    "training_form": {},
    "gql_activity_search_perms": ["208001"],
    "gql_activity_create_perms": ["208002"],
    "gql_activity_update_perms": ["208003"],
    "gql_activity_delete_perms": ["208004"],
    "gql_project_search_perms": ["209001"],
    "gql_project_create_perms": ["209002"],
    "gql_project_update_perms": ["209003"],
    "gql_project_delete_perms": ["209004"],
    "gql_project_beneficiary_enroll_perms": ["209005"],
    "gql_project_beneficiary_time_entry_perms": ["209006"],
    "max_target_beneficiaries": DEFAULT_MAX_TARGET_BENEFICIARIES,
    "project_code_enabled": True,
    "project_code_district_digits": 3,
    "project_code_ta_digits": 5,
    "project_code_sequence_digits": 4,
}


class ProjectSocialProtectionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = MODULE_NAME
    verbose_name = "Project (Social Protection)"

    gql_activity_search_perms = None
    gql_activity_create_perms = None
    gql_activity_update_perms = None
    gql_activity_delete_perms = None
    gql_project_search_perms = None
    gql_project_create_perms = None
    gql_project_update_perms = None
    gql_project_delete_perms = None
    gql_project_beneficiary_enroll_perms = None
    gql_project_beneficiary_time_entry_perms = None
    max_target_beneficiaries = None
    project_type = "WORK"
    training_form = {}
    project_code_enabled = None
    project_code_district_digits = None
    project_code_ta_digits = None
    project_code_sequence_digits = None

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self.__load_config(cfg)
        register_reloader(self.name, self._reload_module_config)
        register_validator(self.name, self.validate_configuration)

    @staticmethod
    def validate_configuration(instance):
        try:
            config = json.loads(instance.config)
        except (ValueError, TypeError) as error:
            raise ValidationError({'config': 'Configuration must be valid JSON.'}) from error
        if not isinstance(config, dict) or config.get('project_type', 'WORK') not in ('WORK', 'TRAINING'):
            raise ValidationError({'config': 'project_type must be WORK or TRAINING.'})
        normalize_training_form(config.get('training_form'), config.get('max_target_beneficiaries', DEFAULT_MAX_TARGET_BENEFICIARIES))

    def _reload_module_config(self, instance):
        try:
            db_config = json.loads(instance.config)
            config = {**DEFAULT_CONFIG, **db_config}
            self.__load_config(config)
        except (ValueError, TypeError, ValidationError):
            logger.exception("Rejected invalid project configuration; keeping previous settings")
            return
        logger.info(f"Reloaded app configs for {self.name} module")

    @classmethod
    def __load_config(cls, cfg):
        if cfg.get('project_type', 'WORK') not in ('WORK', 'TRAINING'):
            raise ValidationError("project_type must be WORK or TRAINING.")
        normalize_training_form(cfg.get('training_form'), cfg.get('max_target_beneficiaries', DEFAULT_MAX_TARGET_BENEFICIARIES))
        cfg = {**DEFAULT_CONFIG, **cfg}
        for field in cfg:
            if hasattr(ProjectSocialProtectionConfig, field):
                setattr(ProjectSocialProtectionConfig, field, cfg[field])
