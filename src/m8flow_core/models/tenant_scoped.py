from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column


class TenantScoped:
    __abstract__ = True


class M8fTenantScopedMixin:
    m8f_tenant_id: Mapped[str] = mapped_column(
        ForeignKey("m8flow_tenant.id"),
        nullable=False,
        index=True,
    )
