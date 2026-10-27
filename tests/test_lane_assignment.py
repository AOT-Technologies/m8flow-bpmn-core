from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.bpmn_process_definition import BpmnProcessDefinitionModel
from m8flow_core.models.group import GroupModel
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_core.models.work_item import WorkItemModel
from m8flow_core.models.work_item_user import WorkItemUserAddedBy, WorkItemUserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_core.services.lane_assignment import lane_group_identifier

TENANT_A = "tenant-lanes-a"
TENANT_B = "tenant-lanes-b"
NO_LANE = None
UNNAMED_LANE = ""
_TIMESTAMP = datetime.fromtimestamp(10, UTC)


def _bpmn(
    lane: str | None,
    *,
    process_id: str = "Process_lanes",
    script: str | None = None,
    extra_lanes: tuple[str, ...] = (),
) -> str:
    """Build start -> [script] -> user task -> end, with the task in ``lane``.

    ``lane=None`` puts the task outside any lane; ``lane=""`` puts it in a
    lane element without a ``name`` attribute.
    """
    script_xml = ""
    first_target = "Task_work"
    if script is not None:
        first_target = "Task_script"
        script_xml = f"""
    <bpmn:scriptTask id="Task_script" name="Script">
      <bpmn:incoming>Flow_start</bpmn:incoming>
      <bpmn:outgoing>Flow_script</bpmn:outgoing>
      <bpmn:script>{script}</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:sequenceFlow id="Flow_script"
      sourceRef="Task_script" targetRef="Task_work" />"""
    lane_xml = ""
    lane_elements: list[str] = []
    if lane is not None:
        name_attr = f' name="{lane}"' if lane else ""
        lane_elements.append(
            f'<bpmn:lane id="Lane_task"{name_attr}>'
            "<bpmn:flowNodeRef>Task_work</bpmn:flowNodeRef></bpmn:lane>"
        )
    for index, extra in enumerate(extra_lanes):
        lane_elements.append(f'<bpmn:lane id="Lane_extra_{index}" name="{extra}" />')
    if lane_elements:
        lane_xml = (
            '<bpmn:laneSet id="LaneSet_1">' + "".join(lane_elements) + "</bpmn:laneSet>"
        )
    incoming = "Flow_script" if script is not None else "Flow_start"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  id="Definitions_lanes" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_1" processRef="{process_id}" />
  </bpmn:collaboration>
  <bpmn:process id="{process_id}" isExecutable="true">
    {lane_xml}
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_start</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_start"
      sourceRef="StartEvent_1" targetRef="{first_target}" />
    {script_xml}
    <bpmn:userTask id="Task_work" name="Work">
      <bpmn:incoming>{incoming}</bpmn:incoming>
      <bpmn:outgoing>Flow_end</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:sequenceFlow id="Flow_end" sourceRef="Task_work" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1">
      <bpmn:incoming>Flow_end</bpmn:incoming>
    </bpmn:endEvent>
  </bpmn:process>
