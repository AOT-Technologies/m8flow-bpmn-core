from __future__ import annotations

from datetime import UTC, datetime

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.user import UserModel

POST_2038 = datetime(2040, 1, 1, 12, 30, tzinfo=UTC)


def test_post_2038_datetime_is_supported() -> None:
    assert POST_2038.year == 2040


def test_timezone_aware_columns_are_present() -> None:
    for mapper in Base.registry.mappers:
        for column in mapper.columns:
            if column.name.endswith("_at") or column.name == "occurred_at":
                assert column.type.timezone is True


def test_active_orm_has_no_legacy_epoch_columns() -> None:
    legacy_names = {
        "created_at_in_seconds",
        "updated_at_in_seconds",
        "start_in_seconds",
        "end_in_seconds",
        "task_updated_at_in_seconds",
        "run_at_in_seconds",
        "queued_to_run_at_in_seconds",
        "locked_at_in_seconds",
        "timestamp",
    }
    for mapper in Base.registry.mappers:
        assert legacy_names.isdisjoint(
            column.name for column in mapper.columns
        ), mapper.class_.__name__


def test_native_datetime_is_canonical(
    session,
) -> None:
    user = UserModel(
        username="future-user",
        service="test",
        service_id="future-user",
        created_at=POST_2038,
        updated_at=POST_2038,
    )
    session.add(user)
    session.flush()

    assert user.created_at == POST_2038
    assert user.created_at.timestamp() == 2_209_033_800

    later = datetime(2041, 1, 1, 12, 30, tzinfo=UTC)
    user.updated_at = later
    session.flush()

    assert user.updated_at == later
