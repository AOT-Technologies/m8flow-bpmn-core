from __future__ import annotations

import ast
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import (
    HumanTaskUserAddedBy,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel

EXAMPLE_BPMN_PATH = Path(__file__).with_name("fixtures") / "conditional-approval.bpmn"
EXAMPLE_DMN_PATH = Path(__file__).with_name("fixtures") / "check_eligibility.dmn"

CONDITIONAL_APPROVAL_PROCESS_ID = "Process_conditional_approval_8qpy9gh"
CONDITIONAL_APPROVAL_TASK_IDS = {
    "script": "Activity_09regp6",
    "submit": "Activity_0qoxmh9",
    "manager_review": "Activity_0b1dd0g",
    "decision": "Activity_08g01ho",
    "finance_review": "Activity_1uha89x",
}

BPMN_NAMESPACES = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "spiffworkflow": "http://spiffworkflow.org/bpmn/schema/1.0/core",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


@dataclass(frozen=True, slots=True)
class ConditionalApprovalScenario:
    name: str
    amount: int
    manager_decision: str
    finance_decision: str | None


@dataclass(frozen=True, slots=True)
class ConditionalApprovalContext:
    tenant: M8flowTenantModel
    process_instance: ProcessInstanceModel
    users: dict[str, UserModel]
    task_definitions: dict[str, TaskDefinitionModel]


SCENARIOS = [
    ConditionalApprovalScenario(
        name="manager-approved-auto-approved",
        amount=500,
        manager_decision="Approved",
        finance_decision=None,
    ),
    ConditionalApprovalScenario(
        name="manager-approved-finance-approved",
        amount=1_500,
        manager_decision="Approved",
        finance_decision="Approved",
    ),
    ConditionalApprovalScenario(
        name="manager-approved-finance-rejected",
        amount=1_500,
        manager_decision="Approved",
        finance_decision="Rejected",
    ),
    ConditionalApprovalScenario(
        name="manager-rejected",
        amount=1_500,
        manager_decision="Rejected",
        finance_decision=None,
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_conditional_approval_workflow_poc_supports_lanes_and_assignments(
    session: Session,
    scenario: ConditionalApprovalScenario,
) -> None:
    lane_owners = _assert_conditional_approval_bpmn_shape()
    _assert_conditional_approval_dmn_shape()
    # Step 1: the requester submits the expense claim and starts the workflow.
    context = _seed_conditional_approval_workflow(session, scenario, lane_owners)

    assert context.task_definitions["script"].typename == "ScriptTask"
    assert context.task_definitions["script"].is_human_task() is False
    assert context.task_definitions["script"].properties_json["manual"] is False
    assert context.task_definitions["submit"].typename == "UserTask"
    assert context.task_definitions["submit"].is_human_task() is True
    assert context.task_definitions["submit"].properties_json["manual"] is True
    assert context.task_definitions["manager_review"].properties_json["lane"] == (
        "Manager"
    )
    assert context.task_definitions["finance_review"].properties_json["lane"] == (
        "Finance"
    )
    assert context.task_definitions["decision"].typename == "BusinessRuleTask"
    assert context.task_definitions["decision"].is_human_task() is False

    session.expire_all()
    process_instance = api.execute_command(
        session,
        api.GetProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
        ),
    )
    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert process_instance.start_in_seconds == 100
    assert process_instance.workflow_state_json is not None

    # Step 1 continued: the submit task should be pending for the requester.
    submit_pending_tasks = api.execute_command(
        session,
        api.GetPendingTasksCommand(
            tenant_id=context.tenant.id,
            user_id=context.users["requester"].id,
        ),
    )
    assert len(submit_pending_tasks) == 1
    submit_task = submit_pending_tasks[0]
    assert submit_task.task_name == CONDITIONAL_APPROVAL_TASK_IDS["submit"]
    assert submit_task.task_title == "Submit Expense Claim"
    assert submit_task.lane_name is None
    assert submit_task.lane_assignment_id is None
    assert submit_task.task_status == "READY"
    assert submit_task.task_model is not None
    assert submit_task.task_model.task_definition is not None
    assert (
        submit_task.task_model.task_definition.bpmn_identifier
        == CONDITIONAL_APPROVAL_TASK_IDS["submit"]
    )
    assert submit_task.json_metadata is not None
    assert submit_task.json_metadata["lane_owners"] == lane_owners
    assert _assignment_summary(submit_task) == [
        ("requester", HumanTaskUserAddedBy.process_initiator.value),
    ]

    submit_claimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
        ),
    )
    assert submit_claimed_task.actual_owner_id == context.users["requester"].id
    assert submit_claimed_task.task_status == "CLAIMED"
    submit_completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=submit_task.id,
            user_id=context.users["requester"].id,
            completed_at_in_seconds=110,
            task_payload={
                "expense_date": "2026-04-01",
                "expense_type": "Travel",
                "amount": str(scenario.amount),
                "description": "Trip to LA",
            },
        ),
    )
    assert submit_completed_task.task_model.future_task is not None
    assert submit_completed_task.task_model.future_task.completed is True
    assert submit_completed_task.completed is True
    assert submit_completed_task.task_status == "COMPLETED"
    session.expire_all()
    process_instance = api.execute_command(
        session,
        api.GetProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
        ),
    )
    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert process_instance.end_in_seconds is None

    # Step 2: manager-lane users should now see the review task to claim.
    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert process_instance.end_in_seconds is None

    # Step 3: the manager claims and completes the review task.
    manager_pending_tasks = api.execute_command(
        session,
        api.GetPendingTasksCommand(
            tenant_id=context.tenant.id,
            user_id=context.users["manager"].id,
        ),
    )
    assert len(manager_pending_tasks) == 1
    manager_task = manager_pending_tasks[0]
    assert manager_task.task_name == CONDITIONAL_APPROVAL_TASK_IDS["manager_review"]
    assert manager_task.task_title == "Review Expense Claim"
    assert manager_task.lane_name == "Manager"
    assert manager_task.lane_assignment_id == api.resolve_lane_assignment_id("Manager")
    # Manager ownership should be resolved from the lane_owners mapping.
    assert manager_task.task_model is not None
    assert manager_task.task_model.task_definition is not None
    assert (
        manager_task.task_model.task_definition.bpmn_identifier
        == CONDITIONAL_APPROVAL_TASK_IDS["manager_review"]
    )
    assert manager_task.json_metadata is not None
    assert manager_task.json_metadata["lane_owners"] == lane_owners
    assert _assignment_summary(manager_task) == [
        ("manager", HumanTaskUserAddedBy.lane_owner.value),
        ("reviewer", HumanTaskUserAddedBy.lane_owner.value),
    ]
    for username in ("manager", "reviewer"):
        assert [
            item.id
            for item in api.execute_command(
                session,
                api.GetPendingTasksCommand(
                    tenant_id=context.tenant.id,
                    user_id=context.users[username].id,
                ),
            )
        ] == [manager_task.id]

    manager_claimed_task = api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=manager_task.id,
            user_id=context.users["manager"].id,
        ),
    )
    assert manager_claimed_task.actual_owner_id == context.users["manager"].id
    assert manager_claimed_task.task_status == "CLAIMED"
    manager_completed_task = api.execute_command(
        session,
        api.CompleteTaskCommand(
            tenant_id=context.tenant.id,
            human_task_id=manager_task.id,
            user_id=context.users["manager"].id,
            completed_at_in_seconds=120,
            task_payload={"decision": scenario.manager_decision},
        ),
    )
    assert manager_completed_task.task_model.future_task is not None
    assert manager_completed_task.task_model.future_task.completed is True
    assert manager_completed_task.task_model.end_in_seconds == 120

    session.expire_all()
    process_instance = api.execute_command(
        session,
        api.GetProcessInstanceCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
        ),
    )
    # Step 4: rejected claims end immediately, and auto-approved claims stop here too.
    if scenario.manager_decision == "Approved" and scenario.amount > 500:
        assert process_instance.status == api.ProcessInstanceStatus.user_input_required
        assert process_instance.end_in_seconds is None
    else:
        assert process_instance.status == api.ProcessInstanceStatus.complete
        assert process_instance.end_in_seconds == 120

    finance_task: HumanTaskModel | None = None
    if scenario.manager_decision == "Approved" and scenario.amount > 500:
        # Step 5: non auto-approved claims should appear in the Finance lane.
        finance_pending_tasks = api.execute_command(
            session,
            api.GetPendingTasksCommand(
                tenant_id=context.tenant.id,
                user_id=context.users["finance"].id,
            ),
        )
        assert len(finance_pending_tasks) == 1
        finance_task = finance_pending_tasks[0]
        assert finance_task.task_name == CONDITIONAL_APPROVAL_TASK_IDS["finance_review"]
        assert finance_task.task_title == "Review Expense Claim (Finance)"
        assert finance_task.lane_name == "Finance"
        assert finance_task.lane_assignment_id == api.resolve_lane_assignment_id(
            "Finance"
        )
        assert finance_task.task_model is not None
        assert finance_task.task_model.task_definition is not None
        assert (
            finance_task.task_model.task_definition.bpmn_identifier
            == CONDITIONAL_APPROVAL_TASK_IDS["finance_review"]
        )
        assert finance_task.json_metadata is not None
        assert finance_task.json_metadata["lane_owners"] == lane_owners
        assert _assignment_summary(finance_task) == [
            ("james", HumanTaskUserAddedBy.lane_owner.value),
        ]
        assert [
            item.id
            for item in api.execute_command(
                session,
                api.GetPendingTasksCommand(
                    tenant_id=context.tenant.id,
                    user_id=context.users["finance"].id,
                ),
            )
        ] == [finance_task.id]

        # Step 6: Finance claims the task and decides whether it is approved.
        finance_claimed_task = api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=context.tenant.id,
                human_task_id=finance_task.id,
                user_id=context.users["finance"].id,
            ),
        )
        assert finance_claimed_task.actual_owner_id == context.users["finance"].id
        assert finance_claimed_task.task_status == "CLAIMED"
        finance_completed_task = api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=context.tenant.id,
                human_task_id=finance_task.id,
                user_id=context.users["finance"].id,
                completed_at_in_seconds=130,
                task_payload={
                    "finance_decision": scenario.finance_decision or "Approved"
                },
            ),
        )
        assert finance_completed_task.task_model.future_task is not None
        assert finance_completed_task.task_model.future_task.completed is True
        assert finance_completed_task.task_model.end_in_seconds == 130
        session.expire_all()
        process_instance = api.execute_command(
            session,
            api.GetProcessInstanceCommand(
                tenant_id=context.tenant.id,
                process_instance_id=context.process_instance.id,
            ),
        )
    else:
        assert (
            api.execute_command(
                session,
                api.GetPendingTasksCommand(
                    tenant_id=context.tenant.id,
                    user_id=context.users["finance"].id,
                ),
            )
            == []
        )

    # End: once the taken branch completes, the workflow should be complete.
    assert process_instance.status == api.ProcessInstanceStatus.complete
    assert process_instance.end_in_seconds == (130 if finance_task else 120)

    metadata_rows = api.execute_command(
        session,
        api.GetProcessInstanceMetadataCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
        ),
    )
    metadata_map = {item.key: item.value for item in metadata_rows}
    expected_metadata = {
        "amount": str(scenario.amount),
        "description": "Trip to LA",
        "expense_date": "2026-04-01",
        "expense_type": "Travel",
        "decision": scenario.manager_decision,
    }
    if scenario.finance_decision is not None:
        expected_metadata["finance_decision"] = scenario.finance_decision
    assert metadata_map == expected_metadata

    events = api.execute_command(
        session,
        api.GetProcessInstanceEventsCommand(
            tenant_id=context.tenant.id,
            process_instance_id=context.process_instance.id,
        ),
    )
    expected_events = [
        ProcessInstanceEventType.process_instance_created.value,
        ProcessInstanceEventType.task_completed.value,
        ProcessInstanceEventType.task_completed.value,
    ]
    if finance_task is not None:
        expected_events.append(ProcessInstanceEventType.task_completed.value)
    expected_events.append(ProcessInstanceEventType.process_instance_completed.value)
    assert [event.event_type for event in events] == expected_events


