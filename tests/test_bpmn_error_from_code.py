"""BPMN errors raised from script and service-task code (M8F-675).

A script raises ``BpmnError("CODE")`` and a connector raises
``ServiceTaskExecutionError(..., error_code="CODE")``; the nearest matching
error boundary catches it and the activity is interrupted. Anything else stays
an ordinary task failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from xml.sax.saxutils import escape

import pytest
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_core.services.workflow_runtime import _restore_workflow

_NS = (
    'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"'
)


def _script(code: str) -> str:
    return (
        '<bpmn:scriptTask id="Work"><bpmn:incoming>Flow_1</bpmn:incoming>'
        "<bpmn:outgoing>Flow_2</bpmn:outgoing>"
        f"<bpmn:script>{escape(code)}</bpmn:script></bpmn:scriptTask>"
    )


def _subprocess(code: str) -> str:
    return (
        '<bpmn:subProcess id="Work"><bpmn:incoming>Flow_1</bpmn:incoming>'
        "<bpmn:outgoing>Flow_2</bpmn:outgoing>"
        '<bpmn:startEvent id="SubStart">'
        "<bpmn:outgoing>Flow_S1</bpmn:outgoing></bpmn:startEvent>"
        '<bpmn:scriptTask id="SubRaise"><bpmn:incoming>Flow_S1</bpmn:incoming>'
        "<bpmn:outgoing>Flow_S2</bpmn:outgoing>"
        f"<bpmn:script>{escape(code)}</bpmn:script></bpmn:scriptTask>"
        '<bpmn:endEvent id="SubEnd">'
        "<bpmn:incoming>Flow_S2</bpmn:incoming></bpmn:endEvent>"
        '<bpmn:sequenceFlow id="Flow_S1" sourceRef="SubStart" targetRef="SubRaise" />'
        '<bpmn:sequenceFlow id="Flow_S2" sourceRef="SubRaise" targetRef="SubEnd" />'
        "</bpmn:subProcess>"
    )


_SERVICE = (
    '<bpmn:serviceTask id="Work"><bpmn:extensionElements>'
    '<spiffworkflow:serviceTaskOperator id="demo/Lookup" resultVariable="lookup">'
    "<spiffworkflow:parameters /></spiffworkflow:serviceTaskOperator>"
    "</bpmn:extensionElements>"
    "<bpmn:incoming>Flow_1</bpmn:incoming><bpmn:outgoing>Flow_2</bpmn:outgoing>"
    "</bpmn:serviceTask>"
)


def _bpmn(work: str, catch_code: str | None, *, variable: str | None = None) -> str:
    """Start -> Work -> NormalPath -> end, with an error boundary on Work."""
    error = ""
    error_ref = ""
    if catch_code is not None:
        extension = (
            "<bpmn:extensionElements>"
            f"<spiffworkflow:variableName>{variable}</spiffworkflow:variableName>"
            "</bpmn:extensionElements>"
            if variable
            else ""
        )
        error = (
            f'<bpmn:error id="Err_1" errorCode="{catch_code}">{extension}</bpmn:error>'
        )
        error_ref = ' errorRef="Err_1"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {_NS} id="Defs" targetNamespace="urn:m8flow:m8f675">
  {error}
  <bpmn:process id="m8f675" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    {work}
    <bpmn:scriptTask id="NormalPath"><bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing><bpmn:script>normal_path = True</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:endEvent id="EndNormal"><bpmn:incoming>Flow_3</bpmn:incoming></bpmn:endEvent>
    <bpmn:boundaryEvent id="CatchError" attachedToRef="Work">
      <bpmn:outgoing>Flow_4</bpmn:outgoing><bpmn:errorEventDefinition{error_ref} />
    </bpmn:boundaryEvent>
    <bpmn:scriptTask id="HandleError"><bpmn:incoming>Flow_4</bpmn:incoming>
      <bpmn:outgoing>Flow_5</bpmn:outgoing>
      <bpmn:script>error_caught = True</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:endEvent id="EndHandled"><bpmn:incoming>Flow_5</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Work" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Work" targetRef="NormalPath" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="NormalPath" targetRef="EndNormal" />
    <bpmn:sequenceFlow id="Flow_4" sourceRef="CatchError" targetRef="HandleError" />
    <bpmn:sequenceFlow id="Flow_5" sourceRef="HandleError" targetRef="EndHandled" />
  </bpmn:process>
</bpmn:definitions>
"""


