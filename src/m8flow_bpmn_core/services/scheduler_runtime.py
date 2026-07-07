from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import (
    BpmnCoreError,
    NotFoundError,
    ValidationError,
)
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceStatus
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel, SchedulerJobType
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.authorization import ROLE_ADMIN, ensure_v1_role
from m8flow_bpmn_core.services.process_instances import (
    get_process_instance,
    retry_process_instance,
)
from m8flow_bpmn_core.services.scheduler_jobs import (
    delete_scheduler_job,
    list_due_scheduler_jobs,
    upsert_scheduler_job,
)
from m8flow_bpmn_core.services.workflow_runtime import (
    _initialize_process_instance_from_timer_start_definition,
    _refresh_waiting_process_instance_workflow,
    _timer_event_run_at_in_seconds,
)

_TIMER_START_SYSTEM_USERNAME = "__m8f_timer_start__"
_TIMER_START_SYSTEM_SERVICE_ID_PREFIX = "__m8f_timer_start__"


def claim_due_scheduler_jobs(
    session: Session,
    *,
    now_in_seconds: int | None = None,
    limit: int = 100,
    worker_id: str = "inline",
    tenant_id: str | None = None,
) -> list[SchedulerJobModel]:
    normalized_worker_id = _normalize_worker_id(worker_id)
    occurred_at = _resolve_timestamp(now_in_seconds)
    claimed_jobs: list[SchedulerJobModel] = []

    for job in list_due_scheduler_jobs(
        session,
        now_in_seconds=occurred_at,
        limit=limit,
        tenant_id=tenant_id,
    ):
        job.locked_by = normalized_worker_id
        job.locked_at_in_seconds = occurred_at
        job.updated_at_in_seconds = occurred_at
        claimed_jobs.append(job)

    session.flush()
    return claimed_jobs


def run_due_scheduler_jobs(
    session: Session,
    *,
    now_in_seconds: int | None = None,
    limit: int = 100,
    worker_id: str = "inline",
    tenant_id: str | None = None,
) -> int:
    occurred_at = _resolve_timestamp(now_in_seconds)
    claimed_jobs = claim_due_scheduler_jobs(
        session,
        now_in_seconds=occurred_at,
        limit=limit,
        worker_id=worker_id,
        tenant_id=tenant_id,
    )
    processed_count = 0
    batch_errors: list[tuple[str, BpmnCoreError]] = []

    for job in claimed_jobs:
        try:
            _execute_claimed_scheduler_job(
                session,
                job=job,
                occurred_at=occurred_at,
            )
        except BpmnCoreError as exc:
            _release_scheduler_job_lock(
                session,
                job=job,
                updated_at_in_seconds=occurred_at,
            )
            batch_errors.append((job.job_key, exc))
        except Exception as exc:
            _release_scheduler_job_lock(
                session,
                job=job,
                updated_at_in_seconds=occurred_at,
            )
            wrapped_error = BpmnCoreError(
                f"Scheduled job {job.job_key!r} failed during execution"
            )
            wrapped_error.__cause__ = exc
            batch_errors.append((job.job_key, wrapped_error))
        else:
            processed_count += 1

    if len(batch_errors) == 1:
        raise batch_errors[0][1]
    if batch_errors:
        raise _build_scheduler_batch_error(batch_errors)

    return processed_count


def _execute_claimed_scheduler_job(
    session: Session,
    *,
    job: SchedulerJobModel,
    occurred_at: int,
) -> None:
    if job.job_type == SchedulerJobType.intermediate_timer.value:
        _execute_intermediate_timer_job(
            session,
            job=job,
            occurred_at=occurred_at,
        )
        return
    if job.job_type == SchedulerJobType.process_retry.value:
        _execute_process_retry_job(
            session,
            job=job,
            occurred_at=occurred_at,
        )
        return
    if job.job_type == SchedulerJobType.timer_start.value:
        _execute_timer_start_job(
            session,
            job=job,
            occurred_at=occurred_at,
        )
        return

    raise ValidationError(
        f"Scheduler job type {job.job_type!r} is not executable yet"
    )