def _seed_conditional_approval_workflow(
    session: Session,
    scenario: ConditionalApprovalScenario,
    lane_owners: dict[str, list[str]],
) -> ConditionalApprovalContext:
    bpmn_xml = EXAMPLE_BPMN_PATH.read_text(encoding="utf-8")
    dmn_xml = EXAMPLE_DMN_PATH.read_text(encoding="utf-8")
    service_url = "http://localhost:7002/realms/conditional-approval"

    users = {
        "manager": UserModel(
            username="manager",
            email="manager@example.com",
            service=service_url,
            service_id="manager-keycloak",
            display_name="Manager",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "reviewer": UserModel(
            username="reviewer",
            email="reviewer@example.com",
            service=service_url,
            service_id="reviewer-keycloak",
            display_name="Reviewer",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "finance": UserModel(
            username="james",
            email="james@example.com",
            service=service_url,
            service_id="finance-keycloak",
            display_name="Finance",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
        "requester": UserModel(
            username="requester",
            email="requester@example.com",
            service=service_url,
            service_id="requester-keycloak",
            display_name="Requester",
            created_at_in_seconds=1,
            updated_at_in_seconds=1,
        ),
    }

    tenant = M8flowTenantModel(
        id="tenant-conditional-approval",
        name="Conditional Approval Tenant",
        slug="conditional-approval",
    )
    session.add(tenant)
    session.add_all(users.values())
    session.flush()

    # Import the stored BPMN/DMN definition first so the workflow can start by id.
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="conditional-approval-poc",
            bpmn_name="Conditional Approval POC",
            source_bpmn_xml=bpmn_xml,
            source_dmn_xml=dmn_xml,
            properties_json={
                "version": 1,
                "flow": "conditional_approval",
                "source_bpmn_fixture": EXAMPLE_BPMN_PATH.name,
                "lane_owners": lane_owners,
                "scenario_name": scenario.name,
            },
            bpmn_version_control_type="git",
            bpmn_version_control_identifier="main",
            created_at_in_seconds=90,
            updated_at_in_seconds=90,
        ),
    )
    assert definition.source_bpmn_xml == bpmn_xml
    assert definition.source_dmn_xml == dmn_xml
    assert definition.properties_json["scenario_name"] == scenario.name

    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=users["requester"].id,
            summary=f"Scenario: {scenario.name}",
            process_version=1,
            started_at_in_seconds=100,
            bpmn_process_id=CONDITIONAL_APPROVAL_PROCESS_ID,
        ),
    )
    assert process_instance.status == api.ProcessInstanceStatus.user_input_required
    assert process_instance.workflow_state_json is not None

    session.refresh(process_instance)
    task_definitions = _load_conditional_approval_task_definitions(
        session,
        tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
    )

    return ConditionalApprovalContext(
        tenant=tenant,
        process_instance=process_instance,
        users=users,
        task_definitions=task_definitions,
    )