@dataclass
class _FailingConnector:
    error_code: str | None
    connector_key: str = "demo"

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return (
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key, command_name="Lookup"
            ),
        )

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        raise api.ServiceTaskExecutionError(
            "lookup failed with HTTP 404", error_code=self.error_code
        )


def _start(session: Session, bpmn_xml: str) -> ProcessInstanceModel:
    tenant = M8flowTenantModel(id="tenant-m8f675", name="M8F-675", slug="tenant-m8f675")
    user = UserModel(
        username="m8f675-admin",
        email="m8f675-admin@example.com",
        service="http://localhost:7002/realms/tenant-m8f675",
        service_id="m8f675-admin-keycloak",
        display_name="Admin",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant.id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="m8f675",
            user_id=user.id,
            bpmn_name="M8F-675",
            source_bpmn_xml=bpmn_xml,
            created_at=datetime.fromtimestamp(10, UTC),
            updated_at=datetime.fromtimestamp(10, UTC),
        ),
    )
    # As a host does before starting: core saves start failures on its own
    # session, which only sees committed rows.
    session.commit()
    return api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
            started_at=datetime.fromtimestamp(20, UTC),
        ),
    )


def _assert_caught(process_instance: ProcessInstanceModel) -> dict[str, object]:
    assert process_instance.status == api.ProcessInstanceStatus.complete
    workflow = _restore_workflow(process_instance.workflow_state_json)
    assert workflow.data.get("error_caught") is True
    assert "normal_path" not in workflow.data
    work = [t for t in workflow.get_tasks() if t.task_spec.name == "Work"]
    assert [t.state for t in work] == [TaskState.CANCELLED]
    return workflow.data


def test_script_bpmn_error_is_caught_by_matching_boundary(session: Session) -> None:
    _assert_caught(_start(session, _bpmn(_script('raise BpmnError("E1")'), "E1")))


def test_boundary_without_error_code_catches_any_bpmn_error(session: Session) -> None:
    _assert_caught(_start(session, _bpmn(_script('raise BpmnError("ANY")'), None)))


def test_bpmn_error_leaves_subprocess_to_its_boundary(session: Session) -> None:
    _assert_caught(_start(session, _bpmn(_subprocess('raise BpmnError("E1")'), "E1")))


def test_bpmn_error_from_a_loop_instance_reaches_the_activity_boundary(
    session: Session,
) -> None:
    work = _script('raise BpmnError("E1")').replace(
        "<bpmn:script>",
        "<bpmn:multiInstanceLoopCharacteristics>"
        "<bpmn:loopCardinality>2</bpmn:loopCardinality>"
        "</bpmn:multiInstanceLoopCharacteristics><bpmn:script>",
    )
    process_instance = _start(session, _bpmn(work, "E1"))
    assert process_instance.status == api.ProcessInstanceStatus.complete
    workflow = _restore_workflow(process_instance.workflow_state_json)
    assert workflow.data.get("error_caught") is True
    assert "normal_path" not in workflow.data
    assert not workflow.get_tasks(state=TaskState.ERROR)


def test_boundary_variable_receives_error_code_and_message(session: Session) -> None:
    data = _assert_caught(
        _start(
            session,
            _bpmn(
                _script('raise BpmnError("E1", "Order rejected")'), "E1", variable="err"
            ),
        )
    )
    assert data["err"] == {"code": "E1", "message": "Order rejected"}