def _execute_intermediate_timer_job(
    session: Session,
    *,
    job: SchedulerJobModel,
    occurred_at: int,
) -> None:
    if job.process_instance_id is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    process_instance = get_process_instance(
        session,
        tenant_id=job.m8f_tenant_id,
        process_instance_id=job.process_instance_id,
    )
    if process_instance.status not in (
        ProcessInstanceStatus.waiting.value,
        ProcessInstanceStatus.user_input_required.value,
    ):
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return
    if process_instance.workflow_state_json is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    _refresh_waiting_process_instance_workflow(
        session,
        tenant_id=job.m8f_tenant_id,
        process_instance_id=job.process_instance_id,
        occurred_at=occurred_at,
    )


def _execute_process_retry_job(
    session: Session,
    *,
    job: SchedulerJobModel,
    occurred_at: int,
) -> None:
    if job.process_instance_id is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    process_instance = get_process_instance(
        session,
        tenant_id=job.m8f_tenant_id,
        process_instance_id=job.process_instance_id,
    )
    if process_instance.status != "error":
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    retry_process_instance(
        session,
        tenant_id=job.m8f_tenant_id,
        process_instance_id=job.process_instance_id,
        user_id=_scheduler_job_user_id(
            job.payload_json,
            key="requested_by_user_id",
        ),
        retried_at_in_seconds=occurred_at,
    )


def _execute_timer_start_job(
    session: Session,
    *,
    job: SchedulerJobModel,
    occurred_at: int,
) -> None:
    if job.bpmn_process_definition_id is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    process_definition = session.scalar(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == job.m8f_tenant_id,
            BpmnProcessDefinitionModel.id == job.bpmn_process_definition_id,
        )
    )
    if process_definition is None or process_definition.source_bpmn_xml is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    timer_task_payload = _scheduler_job_timer_task(job.payload_json)
    system_user = _ensure_timer_start_system_user(
        session,
        tenant_id=job.m8f_tenant_id,
        occurred_at=occurred_at,
    )
    _initialize_process_instance_from_timer_start_definition(
        session,
        tenant_id=job.m8f_tenant_id,
        process_definition_id=job.bpmn_process_definition_id,
        process_initiator_id=system_user.id,
        timer_start_task_spec_name=_scheduler_job_task_spec_name(timer_task_payload),
        started_at_in_seconds=occurred_at,
    )
    _reschedule_or_delete_timer_start_job(
        session,
        job=job,
        timer_task_payload=timer_task_payload,
        occurred_at=occurred_at,
    )


def _release_scheduler_job_lock(
    session: Session,
    *,
    job: SchedulerJobModel,
    updated_at_in_seconds: int,
) -> None:
    existing_job = session.get(SchedulerJobModel, job.id)
    if existing_job is None:
        return

    existing_job.locked_by = None
    existing_job.locked_at_in_seconds = None
    existing_job.updated_at_in_seconds = updated_at_in_seconds
    session.flush()


def _build_scheduler_batch_error(
    batch_errors: list[tuple[str, BpmnCoreError]],
) -> BpmnCoreError:
    error_details = ", ".join(
        f"{job_key}: {type(error).__name__}: {error}"
        for job_key, error in batch_errors
    )
    summary_error = BpmnCoreError(
        f"{len(batch_errors)} scheduler jobs failed in one batch: {error_details}"
    )
    summary_error.__cause__ = batch_errors[0][1]
    return summary_error


def _normalize_worker_id(worker_id: str) -> str:
    normalized_worker_id = worker_id.strip()
    if not normalized_worker_id:
        raise ValidationError("Scheduler worker id cannot be blank")
    return normalized_worker_id


def _scheduler_job_user_id(
    payload_json: object,
    *,
    key: str,
) -> int:
    if not isinstance(payload_json, Mapping):
        raise ValidationError("Scheduler job payload must be a dictionary")

    actor_user_id = payload_json.get(key)
    if type(actor_user_id) is not int:
        raise ValidationError(
            f"Scheduler job payload is missing integer field {key!r}"
        )
    return actor_user_id


def _scheduler_job_timer_task(payload_json: object) -> dict[str, object]:
    if not isinstance(payload_json, Mapping):
        raise ValidationError("Scheduler job payload must be a dictionary")

    timer_task = payload_json.get("timer_task")
    if not isinstance(timer_task, Mapping):
        raise ValidationError("Scheduler job payload is missing a timer_task payload")
    return dict(timer_task)


def _scheduler_job_task_spec_name(timer_task_payload: Mapping[str, object]) -> str:
    task_spec_name = timer_task_payload.get("task_spec_name")
    if not isinstance(task_spec_name, str) or not task_spec_name.strip():
        raise ValidationError("Timer start payload is missing its task_spec_name")
    return task_spec_name


