from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class BpmnProcessModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "bpmn_process"
    __timestamp_compatibility_pairs__ = (
        ("start_in_seconds", "started_at"),
        ("end_in_seconds", "ended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[str | None] = mapped_column(String(36), unique=True)
    bpmn_process_definition_id: Mapped[int] = mapped_column(
        ForeignKey("bpmn_process_definition.id"),
        index=True,
        nullable=False,
    )
    top_level_process_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process.id"), index=True
    )
    direct_parent_process_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process.id"),
        index=True,
    )
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    json_data_hash: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    start_in_seconds: Mapped[float | None] = mapped_column(Numeric(17, 6))
    end_in_seconds: Mapped[float | None] = mapped_column(Numeric(17, 6))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    bpmn_process_definition = relationship(
        "BpmnProcessDefinitionModel",
        back_populates="bpmn_processes",
    )
    tasks = relationship(
        "TaskModel", back_populates="bpmn_process", cascade="all, delete-orphan"
    )
    child_processes = relationship(
        "BpmnProcessModel",
        foreign_keys=[direct_parent_process_id],
        cascade="all",
    )