def test_bpmn_error_with_no_matching_catcher_fails_the_task(session: Session) -> None:
    with pytest.raises(Exception, match="E2"):
        _start(session, _bpmn(_script('raise BpmnError("E2")'), "E1"))


def test_plain_exception_is_not_a_bpmn_error(session: Session) -> None:
    with pytest.raises(Exception, match="E1") as exc_info:
        _start(session, _bpmn(_script('raise Exception("E1")'), "E1"))
    assert "BpmnError" not in type(exc_info.value).__name__


def test_connector_error_code_is_caught_by_matching_boundary(session: Session) -> None:
    registry = api.ServiceTaskRegistry(connectors=(_FailingConnector("HttpError404"),))
    with api.service_task_registry_scope(registry):
        _assert_caught(_start(session, _bpmn(_SERVICE, "HttpError404")))


def test_connector_error_code_without_catcher_stays_a_service_task_failure(
    session: Session,
) -> None:
    registry = api.ServiceTaskRegistry(connectors=(_FailingConnector("HttpError500"),))
    with api.service_task_registry_scope(registry):
        with pytest.raises(api.ServiceTaskExecutionError) as exc_info:
            _start(session, _bpmn(_SERVICE, "HttpError404"))
    assert exc_info.value.error_code == "HttpError500"
    # Core saved the Error on its own session; drop this session's stale copy.
    session.expire_all()
    instances = api.execute_query(
        session, api.ListProcessInstancesQuery(tenant_id="tenant-m8f675")
    )
    assert [pi.status for pi in instances] == [api.ProcessInstanceStatus.error]


def test_script_failure_at_start_saves_a_snapshot_of_the_failed_task(
    engine, session: Session
) -> None:
    """Core moves an instance to Error for a service-task failure at start, but
    a failing script left nothing behind: no workflow state, so no diagram of
    where it failed. The snapshot is saved; the status is the host's call."""
    if engine.dialect.name == "sqlite":
        pytest.xfail(
            "SQLite cannot reliably validate autonomous failure-state "
            "persistence while the outer failing transaction is still open."
        )
    with pytest.raises(Exception, match="boom"):
        _start(session, _bpmn(_script('raise Exception("boom")'), "E1"))

    session.expire_all()
    instance = session.scalars(select(ProcessInstanceModel)).one()
    assert instance.status == api.ProcessInstanceStatus.not_started
    assert instance.workflow_state_json is not None
    workflow = _restore_workflow(instance.workflow_state_json)
    work = [t for t in workflow.get_tasks() if t.task_spec.name == "Work"]
    assert [t.state for t in work] == [TaskState.ERROR]


_PARALLEL_SIBLING_BOUNDARY = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {_NS} id="Defs" targetNamespace="urn:m8flow:m8f675">
  <bpmn:error id="Err_1" errorCode="E1" />
  <bpmn:process id="m8f675_parallel" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>f0</bpmn:outgoing></bpmn:startEvent>
    <bpmn:parallelGateway id="Split">
      <bpmn:incoming>f0</bpmn:incoming>
      <bpmn:outgoing>fb</bpmn:outgoing><bpmn:outgoing>fa</bpmn:outgoing>
    </bpmn:parallelGateway>
    <bpmn:userTask id="Review"><bpmn:incoming>fb</bpmn:incoming>
      <bpmn:outgoing>fb2</bpmn:outgoing></bpmn:userTask>
    <bpmn:endEvent id="EndB"><bpmn:incoming>fb2</bpmn:incoming></bpmn:endEvent>
    <bpmn:boundaryEvent id="CatchOnReview" attachedToRef="Review">
      <bpmn:outgoing>fh</bpmn:outgoing><bpmn:errorEventDefinition errorRef="Err_1" />
    </bpmn:boundaryEvent>
    <bpmn:scriptTask id="HandleError"><bpmn:incoming>fh</bpmn:incoming>
      <bpmn:outgoing>fh2</bpmn:outgoing><bpmn:script>error_caught = True</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:endEvent id="EndHandled"><bpmn:incoming>fh2</bpmn:incoming></bpmn:endEvent>
    <bpmn:scriptTask id="Prepare"><bpmn:incoming>fa</bpmn:incoming>
      <bpmn:outgoing>fa1</bpmn:outgoing><bpmn:script>prepared = True</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:scriptTask id="Raise"><bpmn:incoming>fa1</bpmn:incoming>
      <bpmn:outgoing>fa2</bpmn:outgoing><bpmn:script>raise BpmnError("E1")</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:endEvent id="EndA"><bpmn:incoming>fa2</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="f0" sourceRef="StartEvent_1" targetRef="Split" />
    <bpmn:sequenceFlow id="fb" sourceRef="Split" targetRef="Review" />
    <bpmn:sequenceFlow id="fb2" sourceRef="Review" targetRef="EndB" />
    <bpmn:sequenceFlow id="fa" sourceRef="Split" targetRef="Prepare" />
    <bpmn:sequenceFlow id="fa1" sourceRef="Prepare" targetRef="Raise" />
    <bpmn:sequenceFlow id="fa2" sourceRef="Raise" targetRef="EndA" />
    <bpmn:sequenceFlow id="fh" sourceRef="CatchOnReview" targetRef="HandleError" />
    <bpmn:sequenceFlow id="fh2" sourceRef="HandleError" targetRef="EndHandled" />
  </bpmn:process>
