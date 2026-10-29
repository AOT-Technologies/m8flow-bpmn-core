from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import UniqueConstraint, create_engine, pool

from m8flow_core.models import Base
from m8flow_core.settings import get_settings

target_metadata = Base.metadata


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Skip unique constraints that PostgreSQL folds into the primary key.

    PostgreSQL doesn't create a UNIQUE constraint whose columns are exactly the
    primary key's, so comparing against a live database would always report it
    as missing.
    """
    if (
        type_ == "unique_constraint"
        and not reflected
        and isinstance(obj, UniqueConstraint)
        and context.get_context().dialect.name == "postgresql"
    ):
        primary_key = set(obj.table.primary_key.columns.keys())
        return set(obj.columns.keys()) != primary_key
    return True


def run_migrations_offline() -> None:
    settings = get_settings()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_as_batch=settings.database_url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = get_settings()
    connectable = create_engine(settings.database_url, poolclass=pool.NullPool)

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
                include_object=include_object,
                render_as_batch=connection.dialect.name == "sqlite",
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
