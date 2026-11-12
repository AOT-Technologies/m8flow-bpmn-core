"""Error and escalation event subprocesses through the core API (M8F-665).

These run on real persistence: work items of cancelled user tasks leave the
worklist, and a throw after the instance was saved and restored still reaches
the right handler.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.process_instance_event import ProcessInstanceEventModel
from m8flow_core.models.task import TaskModel
from m8flow_core.models.task_definition import TaskDefinitionModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from test_bpmn_error_escalation_standards import (
    ERROR,
    ESCALATION,
    MAIN_START,
    _bpmn,
    _error_end,
    _flow,
    _handler,
    _script,
    _work,
)

REVIEW = '<bpmn:userTask id="Review" name="Review" />'


def _seed(session: Session) -> tuple[M8flowTenantModel, UserModel]:
    tenant = M8flowTenantModel(id="events-tenant", name="Events", slug="events-tenant")
    user = UserModel(
        username="reviewer",
        email="reviewer@example.com",
        service="http://localhost/realms/events-tenant",
        service_id="reviewer",
        display_name="Reviewer",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant.id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )
    return tenant, user


def _start(session: Session, xml: str):
    tenant, user = _seed(session)
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="events-model",
            user_id=user.id,
            source_bpmn_xml=xml,
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
        ),
    )
    return tenant, user, instance


def _pending(session: Session, tenant, user):
    return api.execute_query(
        session, api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id)
    )


def _status(session: Session, tenant, instance) -> api.ProcessInstanceStatus:
    return api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id, process_instance_id=instance.id
        ),
    ).status


def _task_states(session: Session, instance) -> dict[str, set[str]]:
    rows = session.execute(
        select(TaskDefinitionModel.bpmn_identifier, TaskModel.state)
        .join(
            TaskDefinitionModel,
            TaskModel.task_definition_id == TaskDefinitionModel.id,
        )
        .where(TaskModel.process_instance_id == instance.id)
    )
    states: dict[str, set[str]] = {}
    for bpmn_identifier, state in rows:
        states.setdefault(bpmn_identifier, set()).add(state)
    return states


def _review_beside_escalation(*, interrupting: bool) -> str:
    """A user task on one branch, an escalation throw on the other."""
    return _bpmn(
        f'{MAIN_START}<bpmn:parallelGateway id="Split" />{REVIEW}'
        '<bpmn:endEvent id="ReviewEnd" />'
        f'<bpmn:intermediateThrowEvent id="Throw">{ESCALATION}'
        '</bpmn:intermediateThrowEvent><bpmn:endEvent id="ThrowEnd" />'
        f'{_flow("f1", "Mark", "Split")}{_flow("f2", "Split", "Review")}'
        f'{_flow("f3", "Review", "ReviewEnd")}{_flow("f4", "Split", "Throw")}'
        f'{_flow("f5", "Throw", "ThrowEnd")}'
        + _handler("Notify", ESCALATION, "outer_handled", interrupting=interrupting)
    )


@pytest.mark.parametrize(
    ("interrupting", "status", "pending"),
    [
        pytest.param(True, "complete", 0, id="interrupting-cancels-the-review"),
        pytest.param(False, "user_input_required", 1, id="non-interrupting-keeps-it"),
    ],
)
def test_escalation_event_subprocess_and_a_waiting_user_task(
    session: Session, interrupting: bool, status: str, pending: int
) -> None:
    tenant, user, instance = _start(
        session, _review_beside_escalation(interrupting=interrupting)
    )

    assert _status(session, tenant, instance) == api.ProcessInstanceStatus(status)
    assert len(_pending(session, tenant, user)) == pending


@pytest.mark.parametrize("after_has_history", [False, True])
def test_error_thrown_after_a_restore_reaches_its_event_subprocess(
    session: Session, after_has_history: bool
) -> None:
    xml = _bpmn(
        f"{MAIN_START}{REVIEW}{_work(_error_end(ERROR('ErrB')))}"
        f'{_script("After", "main_done = True")}<bpmn:endEvent id="EndEvent" />'
        f'{_flow("f1", "Mark", "Review")}{_flow("f2", "Review", "Work")}'
        f'{_flow("f3", "Work", "After")}{_flow("f4", "After", "EndEvent")}'
        + _handler("OnError", ERROR("ErrB"), "outer_handled")
    )
    tenant, user, instance = _start(session, xml)
    [task] = _pending(session, tenant, user)
    if after_has_history:
        # A reset can put a step that already ran back into FUTURE; its events
        # are history, so the row they refer to must stay.
        after_guid = session.scalars(
            select(TaskModel.guid)
            .join(TaskDefinitionModel)
            .where(
                TaskModel.process_instance_id == instance.id,
                TaskDefinitionModel.bpmn_identifier == "After",
            )
        ).one()
        session.add(
            ProcessInstanceEventModel(
                m8f_tenant_id=tenant.id,
                process_instance_id=instance.id,
                task_guid=after_guid,
                event_type="task_completed",
                occurred_at=datetime.now(UTC),
            )
        )
        session.flush()

    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant.id, work_item_id=task.id, user_id=user.id
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=tenant.id, work_item_id=task.id, user_id=user.id
        ),
    )

    assert _status(session, tenant, instance) == api.ProcessInstanceStatus.complete
    assert _pending(session, tenant, user) == []
    states = _task_states(session, instance)
    assert states["OnError"] == {"COMPLETED"}
    assert states["Work"] == {"CANCELLED"}
    # "After" was saved as a predicted step while the instance waited; the
    # interrupt dropped it, so no stored row may be left behind unless an
    # event refers to it.
    if after_has_history:
        assert states["After"] == {"FUTURE"}
    else:
        assert "After" not in states