</bpmn:definitions>
"""


def _tenant(session: Session, tenant_id: str) -> M8flowTenantModel:
    tenant = session.get(M8flowTenantModel, tenant_id)
    if tenant is None:
        tenant = M8flowTenantModel(id=tenant_id, name=tenant_id, slug=tenant_id)
        session.add(tenant)
        session.flush()
    return tenant


def _user(
    session: Session,
    username: str,
    *,
    tenant_id: str = TENANT_A,
    email: str | None = None,
    admin: bool = False,
) -> UserModel:
    _tenant(session, tenant_id)
    user = UserModel(
        username=username,
        email=email,
        service=f"http://localhost:7002/realms/{tenant_id}",
        service_id=f"{username}-{tenant_id}",
        created_at=_TIMESTAMP,
        updated_at=_TIMESTAMP,
    )
    session.add(user)
    session.flush()
    if admin:
        ensure_v1_role(
            session, tenant_id=tenant_id, role_name=ROLE_ADMIN, user_ids=[user.id]
        )
    return user


def _lane_group(session: Session, lane: str, tenant_id: str = TENANT_A) -> GroupModel:
    group_id = api.resolve_lane_assignment_id(lane, tenant_id)
    group = session.get(GroupModel, group_id)
    if group is None:
        identifier = lane_group_identifier(lane, tenant_id)
        group = GroupModel(id=group_id, name=identifier, identifier=identifier)
        session.add(group)
        session.flush()
    return group


def _join(session: Session, group: GroupModel, *users: UserModel) -> None:
    for user in users:
        session.add(UserGroupAssignmentModel(user_id=user.id, group_id=group.id))
    session.flush()


def _leave(session: Session, group: GroupModel, user: UserModel) -> None:
    assignment = session.scalars(
        select(UserGroupAssignmentModel).where(
            UserGroupAssignmentModel.group_id == group.id,
            UserGroupAssignmentModel.user_id == user.id,
        )
    ).one()
    session.delete(assignment)
    session.flush()


def _group_member_ids(
    session: Session, lane: str, tenant_id: str = TENANT_A
) -> set[int]:
    return set(
        session.scalars(
            select(UserGroupAssignmentModel.user_id).where(
                UserGroupAssignmentModel.group_id
                == api.resolve_lane_assignment_id(lane, tenant_id)
            )
        )
    )


def _import(
    session: Session,
    importer: UserModel,
    bpmn_xml: str,
    *,
    properties_json: dict[str, object] | None,
    tenant_id: str = TENANT_A,
    bpmn_identifier: str = "lane-assignment",
) -> BpmnProcessDefinitionModel:
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier=bpmn_identifier,
            user_id=importer.id,
            source_bpmn_xml=bpmn_xml,
            properties_json=properties_json,
            created_at=_TIMESTAMP,
            updated_at=_TIMESTAMP,
        ),
    )
    assert isinstance(definition, BpmnProcessDefinitionModel)
    return definition


def _start(
    session: Session,
    definition: BpmnProcessDefinitionModel,
    initiator: UserModel,
    *,
    tenant_id: str = TENANT_A,
) -> WorkItemModel:
    process_instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=initiator.id,
            started_at=datetime.fromtimestamp(20, UTC),
        ),
    )
    work_items = [item for item in process_instance.work_items if not item.completed]
    assert len(work_items) == 1
    return work_items[0]


def _owners(session: Session, work_item: WorkItemModel) -> set[tuple[str, str | None]]:
    rows = session.execute(
        select(UserModel.username, WorkItemUserModel.added_by)
        .join(UserModel, UserModel.id == WorkItemUserModel.user_id)
        .where(WorkItemUserModel.work_item_id == work_item.id)
    ).all()
    return {(username, added_by) for username, added_by in rows}


def _run(
    session: Session,
    bpmn_xml: str,
    properties_json: dict[str, object] | None,
    initiator: UserModel,
) -> WorkItemModel:
    definition = _import(session, initiator, bpmn_xml, properties_json=properties_json)
    return _start(session, definition, initiator)


# -- Scenarios 1 and 2: no lane, unnamed lane ---------------------------------


@pytest.mark.parametrize("lane", [NO_LANE, UNNAMED_LANE, "   "])
def test_task_without_lane_name_goes_to_initiator(
    session: Session, lane: str | None
) -> None:
    alice = _user(session, "alice", admin=True)

    work_item = _run(session, _bpmn(lane), None, alice)

    assert _owners(session, work_item) == {("alice", "process_initiator")}
    assert work_item.lane_assignment_id is None


# -- Scenarios 3 to 5: initiator lanes ----------------------------------------


def test_initiator_lane_opt_in_ignores_lane_group(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    bob = _user(session, "bob")
    carol = _user(session, "carol")
    _join(session, _lane_group(session, "Requester"), bob, carol)

    work_item = _run(
        session,
        _bpmn("Requester"),
        {"initiator_lanes": ["Requester"]},
        alice,
    )

    assert _owners(session, work_item) == {("alice", "process_initiator")}
    assert work_item.lane_assignment_id is None


def test_initiator_lane_matching_is_normalized(session: Session) -> None:
    alice = _user(session, "alice", admin=True)

    work_item = _run(
        session,
        _bpmn("REQUESTER"),
        {"initiator_lanes": [" requester ", "Requester"]},
        alice,
    )

    assert _owners(session, work_item) == {("alice", "process_initiator")}
    assert work_item.lane_assignment_id is None


def test_initiator_lane_ignores_lane_owners_from_task_data(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    bob = _user(session, "bob")

    work_item = _run(
        session,
        _bpmn(
            "Requester",
            script='lane_owners = {"Requester": ["bob"]}',
        ),
        {"initiator_lanes": ["Requester"]},
        alice,
    )

    assert _owners(session, work_item) == {("alice", "process_initiator")}
    assert bob.id not in _group_member_ids(session, "Requester")


def test_lane_named_like_initiator_is_an_ordinary_lane(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    bob = _user(session, "bob")
    _join(session, _lane_group(session, "Process Initiator"), bob)

    work_item = _run(session, _bpmn("Process Initiator"), None, alice)

    assert _owners(session, work_item) == {("bob", "lane_assignment")}
    assert work_item.lane_assignment_id == api.resolve_lane_assignment_id(
        "Process Initiator", TENANT_A
    )


def test_lane_named_like_initiator_with_empty_group_has_no_owners(
    session: Session,
) -> None:
    alice = _user(session, "alice", admin=True)

    work_item = _run(session, _bpmn("Process Initiator"), None, alice)

    assert _owners(session, work_item) == set()
    assert work_item.task_status == "READY"


# -- Scenarios 6 to 10: lane owners -------------------------------------------


def test_lane_owners_join_lane_group_at_import(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    manager = _user(session, "manager")
    reviewer = _user(session, "reviewer")

    _import(
        session,
        alice,
        _bpmn("Manager"),
        properties_json={
            "lane_owners": {
                "Manager": ["manager", "reviewer"],
                "Not In BPMN": ["manager"],
            }
        },
    )

    assert _group_member_ids(session, "Manager") == {manager.id, reviewer.id}
    assert _group_member_ids(session, "not in bpmn") == {manager.id}


def test_owner_key_case_is_normalized(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    _user(session, "james")
    kim = _user(session, "kim")
    _join(session, _lane_group(session, "Finance"), kim)

    work_item = _run(
        session,
        _bpmn("Finance"),
        {"lane_owners": {"finance": ["james"]}},
        alice,
    )

    assert _owners(session, work_item) == {
        ("james", "lane_owner"),
        ("kim", "lane_assignment"),
    }


def test_duplicate_owner_identifiers_produce_one_row(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    _user(session, "james", email="james@example.com")

    work_item = _run(
        session,
        _bpmn("Finance"),
        {
            "lane_owners": {
                "Finance": ["james", " james ", "james@example.com"],
                "FINANCE": ["james"],
            }
        },
        alice,
    )

    assert _owners(session, work_item) == {("james", "lane_owner")}


def test_owner_identifier_falls_back_to_username_before_at(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    _user(session, "james")
    _user(session, "kim", email="kimberly@example.com")
    _user(session, "kimberly")

    work_item = _run(
        session,
        _bpmn("Finance"),
        {"lane_owners": {"Finance": [f"james@{TENANT_A}", "kimberly@example.com"]}},
        alice,
    )

    # ``kimberly@example.com`` matches kim's email, so the fallback to the
    # ``kimberly`` username is not used for it.
    assert _owners(session, work_item) == {
        ("james", "lane_owner"),
        ("kim", "lane_owner"),
    }


def test_unknown_and_foreign_owners_are_skipped_with_warning(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    alice = _user(session, "alice", admin=True)
    zed = _user(session, "zed", tenant_id=TENANT_B)

    with caplog.at_level(
        logging.WARNING, logger="m8flow_core.services.lane_assignment"
    ):
        work_item = _run(
            session,
            _bpmn("Finance"),
            {
                "lane_owners": {
                    "Finance": ["ghost", "zed", 7, ""],
                    "Broken": "not-a-list",
                    3: ["alice"],
                }
            },
            alice,
        )

    assert _owners(session, work_item) == set()
    assert work_item.task_status == "READY"
    assert zed.id not in _group_member_ids(session, "Finance")
    assert _group_member_ids(session, "Finance") == set()
    messages = [record.getMessage() for record in caplog.records]
    assert any("'ghost'" in message and "'finance'" in message for message in messages)
    assert any("'zed'" in message and "'finance'" in message for message in messages)


# -- Scenarios 11 and 12: tenant boundaries -----------------------------------


def test_legacy_unqualified_group_only_assigns_tenant_members(
    session: Session,
) -> None:
    alice = _user(session, "alice", admin=True)
    anna = _user(session, "anna")
    bert = _user(session, "bert", tenant_id=TENANT_B)
    legacy = GroupModel(name="finance", identifier=" Finance ")
    session.add(legacy)
    session.flush()
    _join(session, legacy, anna, bert)

    work_item = _run(session, _bpmn("Finance"), None, alice)

    assert _owners(session, work_item) == {("anna", "lane_assignment")}
    assert work_item.lane_assignment_id == legacy.id
    assert legacy.identifier == " Finance "


def test_same_lane_in_two_tenants_uses_separate_groups(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    bea = _user(session, "bea", tenant_id=TENANT_B, admin=True)
    _user(session, "anna")
    _user(session, "bert", tenant_id=TENANT_B)

    item_a = _run(
        session, _bpmn("Finance"), {"lane_owners": {"Finance": ["anna"]}}, alice
    )
    definition_b = _import(
        session,
        bea,
        _bpmn("Finance", process_id="Process_lanes_b"),
        properties_json={"lane_owners": {"Finance": ["bert", "anna"]}},
        tenant_id=TENANT_B,
    )
    item_b = _start(session, definition_b, bea, tenant_id=TENANT_B)

    assert item_a.lane_assignment_id != item_b.lane_assignment_id
    assert _owners(session, item_a) == {("anna", "lane_owner")}
    assert _owners(session, item_b) == {("bert", "lane_owner")}


# -- Scenario 13: run-time override -------------------------------------------


def test_script_lane_owners_replace_definition_lane_owners(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    james = _user(session, "james")
    kim = _user(session, "kim")
    finance = _lane_group(session, "Finance")

    work_item = _run(
        session,
        _bpmn("Finance", script='lane_owners = {"Finance": ["james"]}'),
        {"lane_owners": {"Finance": ["kim"]}},
        alice,
    )

    # kim joined the group at import, so she is assigned as a group member.
    assert _owners(session, work_item) == {
        ("james", "lane_owner"),
        ("kim", "lane_assignment"),
    }
    assert _group_member_ids(session, "Finance") == {james.id, kim.id}
    assert finance.id == work_item.lane_assignment_id


# -- Scenarios 14 and 15: import validation -----------------------------------


def test_initiator_lane_conflicting_with_lane_owners_is_rejected(
    session: Session,
) -> None:
    alice = _user(session, "alice", admin=True)
    _user(session, "x")

    with pytest.raises(api.ValidationError, match="Manager"):
        _import(
            session,
            alice,
            _bpmn("Manager"),
            properties_json={
                "initiator_lanes": ["Manager"],
                "lane_owners": {"manager": ["x"]},
            },
        )

    assert session.scalars(select(BpmnProcessDefinitionModel)).all() == []
    assert (
        session.get(GroupModel, api.resolve_lane_assignment_id("Manager", TENANT_A))
        is None
    )


@pytest.mark.parametrize(
    ("initiator_lanes", "message"),
    [
        ("Requester", "must be a list"),
        ([""], "non-empty strings"),
        (["  "], "non-empty strings"),
        ([42], "non-empty strings"),
        (["Requestor"], "Requestor"),
    ],
)
def test_invalid_initiator_lanes_are_rejected(
    session: Session, initiator_lanes: object, message: str
) -> None:
    alice = _user(session, "alice", admin=True)

    with pytest.raises(api.ValidationError, match=message):
        _import(
            session,
            alice,
            _bpmn("Requester", extra_lanes=("Manager",)),
            properties_json={"initiator_lanes": initiator_lanes},
        )

    assert session.scalars(select(BpmnProcessDefinitionModel)).all() == []


def test_initiator_lanes_may_name_a_lane_without_tasks(session: Session) -> None:
    alice = _user(session, "alice", admin=True)

    definition = _import(
        session,
        alice,
        _bpmn("Requester", extra_lanes=("Manager",)),
        properties_json={"initiator_lanes": ["manager"], "initiator_lanes_x": 1},
    )

    assert definition.properties_json["initiator_lanes"] == ["manager"]


def test_reimport_without_properties_validates_stored_properties(
    session: Session,
) -> None:
    alice = _user(session, "alice", admin=True)
    bpmn_xml = _bpmn("Requester")
    definition = _import(
        session, alice, bpmn_xml, properties_json={"initiator_lanes": ["Requester"]}
    )

    reimported = _import(session, alice, bpmn_xml, properties_json=None)

    assert reimported.id == definition.id
    assert reimported.properties_json["initiator_lanes"] == ["Requester"]

    with pytest.raises(api.ValidationError, match="Requester"):
        api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=TENANT_A,
                bpmn_identifier="lane-assignment",
                user_id=alice.id,
                source_bpmn_xml=_bpmn("Approver"),
                process_xml_digest=definition.process_xml_digest,
            ),
        )


# -- Scenario 16: assign_pending_tasks_for_user -------------------------------


def test_assign_pending_tasks_for_user_fills_empty_lane(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    kim = _user(session, "kim")
    bert = _user(session, "bert", tenant_id=TENANT_B)
    work_item = _run(session, _bpmn("Finance"), None, alice)
    initiator_item = _run(
        session,
        _bpmn("Requester", process_id="Process_initiator_lane"),
        {"initiator_lanes": ["Requester"]},
        alice,
    )
    assert _owners(session, work_item) == set()

    _join(session, _lane_group(session, "Finance"), kim)
    assigned = api.assign_pending_tasks_for_user(
        session, tenant_id=TENANT_A, user_id=kim.id
    )

    assert [item.id for item in assigned] == [work_item.id]
    assert _owners(session, work_item) == {("kim", "lane_assignment")}
    assert _owners(session, initiator_item) == {("alice", "process_initiator")}
    assert work_item.task_status == "READY"
    assert work_item.actual_owner_id is None
    assert (
        api.assign_pending_tasks_for_user(session, tenant_id=TENANT_A, user_id=kim.id)
        == []
    )
    with pytest.raises(api.AuthorizationError):
        api.assign_pending_tasks_for_user(session, tenant_id=TENANT_A, user_id=bert.id)
    with pytest.raises(api.NotFoundError):
        api.assign_pending_tasks_for_user(session, tenant_id=TENANT_A, user_id=99999)


# -- Scenario 17: re-materialization ------------------------------------------


@pytest.mark.parametrize("path", ["suspend-resume", "error-retry"])
def test_rematerialization_keeps_existing_owners(session: Session, path: str) -> None:
    alice = _user(session, "alice", admin=True)
    kim = _user(session, "kim")
    lee = _user(session, "lee")
    finance = _lane_group(session, "Finance")
    _join(session, finance, kim)
    work_item = _run(session, _bpmn("Finance"), None, alice)
    process_instance_id = work_item.process_instance_id
    assert _owners(session, work_item) == {("kim", "lane_assignment")}

    _leave(session, finance, kim)
    _join(session, finance, lee)
    if path == "suspend-resume":
        first: object = api.SuspendProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=process_instance_id,
            user_id=alice.id,
        )
        second: object = api.ResumeProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=process_instance_id,
            user_id=alice.id,
        )
    else:
        first = api.ErrorProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=process_instance_id,
            user_id=alice.id,
        )
        second = api.RetryProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=process_instance_id,
            user_id=alice.id,
        )
    api.execute_command(session, first)
    api.execute_command(session, second)

    open_items = session.scalars(
        select(WorkItemModel).where(
            WorkItemModel.process_instance_id == process_instance_id,
            WorkItemModel.completed.is_(False),
        )
    ).all()
    assert [item.id for item in open_items] == [work_item.id]
    assert _owners(session, work_item) == {
        ("kim", "lane_assignment"),
        ("lee", "lane_assignment"),
    }


def test_rematerialization_is_idempotent(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    kim = _user(session, "kim")
    _join(session, _lane_group(session, "Finance"), kim)
    work_item = _run(session, _bpmn("Finance"), None, alice)
    count_before = len(session.scalars(select(WorkItemUserModel)).all())

    for command in (
        api.SuspendProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=work_item.process_instance_id,
            user_id=alice.id,
        ),
        api.ResumeProcessInstanceCommand(
            tenant_id=TENANT_A,
            process_instance_id=work_item.process_instance_id,
            user_id=alice.id,
        ),
    ):
        api.execute_command(session, command)

    assert len(session.scalars(select(WorkItemUserModel)).all()) == count_before


# -- Scenarios 18 and 19: added_by labels -------------------------------------


def test_legacy_added_by_labels_stay_readable_and_usable(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    legacy_labels = {"guest": None, "manual": None, None: None, "something-else": None}
    users = {
        label: _user(session, f"legacy-{label}", admin=True) for label in legacy_labels
    }
    work_items = []
    for index, (label, user) in enumerate(users.items()):
        item = _run(
            session,
            _bpmn("Finance", process_id=f"Process_legacy_{index}"),
            None,
            alice,
        )
        session.execute(
            text(
                "INSERT INTO work_item_user (m8f_tenant_id, work_item_id, user_id, "
                "added_by) VALUES (:tenant, :work_item, :user, :label)"
            ),
            {"tenant": TENANT_A, "work_item": item.id, "user": user.id, "label": label},
        )
        work_items.append((label, user, item))
    session.expire_all()

    for label, user, item in work_items:
        row = session.scalars(
            select(WorkItemUserModel).where(
                WorkItemUserModel.work_item_id == item.id,
                WorkItemUserModel.user_id == user.id,
            )
        ).one()
        assert row.added_by == label

        pending = api.execute_query(
            session, api.GetPendingTasksQuery(tenant_id=TENANT_A, user_id=user.id)
        )
        assert [pending_item.id for pending_item in pending] == [item.id]
        api.execute_command(
            session,
            api.ClaimTaskCommand(
                tenant_id=TENANT_A, work_item_id=item.id, user_id=user.id
            ),
        )
        api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=TENANT_A, work_item_id=item.id, user_id=user.id
            ),
        )
        session.refresh(item)
        assert item.completed is True


@pytest.mark.parametrize("label", ["manual", "guest", "", "LANE_OWNER", 3])
def test_writing_unknown_added_by_raises(session: Session, label: object) -> None:
    with pytest.raises(api.ValidationError) as excinfo:
        WorkItemUserModel(
            m8f_tenant_id=TENANT_A, work_item_id=1, user_id=1, added_by=label
        )
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize(
    "label",
    [
        None,
        "lane_owner",
        "lane_assignment",
        "process_initiator",
        WorkItemUserAddedBy.lane_owner,
    ],
)
def test_writing_known_added_by_succeeds(label: object) -> None:
    row = WorkItemUserModel(m8f_tenant_id=TENANT_A, work_item_id=1, user_id=1)
    row.added_by = label  # type: ignore[assignment]
    assert row.added_by == (None if label is None else str(label))


# -- Scenarios 20 and 22: public surface --------------------------------------


def test_claim_task_command_has_no_added_by() -> None:
    with pytest.raises(TypeError):
        api.ClaimTaskCommand(  # type: ignore[call-arg]
            tenant_id=TENANT_A, work_item_id=1, user_id=1, added_by="manual"
        )
    command = api.ClaimTaskCommand(tenant_id=TENANT_A, work_item_id=1, user_id=1)
    with pytest.raises(FrozenInstanceError):
        command.user_id = 2  # type: ignore[misc]


def test_claim_task_function_rejects_added_by(session: Session) -> None:
    with pytest.raises(TypeError):
        api.claim_task(  # type: ignore[call-arg]
            session, tenant_id=TENANT_A, work_item_id=1, user_id=1, added_by="manual"
        )


def test_work_item_user_added_by_has_three_members() -> None:
    assert sorted((member.name, member.value) for member in WorkItemUserAddedBy) == [
        ("lane_assignment", "lane_assignment"),
        ("lane_owner", "lane_owner"),
        ("process_initiator", "process_initiator"),
    ]


# -- Precedence ---------------------------------------------------------------


def test_initiator_in_normal_lane_is_labelled_like_anyone_else(
    session: Session,
) -> None:
    alice = _user(session, "alice", admin=True)
    james = _user(session, "james")
    _join(session, _lane_group(session, "Finance"), alice, james)

    work_item = _run(
        session,
        _bpmn("Finance"),
        {"lane_owners": {"Finance": ["james"]}},
        alice,
    )

    assert _owners(session, work_item) == {
        ("alice", "lane_assignment"),
        ("james", "lane_owner"),
    }


def test_missing_initiator_raises_not_found(session: Session) -> None:
    alice = _user(session, "alice", admin=True)
    bob = _user(session, "bob", admin=True)
    definition = _import(session, alice, _bpmn(NO_LANE), properties_json=None)
    first_item = _start(session, definition, bob)
    process_instance = first_item.process_instance
    session.execute(
        text("DELETE FROM work_item_user WHERE work_item_id = :id"),
        {"id": first_item.id},
    )
    session.flush()

    from m8flow_core.services.lane_assignment import assign_work_item_potential_owners

    # The user FK forbids persisting a dangling initiator, so point at a
    # missing user in memory only and restore it before the fixture commits.
    original_initiator_id = process_instance.process_initiator_id
    with session.no_autoflush:
        process_instance.process_initiator_id = 99999
        try:
            with pytest.raises(api.NotFoundError):
                assign_work_item_potential_owners(
                    session,
                    process_instance=process_instance,
                    work_item=first_item,
                    lane_name=None,
                    task_data=None,
                    definition_properties=None,
                )
        finally:
            process_instance.process_initiator_id = original_initiator_id
