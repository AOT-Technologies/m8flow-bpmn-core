#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Persist compensation progress and expose the tenant-scoped journal query."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core.models.compensation import (
    CompensationRecordModel,
    CompensationRunModel,
    CompensationScopeModel,
)
from m8flow_core.models.json_data import JsonDataModel
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.services.compensation_runtime import CompensationCoordinator
from m8flow_core.services.process_instances import (
    get_process_instance,
    record_process_instance_event,
)


def persist_compensation(
    session: Session,
    instance: ProcessInstanceModel,
    comp: CompensationCoordinator,
    occurred_at: datetime,
) -> None:
    with session.no_autoflush:
        tenant_id = instance.m8f_tenant_id
        for model, collection, identity in (
            (CompensationScopeModel, "scopes", "scope_id"),
            (CompensationRunModel, "runs", "throw_task_guid"),
            (CompensationRecordModel, "records", "source_task_guid"),
        ):
            existing = {
                getattr(row, identity): row
                for row in session.scalars(
                    select(model).where(
                        model.m8f_tenant_id == tenant_id,
                        model.process_instance_id == instance.id,
                    )
                )
            }
            for value in comp.state[collection].values():
                row: Any = existing.get(value[identity])
                if row is None:
                    row = model(
                        m8f_tenant_id=tenant_id,
                        process_instance_id=instance.id,
                        created_at=occurred_at,
                    )
                    session.add(row)
                for column in model.__table__.columns:
                    if column.name in value and column.name != "created_at":
                        setattr(row, column.name, value[column.name])
                row.updated_at = occurred_at
                if "data" in value:
                    row.data_hash = JsonDataModel.create_or_update_from_payload(
                        session, tenant_id, value["data"]
                    )
                if (
                    collection == "records"
                    and value["source_task_guid"] in comp.handlers
                ):
                    handler = comp.handlers[value["source_task_guid"]]
                    row.workflow_state_hash = (
                        JsonDataModel.create_or_update_from_payload(
                            session, tenant_id, comp.handler_snapshot(handler)
                        )
                    )
        for event_type, task_guid in comp.events:
            record_process_instance_event(
                session,
                tenant_id=tenant_id,
                process_instance_id=instance.id,
                event_type=event_type,
                task_guid=task_guid,
                occurred_at=occurred_at,
            )
        comp.events.clear()


def get_process_instance_compensations(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> list[CompensationRecordModel]:
    get_process_instance(
        session, tenant_id=tenant_id, process_instance_id=process_instance_id
    )
    return list(
        session.scalars(
            select(CompensationRecordModel)
            .where(
                CompensationRecordModel.m8f_tenant_id == tenant_id,
                CompensationRecordModel.process_instance_id == process_instance_id,
            )
            .order_by(CompensationRecordModel.completion_sequence)
        )
    )
