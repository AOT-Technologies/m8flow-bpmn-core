from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.errors import CallActivityResolutionError
from m8flow_core.models.task_definition import TaskDefinitionModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_core.services.workflow_engine import _build_workflow


class _FailingConnector:
    connector_key = "demo"

    def list_commands(self):
        return (
            api.ServiceTaskCommandDefinition(
                connector_key="demo", command_name="Fail"
            ),
        )

    def execute(self, _request):
        raise RuntimeError("child connector failed")

FIXTURES = Path(__file__).with_name("fixtures")
PARENT_BPMN = (FIXTURES / "simple-call-activity.bpmn").read_text(encoding="utf-8")
CHILD_BPMN = (FIXTURES / "call-activity-child.bpmn").read_text(encoding="utf-8")
MAPPED_CHILD_BPMN = (FIXTURES / "call-activity-mapped-child.bpmn").read_text(
    encoding="utf-8"
)
FAILING_CHILD_BPMN = (FIXTURES / "call-activity-failing-child.bpmn").read_text(
    encoding="utf-8"
)


def _seed(session: Session) -> tuple[M8flowTenantModel, UserModel]:
    tenant = M8flowTenantModel(id="call-tenant", name="Calls", slug="call-tenant")
    user = UserModel(
        username="caller",
        email="caller@example.com",
        service="http://localhost/realms/call-tenant",
        service_id="caller",
        display_name="Caller",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[user.id],
    )
    return tenant, user


def _start(
    session: Session,
    tenant: M8flowTenantModel,
    user: UserModel,
    resolver,
    submission_metadata: dict[str, str] | None = None,
):
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="parent-model",
            user_id=user.id,
            source_bpmn_xml=PARENT_BPMN,
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
            submission_metadata=submission_metadata,
            call_activity_resolver=resolver,
        ),
    )
    return definition, instance


def test_call_activity_waits_in_child_then_parent_completes(session: Session) -> None:
    tenant, user = _seed(session)
    resolver_contexts: list[api.CallActivityContext] = []

    def resolver(context: api.CallActivityContext):
        resolver_contexts.append(context)
        return api.CallActivityDefinition("child_process", CHILD_BPMN)

    _, instance = _start(session, tenant, user, resolver)
    assert instance.status == api.ProcessInstanceStatus.user_input_required
    assert len(resolver_contexts) == 1
    assert resolver_contexts[0].tenant_id == tenant.id
    tasks = api.execute_query(
        session,
        api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
    )
    assert len(tasks) == 1
    api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant.id,
            work_item_id=tasks[0].id,
            user_id=user.id,
        ),
    )
    api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=tenant.id,
            work_item_id=tasks[0].id,
            user_id=user.id,
        ),
    )
    refreshed = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant.id,
            process_instance_id=instance.id,
        ),
    )
    assert refreshed.status == api.ProcessInstanceStatus.complete
    assert len(resolver_contexts) == 1


def test_call_activity_loads_child_after_selecting_implicit_root() -> None:
    workflow = _build_workflow(
        bpmn_xml=PARENT_BPMN,
        dmn_xml=None,
        bpmn_process_id=None,
        call_activity_resolver=lambda _: api.CallActivityDefinition(
            "child_process", CHILD_BPMN
        ),
        call_activity_context=api.CallActivityContext("call-tenant", "", None, None),
    )

    assert workflow.spec.name == "parent_process"
    assert set(workflow.subprocess_specs) == {"child_process"}


def test_child_task_id_does_not_overwrite_parent_task_definition(
    session: Session,
) -> None:
    tenant, user = _seed(session)
    child_xml = CHILD_BPMN.replace("child_wait", "call_child")
    _, instance = _start(
        session,
        tenant,
        user,
        lambda _context: api.CallActivityDefinition("child_process", child_xml),
    )

    definitions = session.scalars(
        select(TaskDefinitionModel).where(
            TaskDefinitionModel.m8f_tenant_id == tenant.id,
            TaskDefinitionModel.bpmn_process_definition_id
            == instance.bpmn_process_definition_id,
            TaskDefinitionModel.bpmn_identifier.in_(
                ["call_child", "child_process::call_child"]
            ),
        )
    ).all()
    actual_names = {
        definition.bpmn_identifier: definition.bpmn_name
        for definition in definitions
    }
    assert actual_names == {
        "call_child": "Call child",
        "child_process::call_child": "Child approval",
    }
    [work_item] = api.execute_query(
        session,
        api.GetPendingTasksQuery(tenant_id=tenant.id, user_id=user.id),
    )
    assert work_item.task_name == "call_child"
    assert work_item.task_title == "Child approval"


