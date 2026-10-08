from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import ValidationError
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel, SchedulerJobType


def build_scheduler_job_key(
    *,
    job_type: SchedulerJobType | str,
    process_instance_id: int | None = None,
    bpmn_process_definition_id: int | None = None,
    qualifier: str | None = None,
) -> str:
    normalized_job_type = SchedulerJobType(job_type).value
    key_parts = [normalized_job_type]

    if process_instance_id is not None:
        key_parts.append(f"pi:{process_instance_id}")
    if bpmn_process_definition_id is not None:
        key_parts.append(f"pd:{bpmn_process_definition_id}")
    if qualifier is not None:
        normalized_qualifier = qualifier.strip()
        if not normalized_qualifier:
            raise ValidationError("Scheduler job qualifier cannot be blank")
        key_parts.append(f"q:{normalized_qualifier}")

    if len(key_parts) == 1:
        raise ValidationError(
            "Scheduler job key requires a process instance, process definition, "
            "or non-blank qualifier"
        )

    return "|".join(key_parts)


def upsert_scheduler_job(
    session: Session,
    *,
    tenant_id: str,
    job_key: str,
    job_type: SchedulerJobType | str,
    run_at: datetime,
    process_instance_id: int | None = None,
    bpmn_process_definition_id: int | None = None,
    payload_json: Mapping[str, Any] | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> SchedulerJobModel:
    normalized_job_type = SchedulerJobType(job_type).value
    occurred_at = _resolve_timestamp(updated_at)
    normalized_payload = dict(payload_json or {})

    job = session.scalar(
        select(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.job_key == job_key,
        )
    )
    if job is None:
        job = SchedulerJobModel(
            m8f_tenant_id=tenant_id,
            job_key=job_key,
            job_type=normalized_job_type,
            process_instance_id=process_instance_id,
            bpmn_process_definition_id=bpmn_process_definition_id,
            locked_by=None,
            locked_at=None,
            run_at=run_at,
            payload_json=normalized_payload,
            updated_at=occurred_at,
            created_at=(
                created_at
                if created_at is not None
                else occurred_at
            ),
        )
        session.add(job)
    else:
        job.job_type = normalized_job_type
        job.process_instance_id = process_instance_id
        job.bpmn_process_definition_id = bpmn_process_definition_id
        job.locked_by = None
        job.locked_at = None
        job.run_at = run_at
        job.payload_json = normalized_payload
        job.updated_at = occurred_at
        if created_at is not None:
            job.created_at = created_at

    session.flush()
    return job


def list_due_scheduler_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
    tenant_id: str | None = None,
) -> list[SchedulerJobModel]:
    if limit <= 0:
        raise ValidationError("Scheduler job limit must be greater than zero")

    occurred_at = _resolve_timestamp(now)
    stmt = select(SchedulerJobModel).where(
        SchedulerJobModel.locked_by.is_(None),
        SchedulerJobModel.run_at <= occurred_at,
    )
    if tenant_id is not None:
        stmt = stmt.where(SchedulerJobModel.m8f_tenant_id == tenant_id)
    stmt = stmt.order_by(
        SchedulerJobModel.run_at,
        SchedulerJobModel.id,
    ).limit(limit)
    return list(session.scalars(stmt).all())


def delete_scheduler_job(
    session: Session,
    *,
    tenant_id: str,
    job_key: str,
) -> bool:
    result = session.execute(
        delete(SchedulerJobModel).where(
            SchedulerJobModel.m8f_tenant_id == tenant_id,
            SchedulerJobModel.job_key == job_key,
        )
    )
    session.flush()
    return result.rowcount > 0


def _resolve_timestamp(timestamp: datetime | None) -> datetime:
    return timestamp or datetime.now(UTC)