def _load_conditional_approval_task_definitions(
    session: Session,
    *,
    tenant_id: str,
    bpmn_process_definition_id: int,
) -> dict[str, TaskDefinitionModel]:
    stmt = select(TaskDefinitionModel).where(
        TaskDefinitionModel.m8f_tenant_id == tenant_id,
        TaskDefinitionModel.bpmn_process_definition_id == bpmn_process_definition_id,
    )
    task_definitions = {
        task_definition.bpmn_identifier: task_definition
        for task_definition in session.scalars(stmt).all()
    }
    expected_ids = set(CONDITIONAL_APPROVAL_TASK_IDS.values())
    assert expected_ids.issubset(task_definitions)
    return {
        "script": task_definitions[CONDITIONAL_APPROVAL_TASK_IDS["script"]],
        "submit": task_definitions[CONDITIONAL_APPROVAL_TASK_IDS["submit"]],
        "manager_review": task_definitions[
            CONDITIONAL_APPROVAL_TASK_IDS["manager_review"]
        ],
        "decision": task_definitions[CONDITIONAL_APPROVAL_TASK_IDS["decision"]],
        "finance_review": task_definitions[
            CONDITIONAL_APPROVAL_TASK_IDS["finance_review"]
        ],
    }


def _assignment_summary(human_task: HumanTaskModel) -> list[tuple[str, str | None]]:
    return sorted(
        (assignment.user.username, assignment.added_by)
        for assignment in human_task.human_task_users
    )


