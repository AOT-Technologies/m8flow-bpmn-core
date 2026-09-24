from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.user import UserModel


def test_legacy_epoch_values_populate_native_utc_columns(session: Session) -> None:
    user = UserModel(
        username="timestamp-user",
        email="timestamp@example.com",
        service="timestamp-service",
        service_id="timestamp-user",
        created_at_in_seconds=1_700_000_000,
        updated_at_in_seconds=1_700_000_001,
    )
    session.add(user)
    session.flush()

    assert user.created_at == datetime.fromtimestamp(1_700_000_000, UTC)
    assert user.updated_at == datetime.fromtimestamp(1_700_000_001, UTC)


def test_native_datetime_values_populate_legacy_epoch_columns(session: Session) -> None:
    created_at = datetime(2040, 1, 2, 3, 4, 5, 600_000, tzinfo=UTC)
    updated_at = datetime(2040, 1, 2, 3, 4, 6, 600_000, tzinfo=UTC)
    user = UserModel(
        username="native-timestamp-user",
        email="native-timestamp@example.com",
        service="timestamp-service",
        service_id="native-timestamp-user",
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(user)
    session.flush()

    assert user.created_at_in_seconds == round(created_at.timestamp())
    assert user.updated_at_in_seconds == round(updated_at.timestamp())


def test_timestamp_columns_are_timezone_aware_datetime_columns() -> None:
    user_table = Base.metadata.tables["user"]
    tenant_table = Base.metadata.tables["m8flow_tenant"]

    assert isinstance(user_table.c.created_at.type, DateTime)
    assert user_table.c.created_at.type.timezone is True
    assert isinstance(tenant_table.c.updated_at.type, DateTime)
    assert tenant_table.c.updated_at.type.timezone is True
