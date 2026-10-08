from __future__ import annotations

from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.task_types import USER_TASK_TYPENAMES


def test_user_task_typenames_are_immutable_and_domain_scoped() -> None:
    assert USER_TASK_TYPENAMES == {"ManualTask", "NoneTask", "UserTask"}
    assert isinstance(USER_TASK_TYPENAMES, frozenset)


def test_task_definition_uses_central_user_task_vocabulary() -> None:
    assert all(
        TaskDefinitionModel(
            typename=typename,
            properties_json={},
        ).is_user_task()
        for typename in USER_TASK_TYPENAMES
    )
    assert not TaskDefinitionModel(
        typename="ScriptTask",
        properties_json={},
        ).is_user_task()
