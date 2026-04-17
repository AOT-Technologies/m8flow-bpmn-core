from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class BpmnProcessModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "bpmn_process"

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

    bpmn_process_definition = relationship(
        "BpmnProcessDefinitionModel",
        back_populates="bpmn_processes",
    )
    tasks = relationship(
        "TaskModel", back_populates="bpmn_process", cascade="all, delete-orphan"
    )
