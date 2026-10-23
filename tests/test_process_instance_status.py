from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel

ACTIVE_STATUSES = {"not_started", "running", "user_input_required", "waiting"}


def test_has_terminal_status_is_false_before_status_is_set() -> None:
    process_instance = ProcessInstanceModel()

    assert process_instance.status is None
    assert process_instance.has_terminal_status() is False


def test_active_statuses_is_same_through_an_instance() -> None:
    process_instance = ProcessInstanceModel(status="running")

    assert set(process_instance.active_statuses()) == set(
        ProcessInstanceModel.active_statuses()
    )


def test_active_statuses_result_is_independent_per_call() -> None:
    first = ProcessInstanceModel.active_statuses()
    first.clear()
    first.append("terminated")

    assert set(ProcessInstanceModel.active_statuses()) == ACTIVE_STATUSES


def test_active_statuses_filters_process_instances(session: Session) -> None:
    tenant = M8flowTenantModel(
        id="tenant-status", name="Tenant Status", slug="tenant-status"
    )
    user = UserModel(
        username="status-user",
        email="status-user@example.com",
        service="http://localhost:7002/realms/tenant-status",
        service_id="status-user-keycloak",
        display_name="Status User",
        created_at=datetime.fromtimestamp(1, UTC),
        updated_at=datetime.fromtimestamp(1, UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    session.add_all(
        [
            ProcessInstanceModel(
                m8f_tenant_id=tenant.id,
                process_model_identifier=f"status-{status.value}",
                process_model_display_name=f"status-{status.value}",
                process_initiator_id=user.id,
                status=status,
            )
            for status in ProcessInstanceStatus
        ]
    )
    session.flush()

    rows = session.scalars(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.status.in_(ProcessInstanceModel.active_statuses())
        )
    ).all()

    assert {row.status for row in rows} == ACTIVE_STATUSES
    assert len(rows) == len(ACTIVE_STATUSES)