def _assert_conditional_approval_bpmn_shape() -> dict[str, list[str]]:
    tree = ET.parse(EXAMPLE_BPMN_PATH)
    root = tree.getroot()

    process = root.find(
        f".//bpmn:process[@id='{CONDITIONAL_APPROVAL_PROCESS_ID}']",
        BPMN_NAMESPACES,
    )
    assert process is not None

    lane_set = process.find("bpmn:laneSet", BPMN_NAMESPACES)
    assert lane_set is not None

    lane_refs: dict[str, list[str]] = {}
    for lane in lane_set.findall("bpmn:lane", BPMN_NAMESPACES):
        lane_name = lane.get("name") or ""
        lane_refs[lane_name] = [
            ref.text or "" for ref in lane.findall("bpmn:flowNodeRef", BPMN_NAMESPACES)
        ]

    assert lane_refs["Manager"] == [
        "Activity_0b1dd0g",
        "Gateway_0dhl21r",
        "Event_0mumwd3",
        "Gateway_1pdzbsj",
        "Event_1ynhak2",
        "Activity_08g01ho",
    ]
    assert lane_refs["Finance"] == [
        "Gateway_0bak68o",
        "Event_063qbbs",
        "Event_0jl40tz",
        "Activity_1uha89x",
    ]
    assert lane_refs[""] == [
        "Event_0jqbb0y",
        "Activity_09regp6",
        "Activity_0qoxmh9",
    ]

    script_task = process.find(
        "bpmn:scriptTask[@id='Activity_09regp6']",
        BPMN_NAMESPACES,
    )
    assert script_task is not None
    assert script_task.get("name") == "Determine Expense Approvers"
    lane_owners = _extract_lane_owners(script_task)
    assert lane_owners == {
        "Manager": ["manager", "reviewer"],
        "Finance": ["james"],
    }

    assert _extract_spiff_properties(root, "userTask", "Activity_0qoxmh9") == {
        "formJsonSchemaFilename": "expense-request-schema.json",
        "formUiSchemaFilename": "expense-request-uischema.json",
    }
    assert _extract_spiff_properties(root, "userTask", "Activity_0b1dd0g") == {
        "formJsonSchemaFilename": "manage-claim-approval-schema.json",
        "formUiSchemaFilename": "manage-claim-approval-uischema.json",
    }
    assert _extract_spiff_properties(root, "userTask", "Activity_1uha89x") == {
        "formJsonSchemaFilename": "finance-approval-form-schema.json",
        "formUiSchemaFilename": "finance-approval-form-uischema.json",
    }
    assert _extract_called_decision_id(root, "Activity_08g01ho") == (
        "check_eligibility"
    )

    assert _extract_condition_text(root, "Flow_07e48pm") == 'decision == "Rejected"'
    assert _extract_condition_text(root, "Flow_1rj2fuy") == 'decision == "Approved"'
    assert _extract_condition_text(root, "Flow_11egx2x") == "is_eligible == True"
    assert _extract_condition_text(root, "Flow_0dcwgro") == "is_eligible == False"
    return lane_owners


