"""Catalogue mutations use the shared journal, CSRF and error-handling pipeline."""
import graphene

from core.gql.gql_mutations.base_mutation import BaseMutation
from core.schema import OpenIMISMutation
from project_social_protection.models import Activity
from project_social_protection.training_catalogue import TrainingModuleService, require_catalogue_permission


class TrainingTopicInput(graphene.InputObjectType):
    id = graphene.UUID()
    code = graphene.String(required=True)
    name = graphene.String(required=True)


class TrainingModuleInput(OpenIMISMutation.Input):
    name = graphene.String(required=True)
    programme_ids = graphene.List(graphene.NonNull(graphene.UUID), required=True)
    topics = graphene.List(graphene.NonNull(TrainingTopicInput), required=True)


class CreateTrainingModule(BaseMutation):
    _mutation_class = 'CreateTrainingModule'
    _mutation_module = 'project_social_protection'
    _model = Activity

    class Input(TrainingModuleInput):
        pass

    @classmethod
    def _validate_mutation(cls, user, **data):
        require_catalogue_permission(user, 'create')

    @classmethod
    def _mutate(cls, user, **data):
        TrainingModuleService(user).save(data['name'], data['programme_ids'], data['topics'])


class UpdateTrainingModule(BaseMutation):
    _mutation_class = 'UpdateTrainingModule'
    _mutation_module = 'project_social_protection'
    _model = Activity

    class Input(TrainingModuleInput):
        id = graphene.UUID(required=True)

    @classmethod
    def _validate_mutation(cls, user, **data):
        require_catalogue_permission(user, 'update')

    @classmethod
    def _mutate(cls, user, **data):
        TrainingModuleService(user).save(data['name'], data['programme_ids'], data['topics'], data['id'])


class DeleteTrainingModule(BaseMutation):
    _mutation_class = 'DeleteTrainingModule'
    _mutation_module = 'project_social_protection'
    _model = Activity

    class Input(OpenIMISMutation.Input):
        id = graphene.UUID(required=True)

    @classmethod
    def _validate_mutation(cls, user, **data):
        require_catalogue_permission(user, 'delete')

    @classmethod
    def _mutate(cls, user, **data):
        TrainingModuleService(user).delete(data['id'])
