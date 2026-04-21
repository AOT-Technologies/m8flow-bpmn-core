from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class BpmnProcessDefinitionModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "bpmn_process_definition"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "full_process_model_hash",
            name="bpmn_process_definition_full_process_model_hash_tenant_unique",
        ),
        UniqueConstraint(
            "m8f_tenant_id",
            "full_process_model_hash",
            "single_process_hash",
            name="process_hash_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    single_process_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_process_model_hash: Mapped[str | None] = mapped_column(String(255))
    bpmn_identifier: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    bpmn_name: Mapped[str | None] = mapped_column(String(255), index=True)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_bpmn_xml: Mapped[str | None] = mapped_column(Text)
    source_dmn_xml: Mapped[str | None] = mapped_column(Text)
    bpmn_version_control_type: Mapped[str | None] = mapped_column(String(50))
    bpmn_version_control_identifier: Mapped[str | None] = mapped_column(String(255))
    updated_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at_in_seconds: Mapped[int | None] = mapped_column(Integer)

    bpmn_processes = relationship(
        "BpmnProcessModel",
        back_populates="bpmn_process_definition",
        cascade="all, delete-orphan",
    )
    task_definitions = relationship(
        "TaskDefinitionModel",
        back_populates="bpmn_process_definition",
        cascade="all, delete-orphan",
    )