</bpmn:definitions>
"""


def test_bpmn_error_is_not_caught_by_a_boundary_on_a_parallel_sibling(
    session: Session,
) -> None:
    """Only a boundary on the activity that raised (or on a sub-process hosting
    it) may catch. Here the only E1 boundary is on a user task in the other
    parallel branch, already waiting when the script raises; it must not catch."""
    with pytest.raises(Exception, match="E1") as exc_info:
        _start(session, _PARALLEL_SIBLING_BOUNDARY)
    assert "BpmnError" in str(exc_info.value)


_ERROR_EVENT_SUBPROCESS = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {_NS} id="Defs" targetNamespace="urn:m8flow:m8f675">
  <bpmn:error id="Err_1" errorCode="E1" />
  <bpmn:process id="m8f675_esp" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing></bpmn:startEvent>
    {_script('raise BpmnError("E1")')}
    <bpmn:scriptTask id="NormalPath"><bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing><bpmn:script>normal_path = True</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:endEvent id="EndNormal"><bpmn:incoming>Flow_3</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Work" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Work" targetRef="NormalPath" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="NormalPath" targetRef="EndNormal" />
    <bpmn:subProcess id="OnError" triggeredByEvent="true">
      <bpmn:startEvent id="ErrorStart"><bpmn:outgoing>Flow_E1</bpmn:outgoing>
        <bpmn:errorEventDefinition errorRef="Err_1" /></bpmn:startEvent>
      <bpmn:scriptTask id="HandleError"><bpmn:incoming>Flow_E1</bpmn:incoming>
        <bpmn:outgoing>Flow_E2</bpmn:outgoing>
        <bpmn:script>error_caught = True</bpmn:script>
      </bpmn:scriptTask>
      <bpmn:endEvent id="ErrorEnd">
        <bpmn:incoming>Flow_E2</bpmn:incoming></bpmn:endEvent>
      <bpmn:sequenceFlow id="Flow_E1" sourceRef="ErrorStart" targetRef="HandleError" />
      <bpmn:sequenceFlow id="Flow_E2" sourceRef="HandleError" targetRef="ErrorEnd" />
    </bpmn:subProcess>
  </bpmn:process>
</bpmn:definitions>
"""


@pytest.mark.xfail(
    strict=True,
    reason="Error event subprocesses need M8F-665's routing (PR #52); once it "
    "lands, code-raised errors go through _throw_to_nearest_catcher.",
)
def test_script_bpmn_error_starts_a_matching_error_event_subprocess(
    session: Session,
) -> None:
    _assert_caught(_start(session, _ERROR_EVENT_SUBPROCESS))