def _assert_conditional_approval_dmn_shape() -> None:
    tree = ET.parse(EXAMPLE_DMN_PATH)
    root = tree.getroot()

    dmn_namespaces = {
        "dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/",
        "dmndi": "https://www.omg.org/spec/DMN/20191111/DMNDI/",
        "dc": "http://www.omg.org/spec/DMN/20180521/DC/",
    }

    decision = root.find(".//dmn:decision[@id='check_eligibility']", dmn_namespaces)
    assert decision is not None
    assert decision.get("name") == "Check Eligibility"

    decision_table = decision.find("dmn:decisionTable", dmn_namespaces)
    assert decision_table is not None

    input_expression = decision_table.find(
        "dmn:input/dmn:inputExpression",
        dmn_namespaces,
    )
    assert input_expression is not None
    assert input_expression.get("typeRef") == "integer"
    input_text = input_expression.find("dmn:text", dmn_namespaces)
    assert input_text is not None
    assert "".join(input_text.itertext()).strip() == "amount"

    output = decision_table.find("dmn:output", dmn_namespaces)
    assert output is not None
    assert output.get("name") == "is_eligible"
    assert output.get("typeRef") == "boolean"

    rules = decision_table.findall("dmn:rule", dmn_namespaces)
    assert len(rules) == 2
    first_rule_input = rules[0].find("dmn:inputEntry/dmn:text", dmn_namespaces)
    first_rule_output = rules[0].find("dmn:outputEntry/dmn:text", dmn_namespaces)
    second_rule_input = rules[1].find("dmn:inputEntry/dmn:text", dmn_namespaces)
    second_rule_output = rules[1].find("dmn:outputEntry/dmn:text", dmn_namespaces)
    assert first_rule_input is not None
    assert first_rule_output is not None
    assert second_rule_input is not None
    assert second_rule_output is not None
    assert "".join(first_rule_input.itertext()).strip() == "<= 500"
    assert "".join(first_rule_output.itertext()).strip() == "True"
    assert "".join(second_rule_input.itertext()).strip() == "> 500"
    assert "".join(second_rule_output.itertext()).strip() == "False"


