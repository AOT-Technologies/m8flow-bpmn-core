"""Durable signal publications, subscriptions and delivery receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from m8flow_core.models.base import Base
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class SignalRegistryStateModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "signal_registry_state"
    m8f_tenant_id: Mapped[str] = mapped_column(
        ForeignKey("m8flow_tenant.id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SignalPublicationModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "signal_publication"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id", "occurrence_id", name="uq_signal_publication_occurrence"
        ),
        UniqueConstraint("m8f_tenant_id", "id", name="uq_signal_publication_tenant_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSON(none_as_null=False))
    source_process_instance_id: Mapped[int | None] = mapped_column(Integer)
    source_task_guid: Mapped[str | None] = mapped_column(String(36))
    published_by_user_id: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalSubscriptionModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "signal_subscription"
    __table_args__ = (
        ForeignKeyConstraint(
            ["m8f_tenant_id", "process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
        ),
        ForeignKeyConstraint(
            ["m8f_tenant_id", "definition_id"],
            ["bpmn_process_definition.m8f_tenant_id", "bpmn_process_definition.id"],
        ),
        CheckConstraint(
            "kind IN ('start', 'catch', 'boundary', 'event_subprocess')",
            name="m8f_signal_subscription_kind",
        ),
        UniqueConstraint(
            "m8f_tenant_id", "subscription_key", name="uq_signal_subscription_key"
        ),
        UniqueConstraint(
            "m8f_tenant_id", "id", name="uq_signal_subscription_tenant_id"
        ),
        Index("ix_signal_subscription_match", "m8f_tenant_id", "signal_name", "active"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_key: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    process_instance_id: Mapped[int | None] = mapped_column(Integer, index=True)
    definition_id: Mapped[int | None] = mapped_column(Integer)
    scope_id: Mapped[str | None] = mapped_column(String(36))
    task_guid: Mapped[str | None] = mapped_column(String(36))
    task_spec_name: Mapped[str] = mapped_column(String(255))
    process_model_identifier: Mapped[str | None] = mapped_column(String(255))
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalDeliveryModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "signal_delivery"
    __table_args__ = (
        ForeignKeyConstraint(
            ["m8f_tenant_id", "process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
        ),
        CheckConstraint(
            "status IN ('pending', 'delivered', 'skipped')",
            name="m8f_signal_delivery_status",
        ),
        CheckConstraint("attempts >= 0", name="m8f_signal_delivery_attempts"),
        UniqueConstraint("m8f_tenant_id", "publication_id", "subscription_id"),
        ForeignKeyConstraint(
            ["m8f_tenant_id", "publication_id"],
            ["signal_publication.m8f_tenant_id", "signal_publication.id"],
        ),
        ForeignKeyConstraint(
            ["m8f_tenant_id", "subscription_id"],
            ["signal_subscription.m8f_tenant_id", "signal_subscription.id"],
        ),
        Index("ix_signal_delivery_due", "m8f_tenant_id", "status", "retry_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subscription_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(2000))
    process_instance_id: Mapped[int | None] = mapped_column(Integer)
