from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.task_types import USER_TASK_TYPENAMES
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class TaskDefinitionModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "task_definition"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "bpmn_process_definition_id",
            "bpmn_identifier",
            name="m8f_task_definition_tenant_process_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bpmn_process_definition_id: Mapped[int] = mapped_column(
        ForeignKey("bpmn_process_definition.id"),
        index=True,
        nullable=False,
    )
    bpmn_identifier: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    bpmn_name: Mapped[str | None] = mapped_column(String(255), index=True)
    typename: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    bpmn_process_definition = relationship(
        "BpmnProcessDefinitionModel",
        back_populates="task_definitions",
    )

    def is_user_task(self) -> bool:
        return self.typename in USER_TASK_TYPENAMES