def _extract_lane_owners(script_task: ET.Element) -> dict[str, list[str]]:
    script_node = script_task.find("bpmn:script", BPMN_NAMESPACES)
    assert script_node is not None
    script_text = textwrap.dedent("".join(script_node.itertext())).strip()
    module = ast.parse(script_text)
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if isinstance(target, ast.Name) and target.id == "lane_owners":
            lane_owners = ast.literal_eval(statement.value)
            assert isinstance(lane_owners, dict)
            return lane_owners
    raise AssertionError("lane_owners assignment was not found in the script task")


def _extract_spiff_properties(
    root: ET.Element,
    element_tag: str,
    element_id: str,
) -> dict[str, str]:
    node = root.find(f".//bpmn:{element_tag}[@id='{element_id}']", BPMN_NAMESPACES)
    assert node is not None
    properties: dict[str, str] = {}
    for property_node in node.findall(
        ".//spiffworkflow:property",
        BPMN_NAMESPACES,
    ):
        name = property_node.get("name")
        value = property_node.get("value")
        assert name is not None
        assert value is not None
        properties[name] = value
    return properties


def _extract_called_decision_id(root: ET.Element, element_id: str) -> str:
    node = root.find(f".//bpmn:businessRuleTask[@id='{element_id}']", BPMN_NAMESPACES)
    assert node is not None
    decision_node = node.find(
        "bpmn:extensionElements/spiffworkflow:calledDecisionId",
        BPMN_NAMESPACES,
    )
    assert decision_node is not None
    return "".join(decision_node.itertext()).strip()


def _extract_condition_text(root: ET.Element, element_id: str) -> str:
    node = root.find(f".//bpmn:sequenceFlow[@id='{element_id}']", BPMN_NAMESPACES)
    assert node is not None
    condition_node = node.find("bpmn:conditionExpression", BPMN_NAMESPACES)
    assert condition_node is not None
    return "".join(condition_node.itertext()).strip()