def _ensure_timer_start_system_user(
    session: Session,
    *,
    tenant_id: str,
    occurred_at: int,
) -> UserModel:
    tenant = session.scalar(
        select(M8flowTenantModel).where(
            M8flowTenantModel.id == tenant_id
        )
    )
    if tenant is None:
        tenant = session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == tenant_id
            )
        )
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} was not found")

    service_id = f"{_TIMER_START_SYSTEM_SERVICE_ID_PREFIX}:{tenant.id}"
    system_user = session.scalar(
        select(UserModel).where(
            UserModel.service == tenant.id,
            UserModel.service_id == service_id,
        )
    )
    if system_user is None:
        system_user = UserModel(
            username=_TIMER_START_SYSTEM_USERNAME,
            email=None,
            service=tenant.id,
            service_id=service_id,
            display_name="Timer Start System",
            created_at_in_seconds=occurred_at,
            updated_at_in_seconds=occurred_at,
        )
        session.add(system_user)
        session.flush()
    else:
        system_user.updated_at_in_seconds = occurred_at
        session.flush()

    ensure_v1_role(
        session,
        tenant_id=tenant.id,
        role_name=ROLE_ADMIN,
        user_ids=[system_user.id],
    )
    return system_user


def _reschedule_or_delete_timer_start_job(
    session: Session,
    *,
    job: SchedulerJobModel,
    timer_task_payload: Mapping[str, object],
    occurred_at: int,
) -> None:
    next_timer_task_payload = _next_timer_start_task_payload_after_fire(
        timer_task_payload
    )
    if next_timer_task_payload is None:
        delete_scheduler_job(
            session,
            tenant_id=job.m8f_tenant_id,
            job_key=job.job_key,
        )
        return

    upsert_scheduler_job(
        session,
        tenant_id=job.m8f_tenant_id,
        job_key=job.job_key,
        job_type=SchedulerJobType.timer_start,
        bpmn_process_definition_id=job.bpmn_process_definition_id,
        run_at_in_seconds=next_timer_task_payload["run_at_in_seconds"],
        payload_json={
            "scheduled_from": "timer_start_runtime",
            "timer_task": next_timer_task_payload,
        },
        updated_at_in_seconds=occurred_at,
    )


def _next_timer_start_task_payload_after_fire(
    timer_task_payload: Mapping[str, object],
) -> dict[str, object] | None:
    current_event_value = timer_task_payload.get("event_value")
    if isinstance(current_event_value, str):
        return None
    if not isinstance(current_event_value, Mapping):
        raise ValidationError("Timer start payload is missing its event_value")

    next_due_value = current_event_value.get("next")
    if not isinstance(next_due_value, str) or not next_due_value:
        raise ValidationError("Recurring timer start payload is missing its next value")

    duration_value = current_event_value.get("duration")
    if not isinstance(duration_value, (int, float)) or duration_value <= 0:
        raise ValidationError(
            "Recurring timer start payload is missing its positive duration"
        )

    cycles_value = current_event_value.get("cycles")
    if cycles_value is None:
        remaining_cycles: int | None = None
    elif type(cycles_value) is int and cycles_value > 1:
        remaining_cycles = cycles_value - 1
    elif type(cycles_value) is int and cycles_value <= 1:
        return None
    else:
        raise ValidationError("Recurring timer start payload has invalid cycles")

    following_due_at = _parse_scheduler_job_due_at(next_due_value) + timedelta(
        seconds=float(duration_value)
    )
    next_event_value: dict[str, object] = {
        "next": following_due_at.isoformat(),
        "duration": float(duration_value),
    }
    if remaining_cycles is not None:
        next_event_value["cycles"] = remaining_cycles

    next_payload = dict(timer_task_payload)
    next_payload["event_value"] = next_event_value
    next_payload["run_at_in_seconds"] = _timer_event_run_at_in_seconds(
        next_event_value
    )
    return next_payload


def _parse_scheduler_job_due_at(value: str) -> datetime:
    due_at = datetime.fromisoformat(value)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    return due_at


def _resolve_timestamp(timestamp_in_seconds: int | None) -> int:
    return (
        timestamp_in_seconds
        if timestamp_in_seconds is not None
        else round(time.time())
    )
