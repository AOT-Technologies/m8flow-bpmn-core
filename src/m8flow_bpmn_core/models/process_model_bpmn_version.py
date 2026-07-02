from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessModelBpmnVersionModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_model_bpmn_version"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "process_model_identifier",
            "bpmn_xml_hash",
            name="uq_process_model_bpmn_version_tenant_model_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    process_model_identifier: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    bpmn_xml_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bpmn_xml_file_contents: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_in_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True
    )
