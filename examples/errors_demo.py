"""Demonstrate every public error class raised by m8flow_bpmn_core.

Run with:

    uv run python examples/errors_demo.py

The example uses an in-memory SQLite database, seeds the minimum amount of
data needed to trigger each error, then exercises one call per error type.
For each case it prints:

* the call that was attempted,
* the domain error class that was raised,
* the builtin exception that the domain class also subclasses, and
* the message produced by the service layer.

This is the runnable counterpart of the "Errors" section in doc/api.md.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from m8flow_bpmn_core import api  # noqa: E402
from m8flow_bpmn_core.db import build_engine, create_schema  # noqa: E402
from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel  # noqa: E402
from m8flow_bpmn_core.models.bpmn_process_definition import (  # noqa: E402
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel  # noqa: E402
from m8flow_bpmn_core.models.human_task_user import (  # noqa: E402
    HumanTaskUserModel,
)
from m8flow_bpmn_core.models.process_instance import (  # noqa: E402
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_bpmn_core.models.tenant import M8flowTenantModel  # noqa: E402
from m8flow_bpmn_core.models.user import UserModel  # noqa: E402
from m8flow_bpmn_core.services.authorization import (  # noqa: E402
    ROLE_USER,
    ensure_v1_role,
)

TENANT_ID = "tenant-errors-demo"
TENANT_SLUG = "tenant-errors-demo"
FOREIGN_TENANT_ID = "tenant-errors-demo-foreign"
FOREIGN_TENANT_SLUG = "tenant-errors-demo-foreign"


def main() -> None:
    print("m8flow-bpmn-core public-error demo")
    print()
    print("This walks every error class re-exported from m8flow_bpmn_core.api,")
    print("triggers it through a public service call, and shows that each one")
    print("can be caught by either the domain class or the matching builtin.")
    print()

    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)

    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        context = _seed(session)
        session.commit()

        _section(
            "NotFoundError",
            "Reading a process instance that does not exist.",
            lambda: api.execute_query(
                session,
                api.GetProcessInstanceQuery(
                    tenant_id=TENANT_ID,
                    process_instance_id=999_999,
                ),
            ),
            expected_domain=api.NotFoundError,
            expected_builtin=LookupError,
        )

        _section(
            "AuthorizationError (user not in tenant)",
            "Listing pending tasks for a user that belongs to another tenant.",
            lambda: api.execute_query(
                session,
                api.GetPendingTasksQuery(
                    tenant_id=TENANT_ID,
                    user_id=context["foreign_user_id"],
                ),
            ),
            expected_domain=api.AuthorizationError,
            expected_builtin=PermissionError,
        )

        _section(
            "AuthorizationError (user not assigned to task)",
            "Completing a task as a user who is not on the task's assignee list.",
            lambda: api.execute_command(
                session,
                api.CompleteTaskCommand(
                    tenant_id=TENANT_ID,
                    human_task_id=context["unassigned_task_id"],
                    user_id=context["other_user_id"],
                ),
            ),
            expected_domain=api.AuthorizationError,
            expected_builtin=PermissionError,
        )

        _section(
            "InvalidStateError (terminal transition)",
            "Suspending a process instance that is already terminated.",
            lambda: api.execute_command(
                session,
                api.SuspendProcessInstanceCommand(
                    tenant_id=TENANT_ID,
                    process_instance_id=context["terminated_process_instance_id"],
                ),
            ),
            expected_domain=api.InvalidStateError,
            expected_builtin=ValueError,
        )

        _section(
            "InvalidStateError (claim completed task)",
            "Claiming a task that has already been completed.",
            lambda: api.execute_command(
                session,
                api.ClaimTaskCommand(
                    tenant_id=TENANT_ID,
                    human_task_id=context["completed_task_id"],
                    user_id=context["primary_user_id"],
                ),
            ),
            expected_domain=api.InvalidStateError,
            expected_builtin=ValueError,
        )

        _section(
            "ValidationError (bad event type)",
            "Recording a process event with a value that is not in the enum.",
            lambda: api.execute_command(
                session,
                api.RecordProcessInstanceEventCommand(
                    tenant_id=TENANT_ID,
                    process_instance_id=context["running_process_instance_id"],
                    event_type="not-a-real-event",
                ),
            ),
            expected_domain=api.ValidationError,
            expected_builtin=ValueError,
        )

        _section(
            "BpmnCoreError (base class catches everything)",
            "The same call as above, but caught at the base class.",
            lambda: api.execute_command(
                session,
                api.RecordProcessInstanceEventCommand(
                    tenant_id=TENANT_ID,
                    process_instance_id=context["running_process_instance_id"],
                    event_type="not-a-real-event",
                ),
            ),
            expected_domain=api.BpmnCoreError,
            expected_builtin=Exception,
        )

    print()
    print("All errors raised and caught as expected.")


def _section(
    title: str,
    description: str,
    call: Callable[[], object],
    *,
    expected_domain: type[Exception],
    expected_builtin: type[Exception],
) -> None:
    print("-" * 80)
    print(f"Case: {title}")
    print(description)
    try:
        call()
    except expected_domain as exc:
        if not isinstance(exc, expected_builtin):
            raise RuntimeError(
                f"Expected {expected_domain.__name__} to also be a "
                f"{expected_builtin.__name__}, but isinstance returned False"
            ) from exc
        print(f"  raised:   {type(exc).__name__}: {exc}")
        print(
            f"  domain:   {expected_domain.__name__} "
            f"(also subclass of {expected_builtin.__name__})"
        )
    except Exception as exc:  # pragma: no cover - the demo would be broken
        raise RuntimeError(
            f"Expected {expected_domain.__name__}, "
            f"got {type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise RuntimeError(
            f"Expected {expected_domain.__name__} to be raised, "
            f"but the call returned successfully"
        )


def _seed(session: Session) -> dict[str, int]:
    """Insert the minimum rows needed to trigger each error class."""
    tenant = M8flowTenantModel(id=TENANT_ID, name="Demo", slug=TENANT_SLUG)
    foreign_tenant = M8flowTenantModel(
        id=FOREIGN_TENANT_ID, name="Foreign", slug=FOREIGN_TENANT_SLUG
    )
    primary_user = UserModel(
        username="primary",
        email="primary@example.com",
        service=f"http://localhost/realms/{TENANT_SLUG}",
        service_id="primary-keycloak",
        display_name="Primary User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    other_user = UserModel(
        username="other",
        email="other@example.com",
        service=f"http://localhost/realms/{TENANT_SLUG}",
        service_id="other-keycloak",
        display_name="Other User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    foreign_user = UserModel(
        username="foreigner",
        email="foreigner@example.com",
        service=f"http://localhost/realms/{FOREIGN_TENANT_SLUG}",
        service_id="foreigner-keycloak",
        display_name="Foreign User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, foreign_tenant, primary_user, other_user, foreign_user])
    session.flush()
    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_USER,
        user_ids=[primary_user.id, other_user.id],
    )

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="demo-single",
        full_process_model_hash="demo-full",
        bpmn_identifier="demo-process",
        bpmn_name="Demo Process",
        source_bpmn_xml="<bpmn />",
        source_dmn_xml=None,
        properties_json={},
        created_at_in_seconds=10,
        updated_at_in_seconds=10,
    )
    session.add(definition)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid="demo-bpmn-process",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"root": "demo-root"},
        json_data_hash="demo-bpmn-hash",
    )
    session.add(bpmn_process)
    session.flush()

    def _make_process_instance(*, status: str) -> ProcessInstanceModel:
        instance = ProcessInstanceModel(
            m8f_tenant_id=tenant.id,
            process_model_identifier="demo-process",
            process_model_display_name="Demo Process",
            process_initiator_id=primary_user.id,
            bpmn_process_definition_id=definition.id,
            bpmn_process_id=bpmn_process.id,
            status=status,
            created_at_in_seconds=20,
            updated_at_in_seconds=20,
        )
        session.add(instance)
        session.flush()
        return instance

    running_instance = _make_process_instance(
        status=ProcessInstanceStatus.running.value,
    )
    terminated_instance = _make_process_instance(
        status=ProcessInstanceStatus.terminated.value,
    )

    completed_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=running_instance.id,
        task_guid="task-completed",
        lane_assignment_id=None,
        completed_by_user_id=primary_user.id,
        actual_owner_id=primary_user.id,
        task_name="completed-task",
        task_title="Completed Task",
        task_type="UserTask",
        task_status="COMPLETED",
        process_model_display_name="Demo Process",
        bpmn_process_identifier="demo-process",
        lane_name=None,
        json_metadata={},
        completed=True,
    )
    unassigned_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=running_instance.id,
        task_guid="task-unassigned",
        lane_assignment_id=None,
        completed_by_user_id=None,
        actual_owner_id=None,
        task_name="unassigned-task",
        task_title="Unassigned Task",
        task_type="UserTask",
        task_status="READY",
        process_model_display_name="Demo Process",
        bpmn_process_identifier="demo-process",
        lane_name=None,
        json_metadata={},
        completed=False,
    )
    session.add_all([completed_task, unassigned_task])
    session.flush()

    session.add(
        HumanTaskUserModel(
            m8f_tenant_id=tenant.id,
            human_task_id=unassigned_task.id,
            user_id=primary_user.id,
            added_by="manual",
        )
    )
    session.flush()

    return {
        "primary_user_id": primary_user.id,
        "other_user_id": other_user.id,
        "foreign_user_id": foreign_user.id,
        "running_process_instance_id": running_instance.id,
        "terminated_process_instance_id": terminated_instance.id,
        "completed_task_id": completed_task.id,
        "unassigned_task_id": unassigned_task.id,
    }


if __name__ == "__main__":
    main()
