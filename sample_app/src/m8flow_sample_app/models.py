from __future__ import annotations

from sqlalchemy import Integer, MetaData, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from m8flow_bpmn_core.models.base import NAMING_CONVENTION
from m8flow_bpmn_core.models.tenant_scoped import TenantScoped


class SampleAppBase(DeclarativeBase):
    """Declarative base for host-app-owned tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class SecretModel(TenantScoped, SampleAppBase):
    __tablename__ = "secret"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "key",
            name="secret_key_tenant_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    m8f_tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    updated_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at_in_seconds: Mapped[int | None] = mapped_column(Integer)


ALL_METADATA = [SampleAppBase.metadata]
