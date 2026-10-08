"""Validate data before the final breaking schema cleanup.

This revision is deliberately read-only.  It is placed immediately before the
destructive epoch-column cleanup so an upgrade cannot remove compatibility data
when the remaining breaking migration preconditions are not satisfied.

Revision ID: g8h9i0j1k2l3
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op

revision = "g8h9i0j1k2l3"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

_MIN_EPOCH = Decimal("-62135596800")
_MAX_EPOCH = Decimal("253402300799.999999")

_PRIMARY_KEY_COLUMNS = {
    "task": "guid",
    "future_task": "guid",
}

_MAPPABLE_URI_PATTERNS = (
    re.compile(r"^/(?:v1\.0/)?tasks(?:/(?:\d+|%|\*))?/?$"),
    re.compile(r"^/(?:v1\.0/)?process-instances(?:/(?:\d+|%|\*))?/?$"),
    re.compile(r"^/(?:v1\.0/)?process-models(?:/(?:\d+|%|\*))?/?$"),
    re.compile(r"^/(?:v1\.0/)?tenants(?:/(?:\d+|%|\*))?/?$"),
)

_EVENT_CATEGORIES = {
    "process_instance_created": "process",
    "process_instance_completed": "process",
    "process_instance_error": "process",
    "process_instance_force_run": "process",
    "process_instance_migrated": "process",
    "process_instance_resumed": "process",
    "process_instance_retried": "process",
    "process_instance_rewound_to_task": "process",
    "process_instance_suspended": "process",
    "process_instance_suspended_for_error": "process",
    "process_instance_terminated": "process",
    "task_cancelled": "task",
    "task_completed": "task",
    "task_data_edited": "task",
    "task_executed_manually": "task",
    "task_failed": "task",
    "task_skipped": "task",
}

_PERMISSION_RESOURCE_TYPES = {
    "process_definition",
    "process_instance",
    "process_model",
    "task",
    "tenant",
}

_LEGACY_EPOCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "user": ("created_at_in_seconds", "updated_at_in_seconds"),
    "m8flow_tenant": ("created_at_in_seconds", "updated_at_in_seconds"),
    "bpmn_process_definition": (
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "bpmn_process": ("start_in_seconds", "end_in_seconds"),
    "task_definition": ("created_at_in_seconds", "updated_at_in_seconds"),
    "task": ("start_in_seconds", "end_in_seconds"),
    "process_instance": (
        "start_in_seconds",
        "end_in_seconds",
        "task_updated_at_in_seconds",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "human_task": ("created_at_in_seconds", "updated_at_in_seconds"),
    "work_item": ("created_at_in_seconds", "updated_at_in_seconds"),
    "future_task": (
        "run_at_in_seconds",
        "queued_to_run_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "process_instance_metadata": (
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "process_instance_event": ("timestamp",),
    "process_model_bpmn_version": ("created_at_in_seconds",),
    "scheduler_job": (
        "locked_at_in_seconds",
        "run_at_in_seconds",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
}


def _table_columns(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _sample_ids(result: sa.CursorResult[tuple[object, ...]]) -> list[object]:
    return [row[0] for row in result.fetchmany(20)]


def _validate_work_items(bind: sa.engine.Connection, errors: list[str]) -> None:
    human_task_columns = _table_columns(bind, "human_task")
    work_item_columns = _table_columns(bind, "work_item")
    if not human_task_columns or not work_item_columns:
        return

    missing = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT human_task.id FROM human_task "
                "LEFT JOIN work_item ON work_item.id = human_task.id "
                "WHERE work_item.id IS NULL ORDER BY human_task.id"
            )
        )
    )
    if missing:
        errors.append(f"human_task rows without work_item rows: {missing}")

    orphaned = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT work_item.id FROM work_item "
                "LEFT JOIN human_task ON human_task.id = work_item.id "
                "WHERE human_task.id IS NULL ORDER BY work_item.id"
            )
        )
    )
    if orphaned:
        errors.append(f"orphaned work_item rows: {orphaned}")

    difference_operator = (
        "IS DISTINCT FROM"
        if bind.dialect.name == "postgresql"
        else "IS NOT"
    )
    compared_columns = (
        "task_guid",
        "m8f_tenant_id",
        "process_instance_id",
        "lane_assignment_id",
        "actual_owner_id",
        "completed_by_user_id",
        "task_status",
        "completed",
    )
    comparison_predicate = " OR ".join(
        f"human_task.{column} {difference_operator} work_item.{column}"
        for column in compared_columns
    )
    inconsistent = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT human_task.id FROM human_task "
                "JOIN work_item ON work_item.id = human_task.id "
                f"WHERE {comparison_predicate} "
                "ORDER BY human_task.id"
            )
        )
    )
    if inconsistent:
        errors.append(
            "inconsistent human_task/work_item claim state rows: "
            f"{inconsistent}"
        )


def _validate_identity_columns(bind: sa.engine.Connection, errors: list[str]) -> None:
    columns = _table_columns(bind, "user")
    legacy_to_named = {
        "tenant_specific_field_1": "realm_identifier",
        "tenant_specific_field_2": "external_org_id",
        "tenant_specific_field_3": "external_user_id",
    }
    for legacy_name, named_name in legacy_to_named.items():
        if legacy_name not in columns or named_name not in columns:
            continue
        conflicts = _sample_ids(
            bind.execute(
                sa.text(
                    f'SELECT id FROM "user" WHERE '
                    f'"{legacy_name}" IS NOT NULL AND "{named_name}" IS NOT NULL '
                    f'AND "{legacy_name}" <> "{named_name}" ORDER BY id'
                )
            )
        )
        if conflicts:
            errors.append(
                f"conflicting user identity values for {legacy_name}/{named_name}: "
                f"{conflicts}"
            )


def _validate_process_digests(bind: sa.engine.Connection, errors: list[str]) -> None:
    columns = _table_columns(bind, "bpmn_process_definition")
    required_columns = {
        "m8f_tenant_id",
        "single_process_hash",
        "full_process_model_hash",
    }
    if not required_columns <= columns:
        return
    collisions = bind.execute(
        sa.text(
            "SELECT m8f_tenant_id, COALESCE(full_process_model_hash, "
            "single_process_hash) AS digest, COUNT(*) AS row_count "
            "FROM bpmn_process_definition "
            "WHERE COALESCE(full_process_model_hash, single_process_hash) IS NOT NULL "
            "GROUP BY m8f_tenant_id, COALESCE(full_process_model_hash, "
            "single_process_hash) HAVING COUNT(*) > 1 "
            "ORDER BY m8f_tenant_id, digest"
        )
    ).fetchmany(20)
    if collisions:
        errors.append(
            "process definitions would collide after digest consolidation: "
            f"{[tuple(row) for row in collisions]}"
        )


def _validate_authorization(bind: sa.engine.Connection, errors: list[str]) -> None:
    columns = _table_columns(bind, "permission_target")
    if not {"id", "uri", "resource_type", "resource_id"} <= columns:
        return
    incomplete = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT id FROM permission_target WHERE "
                "resource_type IS NULL AND resource_id IS NOT NULL "
                "ORDER BY id"
            )
        )
    )
    if incomplete:
        errors.append(f"incomplete permission resource pairs: {incomplete}")

    unknown_resource_types = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT id FROM permission_target WHERE resource_type IS NOT NULL "
                "AND resource_type NOT IN ("
                "'process_definition', 'process_instance', 'process_model', "
                "'task', 'tenant') ORDER BY id"
            )
        )
    )
    if unknown_resource_types:
        errors.append(
            "unknown permission resource types: "
            f"{unknown_resource_types}"
        )

    uri_only_rows = bind.execute(
        sa.text(
            "SELECT id, uri FROM permission_target WHERE uri IS NOT NULL "
            "AND resource_type IS NULL AND resource_id IS NULL ORDER BY id"
        )
    ).fetchmany(1000)
    unmappable_uri_only = [
        row[0]
        for row in uri_only_rows
        if not any(
            pattern.fullmatch(row[1].strip())
            for pattern in _MAPPABLE_URI_PATTERNS
        )
    ]
    if unmappable_uri_only:
        errors.append(
            "permission targets require an explicit resource mapping before "
            f"URI fallback removal: {unmappable_uri_only[:20]}"
        )


def _validate_events(bind: sa.engine.Connection, errors: list[str]) -> None:
    columns = _table_columns(bind, "process_instance_event")
    if not {"id", "event_type", "category"} <= columns:
        return
    invalid_categories = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT id FROM process_instance_event WHERE category IS NOT NULL "
                "AND category NOT IN ('process', 'task') ORDER BY id"
            )
        )
    )
    if invalid_categories:
        errors.append(
            "invalid process-instance event categories: "
            f"{invalid_categories}"
        )

    event_types = bind.execute(
        sa.text("SELECT id, event_type FROM process_instance_event")
    ).fetchmany(1000)
    unknown = [row[0] for row in event_types if row[1] not in _EVENT_CATEGORIES]
    if unknown:
        errors.append(f"event types without a category mapping: {unknown[:20]}")


def _validate_timestamps(bind: sa.engine.Connection, errors: list[str]) -> None:
    for table_name, columns in _LEGACY_EPOCH_COLUMNS.items():
        existing_columns = _table_columns(bind, table_name)
        for column_name in columns:
            if column_name not in existing_columns:
                continue
            values = bind.execute(
                sa.text(
                    f'SELECT {_PRIMARY_KEY_COLUMNS.get(table_name, "id")}, '
                    f'"{column_name}" FROM "{table_name}" '
                    f'WHERE "{column_name}" IS NOT NULL LIMIT 1000'
                )
            ).fetchall()
            invalid: list[object] = []
            for row_id, value in values:
                try:
                    epoch = Decimal(str(value))
                except (InvalidOperation, ValueError):
                    invalid.append(row_id)
                    continue
                if not _MIN_EPOCH <= epoch <= _MAX_EPOCH:
                    invalid.append(row_id)
            if invalid:
                errors.append(
                    f"out-of-range {table_name}.{column_name} values: {invalid[:20]}"
                )


def _validate_json_references(bind: sa.engine.Connection, errors: list[str]) -> None:
    json_columns = _table_columns(bind, "json_data")
    if not {"m8f_tenant_id", "hash"} <= json_columns:
        return
    if not _table_columns(bind, "m8flow_tenant"):
        return

    missing_tenants = _sample_ids(
        bind.execute(
            sa.text(
                "SELECT json_data.m8f_tenant_id FROM json_data "
                "LEFT JOIN m8flow_tenant ON m8flow_tenant.id = json_data.m8f_tenant_id "
                "WHERE m8flow_tenant.id IS NULL "
                "GROUP BY json_data.m8f_tenant_id ORDER BY json_data.m8f_tenant_id"
            )
        )
    )
    if missing_tenants:
        errors.append(f"json_data rows reference missing tenants: {missing_tenants}")

    for table_name, hash_column in (
        ("bpmn_process", "json_data_hash"),
        ("task", "json_data_hash"),
        ("task", "python_env_data_hash"),
    ):
        columns = _table_columns(bind, table_name)
        if not {"m8f_tenant_id", hash_column} <= columns:
            continue
        key_column = _PRIMARY_KEY_COLUMNS.get(table_name, "id")
        missing = _sample_ids(
            bind.execute(
                sa.text(
                    f'SELECT source."{key_column}" FROM "{table_name}" AS source '
                    "LEFT JOIN json_data ON json_data.m8f_tenant_id = "
                    "source.m8f_tenant_id AND json_data.hash = "
                    f'source."{hash_column}" '
                    f'WHERE json_data.hash IS NULL ORDER BY source."{key_column}"'
                )
            )
        )
        if missing:
            errors.append(
                f"{table_name}.{hash_column} references missing tenant-scoped "
                f"json_data rows: {missing}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    errors: list[str] = []
    _validate_work_items(bind, errors)
    _validate_identity_columns(bind, errors)
    _validate_process_digests(bind, errors)
    _validate_authorization(bind, errors)
    _validate_events(bind, errors)
    _validate_timestamps(bind, errors)
    _validate_json_references(bind, errors)
    if errors:
        raise RuntimeError(
            "Breaking migration preconditions failed. No destructive changes "
            "were applied:\n- " + "\n- ".join(errors)
        )


def downgrade() -> None:
    # This revision performs validation only and has no schema to restore.
    pass
