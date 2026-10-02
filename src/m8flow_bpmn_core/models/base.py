from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapper

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Base, "before_insert", propagate=True)
@event.listens_for(Base, "before_update", propagate=True)
def _normalize_datetime_values(
    mapper: Mapper[Any], connection: Connection, target: Any
) -> None:
    """Accept numeric epoch values at the boundary while callers migrate.

    The mapped schema and public contract are datetime-only. This narrow
    coercion keeps old fixtures and staged downstream deployments writable
    without retaining legacy columns or legacy attribute names.
    """
    for column in mapper.columns:
        if not isinstance(column.type, DateTime):
            continue
        value = getattr(target, column.key, None)
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            setattr(target, column.key, datetime.fromtimestamp(float(value), UTC))


@event.listens_for(Base, "load", propagate=True)
def _restore_utc_on_loaded_datetimes(target: Any, context: Any) -> None:
    _restore_utc_values(target)


@event.listens_for(Base, "refresh", propagate=True)
def _restore_utc_on_refreshed_datetimes(
    target: Any, context: Any, attrs: Any
) -> None:
    _restore_utc_values(target)


def _restore_utc_values(target: Any) -> None:
    for column in target.__mapper__.columns:
        if not isinstance(column.type, DateTime):
            continue
        value = getattr(target, column.key, None)
        if isinstance(value, datetime) and value.tzinfo is None:
            setattr(target, column.key, value.replace(tzinfo=UTC))
