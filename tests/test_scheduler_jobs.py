from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import ValidationError
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.scheduler_job import SchedulerJobModel, SchedulerJobType
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.services.scheduler_jobs import (
    build_scheduler_job_key,
    delete_scheduler_job,
    list_due_scheduler_jobs,
    upsert_scheduler_job,
)


def test_build_scheduler_job_key_requires_scope() -> None:
    assert (
        build_scheduler_job_key(
            job_type=SchedulerJobType.intermediate_timer,
            process_instance_id=12,
            qualifier="Task_1",
        )
        == "intermediate_timer|pi:12|q:Task_1"
    )
    assert (
        build_scheduler_job_key(
            job_type=SchedulerJobType.timer_start,
            bpmn_process_definition_id=34,
            qualifier="StartEvent_1",
        )
        == "timer_start|pd:34|q:StartEvent_1"
    )

    try:
        build_scheduler_job_key(job_type=SchedulerJobType.process_retry)
    except ValidationError as exc:
        assert "requires a process instance" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("Expected ValidationError when no scheduler scope is set")


def test_upsert_scheduler_job_reuses_existing_row(session: Session) -> None:
    tenant, definition, process_instance = _seed_scheduler_context(session)
    job_key = build_scheduler_job_key(
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        qualifier="TimerCatch_1",
    )

    created_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=job_key,
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        run_at_in_seconds=100,
        payload_json={"event_value": "2026-06-24T16:00:00+00:00"},
        updated_at_in_seconds=90,
        created_at_in_seconds=80,
    )
    created_job.locked_by = "worker-a"
    created_job.locked_at_in_seconds = 91
    session.flush()

    updated_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=job_key,
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        bpmn_process_definition_id=definition.id,
        run_at_in_seconds=140,
        payload_json={"event_value": "2026-06-24T16:10:00+00:00"},
        updated_at_in_seconds=120,
    )

    assert updated_job.id == created_job.id
    assert updated_job.job_type == "intermediate_timer"
    assert updated_job.process_instance_id == process_instance.id
    assert updated_job.bpmn_process_definition_id == definition.id
    assert updated_job.run_at_in_seconds == 140
    assert updated_job.payload_json == {"event_value": "2026-06-24T16:10:00+00:00"}
    assert updated_job.locked_by is None
    assert updated_job.locked_at_in_seconds is None
    assert updated_job.created_at_in_seconds == 80
    assert updated_job.updated_at_in_seconds == 120
    assert len(session.scalars(select(SchedulerJobModel)).all()) == 1


def test_list_due_scheduler_jobs_orders_and_filters_locked_rows(
    session: Session,
) -> None:
    tenant, definition, process_instance = _seed_scheduler_context(session)
    other_tenant = M8flowTenantModel(
        id="tenant-other",
        name="Tenant Other",
        slug="tenant-other",
    )
    session.add(other_tenant)
    session.flush()

    due_instance_key = build_scheduler_job_key(
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        qualifier="TimerCatch_1",
    )
    due_definition_key = build_scheduler_job_key(
        job_type=SchedulerJobType.timer_start,
        bpmn_process_definition_id=definition.id,
        qualifier="StartEvent_1",
    )
    future_key = build_scheduler_job_key(
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance.id,
        qualifier="retry",
    )
    locked_key = build_scheduler_job_key(
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        qualifier="TimerCatch_2",
    )
    other_tenant_key = build_scheduler_job_key(
        job_type=SchedulerJobType.timer_start,
        bpmn_process_definition_id=definition.id,
        qualifier="StartEvent_2",
    )

    upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=due_definition_key,
        job_type=SchedulerJobType.timer_start,
        bpmn_process_definition_id=definition.id,
        run_at_in_seconds=50,
        updated_at_in_seconds=50,
    )
    upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=due_instance_key,
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        run_at_in_seconds=60,
        updated_at_in_seconds=60,
    )
    upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=future_key,
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance.id,
        run_at_in_seconds=500,
        updated_at_in_seconds=70,
    )
    locked_job = upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=locked_key,
        job_type=SchedulerJobType.intermediate_timer,
        process_instance_id=process_instance.id,
        run_at_in_seconds=40,
        updated_at_in_seconds=40,
    )
    locked_job.locked_by = "worker-a"
    locked_job.locked_at_in_seconds = 41
    upsert_scheduler_job(
        session,
        tenant_id=other_tenant.id,
        job_key=other_tenant_key,
        job_type=SchedulerJobType.timer_start,
        bpmn_process_definition_id=definition.id,
        run_at_in_seconds=30,
        updated_at_in_seconds=30,
    )

    due_jobs = list_due_scheduler_jobs(
        session,
        tenant_id=tenant.id,
        now_in_seconds=100,
    )
    assert [job.job_key for job in due_jobs] == [due_definition_key, due_instance_key]


def test_delete_scheduler_job_is_tenant_scoped(session: Session) -> None:
    tenant, _definition, process_instance = _seed_scheduler_context(session)
    job_key = build_scheduler_job_key(
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance.id,
        qualifier="retry",
    )
    upsert_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=job_key,
        job_type=SchedulerJobType.process_retry,
        process_instance_id=process_instance.id,
        run_at_in_seconds=200,
        updated_at_in_seconds=190,
    )

    assert delete_scheduler_job(
        session,
        tenant_id="tenant-mismatch",
        job_key=job_key,
    ) is False
    assert delete_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=job_key,
    ) is True
    assert delete_scheduler_job(
        session,
        tenant_id=tenant.id,
        job_key=job_key,
    ) is False


def _seed_scheduler_context(
    session: Session,
) -> tuple[M8flowTenantModel, BpmnProcessDefinitionModel, ProcessInstanceModel]:
    tenant = M8flowTenantModel(
        id="tenant-scheduler",
        name="Tenant Scheduler",
        slug="tenant-scheduler",
    )
    user = UserModel(
        username="scheduler-user",
        email="scheduler-user@example.com",
        service="http://localhost:7002/realms/tenant-scheduler",
        service_id="scheduler-user-keycloak",
        display_name="Scheduler User",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="scheduler-single",
        full_process_model_hash="scheduler-full",
        bpmn_identifier="scheduler-process",
        bpmn_name="Scheduler Process",
        properties_json={},
        created_at_in_seconds=10,
        updated_at_in_seconds=10,
    )
    session.add(definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier="scheduler-process",
        process_model_display_name="Scheduler Process",
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=None,
        status="waiting",
        created_at_in_seconds=20,
        updated_at_in_seconds=20,
    )
    session.add(process_instance)
    session.flush()
    return tenant, definition, process_instance
