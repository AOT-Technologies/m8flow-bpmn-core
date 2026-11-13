"""Multi-instance user tasks through claim and complete (M8F-640).

A loop iteration's form submission belongs to that iteration: it leaves only
through the loop's output mapping, and never becomes process metadata that
core re-applies to later tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.models.work_item import WorkItemModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

_DEFINITIONS = (
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'id="Defs_review" targetNamespace="http://bpmn.io/schema/bpmn">'
    '<bpmn:process id="Process_review" isExecutable="true">{body}</bpmn:process>'
    "</bpmn:definitions>"
)
_LOOP = (
    "<bpmn:multiInstanceLoopCharacteristics>"
    "<bpmn:loopDataInputRef>items</bpmn:loopDataInputRef>"
    "<bpmn:loopDataOutputRef>out</bpmn:loopDataOutputRef>"
    '<bpmn:inputDataItem id="it" name="it"/>'
    '<bpmn:outputDataItem id="res" name="res"/>'
    "{condition}</bpmn:multiInstanceLoopCharacteristics>"
)


class _Run:
    def __init__(self, session: Session, body: str) -> None:
        self.session = session
        self.tenant = M8flowTenantModel(
            id="mi-review", name="Reviews", slug="mi-review"
        )
        self.user = UserModel(
            username="reviewer640",
            email="reviewer640@example.com",
            service="http://localhost/realms/mi-review",
            service_id="reviewer640",
            display_name="Reviewer",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add_all([self.tenant, self.user])
        session.flush()
        ensure_v1_role(
            session,
            tenant_id=self.tenant.id,
            role_name=ROLE_ADMIN,
            user_ids=[self.user.id],
        )
        definition = api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=self.tenant.id,
                bpmn_identifier="review",
                user_id=self.user.id,
                source_bpmn_xml=_DEFINITIONS.format(body=body),
            ),
        )
        self.instance_id = api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=self.tenant.id,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=self.user.id,
            ),
        ).id

    def pending(self) -> list[WorkItemModel]:
        # Work items are created in input order, so their ids follow it.
        items = api.execute_query(
            self.session,
            api.GetPendingTasksQuery(tenant_id=self.tenant.id, user_id=self.user.id),
        )
        return sorted(items, key=lambda item: item.id)

    def complete(self, work_item: WorkItemModel, payload: dict[str, str]) -> None:
        ids = {
            "tenant_id": self.tenant.id,
            "work_item_id": work_item.id,
            "user_id": self.user.id,
        }
        api.execute_command(self.session, api.ClaimTaskCommand(**ids))
        api.execute_command(
            self.session, api.CompleteTaskCommand(**ids, task_payload=payload)
        )

    def status(self) -> str:
        return str(
            api.execute_query(
                self.session,
                api.GetProcessInstanceQuery(
                    tenant_id=self.tenant.id, process_instance_id=self.instance_id
                ),
            ).status
        )

    def metadata(self) -> dict[str, str]:
        rows = api.execute_query(
            self.session,
            api.GetProcessInstanceMetadataQuery(
                tenant_id=self.tenant.id, process_instance_id=self.instance_id
            ),
        )
        return {row.key: row.value for row in rows}


def _flows(*pairs: tuple[str, str]) -> str:
    return "".join(
        f'<bpmn:sequenceFlow id="f{n}" sourceRef="{a}" targetRef="{b}"/>'
        for n, (a, b) in enumerate(pairs)
    )


_START = (
    '<bpmn:startEvent id="StartEvent_1"/>'
    "<bpmn:scriptTask id=\"prep\"><bpmn:script>items = ['a', 'b', 'c']</bpmn:script>"
    "</bpmn:scriptTask>"
)


def test_parallel_review_stops_on_its_completion_condition(session: Session) -> None:
    loop = _LOOP.format(
        condition="<bpmn:completionCondition>nrOfCompletedInstances &gt;= 2"
        "</bpmn:completionCondition>"
    )
    run = _Run(
        session,
        _START + f'<bpmn:userTask id="review">{loop}</bpmn:userTask>'
        # Outputs keep input order (a before c), not completion order.
        '<bpmn:scriptTask id="chk"><bpmn:script>'
        "if out != ['A', 'C']: raise Exception('out was ' + repr(out))"
        "</bpmn:script></bpmn:scriptTask>"
        '<bpmn:endEvent id="EndEvent_1"/>'
        + _flows(
            ("StartEvent_1", "prep"),
            ("prep", "review"),
            ("review", "chk"),
            ("chk", "EndEvent_1"),
        ),
    )
    review_a, _review_b, review_c = run.pending()

    run.complete(review_c, {"res": "C"})
    run.complete(review_a, {"res": "A"})

    assert run.status() == "complete"
    assert run.pending() == []  # the third review was cancelled
    assert run.metadata() == {}  # iteration submissions stay in their iterations


def test_review_loop_can_be_entered_again(session: Session) -> None:
    # The designer posts every prefilled value back, so each payload echoes its
    # own input item. Before M8F-640 that item became process metadata and the
    # second activation failed.
    run = _Run(
        session,
        _START
        + f'<bpmn:userTask id="review">{_LOOP.format(condition="")}</bpmn:userTask>'
        '<bpmn:userTask id="decide"/>'
        '<bpmn:exclusiveGateway id="again" default="f_done"/>'
        '<bpmn:endEvent id="EndEvent_1"/>'
        + _flows(("StartEvent_1", "prep"), ("prep", "review"), ("review", "decide"))
        + '<bpmn:sequenceFlow id="f_gw" sourceRef="decide" targetRef="again"/>'
        '<bpmn:sequenceFlow id="f_redo" sourceRef="again" targetRef="review">'
        "<bpmn:conditionExpression>redo == 'yes'</bpmn:conditionExpression>"
        "</bpmn:sequenceFlow>"
        '<bpmn:sequenceFlow id="f_done" sourceRef="again" targetRef="EndEvent_1"/>',
    )
    for work_item, item in zip(run.pending(), "abc", strict=True):
        run.complete(work_item, {"it": item, "res": item.upper()})
    (decide,) = run.pending()

    run.complete(decide, {"redo": "yes"})

    assert [w.task_name for w in run.pending()] == ["review [child]"] * 3
    # Tasks outside a loop still record their payload as process metadata.
    assert run.metadata() == {"redo": "yes"}


def test_user_task_inside_a_loop_subprocess_keeps_its_submission(
    session: Session,
) -> None:
    run = _Run(
        session,
        '<bpmn:startEvent id="StartEvent_1"/>'
        "<bpmn:scriptTask id=\"prep\"><bpmn:script>items = ['a', 'b']</bpmn:script>"
        "</bpmn:scriptTask>"
        f'<bpmn:subProcess id="per_item">{_LOOP.format(condition="")}'
        '<bpmn:startEvent id="sub_start"/><bpmn:userTask id="fill"/>'
        "<bpmn:scriptTask id=\"wrap\"><bpmn:script>res = it + ':' + note</bpmn:script>"
        "</bpmn:scriptTask>"
        '<bpmn:endEvent id="sub_end"/>'
        '<bpmn:sequenceFlow id="s0" sourceRef="sub_start" targetRef="fill"/>'
        '<bpmn:sequenceFlow id="s1" sourceRef="fill" targetRef="wrap"/>'
        '<bpmn:sequenceFlow id="s2" sourceRef="wrap" targetRef="sub_end"/>'
        "</bpmn:subProcess>"
        '<bpmn:scriptTask id="chk"><bpmn:script>'
        "if out != ['a:first', 'b:second']: raise Exception('out was ' + repr(out))"
        "</bpmn:script></bpmn:scriptTask>"
        '<bpmn:endEvent id="EndEvent_1"/>'
        + _flows(
            ("StartEvent_1", "prep"),
            ("prep", "per_item"),
            ("per_item", "chk"),
            ("chk", "EndEvent_1"),
        ),
    )
    fill_a, fill_b = run.pending()

    run.complete(fill_b, {"note": "second"})
    run.complete(fill_a, {"note": "first"})

    assert run.status() == "complete"
    assert run.metadata() == {}
