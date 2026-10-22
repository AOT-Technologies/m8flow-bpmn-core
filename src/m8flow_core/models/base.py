from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
