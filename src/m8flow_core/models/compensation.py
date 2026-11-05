"""Durable, tenant-scoped compensation journal."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from m8flow_core.models.base import Base
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class CompensationStatus(StrEnum):
    available = "available"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class CompensationScopeModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "compensation_scope"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "scope_id",
            name="m8f_compensation_scope_occurrence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"), index=True
    )
    scope_id: Mapped[str] = mapped_column(String(36))
    parent_scope_id: Mapped[str | None] = mapped_column(String(36))
    activity_id: Mapped[str] = mapped_column(String(255))
    completed: Mapped[bool] = mapped_column(default=False)
    data_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompensationRunModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "compensation_run"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "throw_task_guid",
            name="m8f_compensation_run_occurrence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(36))
    throw_task_guid: Mapped[str] = mapped_column(String(36))
    scope_id: Mapped[str] = mapped_column(String(36))
    wait_for_completion: Mapped[bool]
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompensationRecordModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "compensation_record"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "source_task_guid",
            name="m8f_compensation_record_occurrence",
        ),
        UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "completion_sequence",
            name="m8f_compensation_record_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"), index=True
    )
    source_task_guid: Mapped[str] = mapped_column(String(36))
    activity_id: Mapped[str] = mapped_column(String(255))
    scope_id: Mapped[str] = mapped_column(String(36))
    completion_sequence: Mapped[int]
    handler_id: Mapped[str | None] = mapped_column(String(255))
    handler_task_guid: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    throw_task_guid: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(default=0)
    failure_details: Mapped[str | None] = mapped_column(Text)
    data_hash: Mapped[str] = mapped_column(String(255))
    workflow_state_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
