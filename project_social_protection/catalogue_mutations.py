import graphene
from django.core.exceptions import ValidationError
from core.schema import _check_csrf_token

from project_social_protection.gql_queries import ActivityGQLType
from project_social_protection.training_catalogue import TrainingModuleService
from project_social_protection.mutation_scope import require_authenticated


class TrainingTopicInput(graphene.InputObjectType):
    id = graphene.UUID()
    code = graphene.String(required=True)
    name = graphene.String(required=True)


class TrainingModuleInput(graphene.InputObjectType):
    name = graphene.String(required=True)
    programme_ids = graphene.List(graphene.NonNull(graphene.UUID), required=True)
    topics = graphene.List(graphene.NonNull(TrainingTopicInput), required=True)


class TrainingModuleResult(graphene.ObjectType):
    success = graphene.Boolean(required=True)
    errors = graphene.List(graphene.String, required=True)
    module = graphene.Field(ActivityGQLType)


def save_module(info, input, module_id=None):
    require_authenticated(info.context.user)
    _check_csrf_token(info.context)
    try:
        module = TrainingModuleService(info.context.user).save(
            input['name'], input['programme_ids'], input['topics'], module_id)
        return TrainingModuleResult(success=True, errors=[], module=module)
    except ValidationError as error:
        return TrainingModuleResult(success=False, errors=error.messages)


class CreateTrainingModule(graphene.Mutation):
    class Arguments:
        input = TrainingModuleInput(required=True)

    Output = TrainingModuleResult

    def mutate(self, info, input):
        return save_module(info, input)


class UpdateTrainingModule(graphene.Mutation):
    class Arguments:
        id = graphene.UUID(required=True)
        input = TrainingModuleInput(required=True)

    Output = TrainingModuleResult

    def mutate(self, info, id, input):
        return save_module(info, input, id)


class DeleteTrainingModule(graphene.Mutation):
    class Arguments:
        id = graphene.UUID(required=True)

    Output = TrainingModuleResult

    def mutate(self, info, id):
        require_authenticated(info.context.user)
        _check_csrf_token(info.context)
        try:
            module = TrainingModuleService(info.context.user).delete(id)
            return TrainingModuleResult(success=True, errors=[], module=module)
        except ValidationError as error:
            return TrainingModuleResult(success=False, errors=error.messages)