def test_call_activity_passes_inputs_and_returns_child_outputs(
    session: Session,
) -> None:
    tenant, user = _seed(session)
    _, instance = _start(
        session,
        tenant,
        user,
        lambda _context: api.CallActivityDefinition(
            "child_process", MAPPED_CHILD_BPMN
        ),
        submission_metadata={"child_input": "hello"},
    )
    assert instance.status == api.ProcessInstanceStatus.complete
    serialized = json.loads(instance.workflow_state_json)
    assert "hello mapped" in json.dumps(serialized)
    assert serialized["data"]["child_output"] == "hello mapped"


@pytest.mark.parametrize(
    ("resolver", "message"),
    [
        (lambda _context: None, "No child process definition"),
        (
            lambda _context: [
                api.CallActivityDefinition("child_process", CHILD_BPMN),
                api.CallActivityDefinition("child_process", CHILD_BPMN),
            ],
            "Ambiguous child process definition",
        ),
    ],
)
def test_call_activity_resolution_errors_are_actionable(
    session: Session, resolver, message: str
) -> None:
    tenant, user = _seed(session)
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="parent-model",
            user_id=user.id,
            source_bpmn_xml=PARENT_BPMN,
        ),
    )
    with pytest.raises(CallActivityResolutionError, match=message):
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=user.id,
                call_activity_resolver=resolver,
            ),
        )


def test_call_activity_resolution_is_tenant_scoped_by_contract(
    session: Session,
) -> None:
    tenant, user = _seed(session)
    observed: list[str] = []

    def resolver(context: api.CallActivityContext):
        observed.append(context.tenant_id)
        # A host lookup should filter by the supplied tenant. A definition found
        # in another tenant is represented as unresolved to this execution.
        return None if context.tenant_id == tenant.id else api.CallActivityDefinition(
            "child_process", CHILD_BPMN
        )

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="parent-model",
            user_id=user.id,
            source_bpmn_xml=PARENT_BPMN,
        ),
    )
    with pytest.raises(
        CallActivityResolutionError, match="No child process definition"
    ):
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=user.id,
                call_activity_resolver=resolver,
            ),
        )
    assert observed == [tenant.id]


def test_child_service_failure_propagates_to_parent(session: Session) -> None:
    tenant, user = _seed(session)
    connector = _FailingConnector()
    registry = api.ServiceTaskRegistry(connectors=(connector,))
    with api.service_task_registry_scope(registry):
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=tenant.id,
                bpmn_identifier="failing-parent-model",
                user_id=user.id,
                source_bpmn_xml=PARENT_BPMN,
            ),
        )
        with pytest.raises(api.ServiceTaskExecutionError, match="demo/Fail"):
            api.execute_command(
                session,
                api.InitializeProcessInstanceFromDefinitionCommand(
                    tenant_id=tenant.id,
                    bpmn_process_definition_id=definition.id,
                    process_initiator_id=user.id,
                    call_activity_resolver=lambda _context: api.CallActivityDefinition(
                        "child_process", FAILING_CHILD_BPMN
                    ),
                ),
            )


def test_parent_without_call_activity_still_completes(session: Session) -> None:
    tenant, user = _seed(session)
    plain_bpmn = PARENT_BPMN.replace(
        '<bpmn:callActivity id="call_child" name="Call child" '
        'calledElement="child_process" />',
        '<bpmn:scriptTask id="call_child" name="Ordinary task">'
        "<bpmn:script>pass</bpmn:script></bpmn:scriptTask>",
    )
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="plain-model",
            user_id=user.id,
            source_bpmn_xml=plain_bpmn,
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
    assert instance.status == api.ProcessInstanceStatus.complete
