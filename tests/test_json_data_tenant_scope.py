from __future__ import annotations

from sqlalchemy import func, select

from m8flow_bpmn_core.models.json_data import JsonDataModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel


def _add_tenant(session, tenant_id: str) -> None:
    session.add(
        M8flowTenantModel(
            id=tenant_id,
            name=tenant_id,
            slug=tenant_id,
        )
    )
    session.flush()


def test_equal_payload_hashes_are_isolated_by_tenant(session) -> None:
    _add_tenant(session, "tenant-a")
    _add_tenant(session, "tenant-b")

    hash_a = JsonDataModel.create_or_update_from_payload(
        session, "tenant-a", {"shared": True}
    )
    hash_b = JsonDataModel.create_or_update_from_payload(
        session, "tenant-b", {"shared": True}
    )
    session.flush()

    assert hash_a == hash_b
    assert JsonDataModel.get_for_tenant(session, "tenant-a", hash_a) is not None
    assert JsonDataModel.get_for_tenant(session, "tenant-b", hash_b) is not None
    assert JsonDataModel.get_for_tenant(session, "tenant-a", "missing") is None
    assert session.scalar(select(func.count()).select_from(JsonDataModel)) == 2


def test_payload_update_does_not_cross_tenant_boundary(session) -> None:
    _add_tenant(session, "tenant-a")
    _add_tenant(session, "tenant-b")

    payload_hash = JsonDataModel.create_or_update_from_payload(
        session, "tenant-a", {"value": "original"}
    )
    JsonDataModel.create_or_update_from_payload(
        session, "tenant-b", {"value": "original"}
    )
    updated_hash = JsonDataModel.create_or_update_from_payload(
        session, "tenant-a", {"value": "updated"}
    )
    session.flush()

    assert JsonDataModel.get_for_tenant(session, "tenant-a", payload_hash).data == {
        "value": "original"
    }
    assert JsonDataModel.get_for_tenant(session, "tenant-b", payload_hash).data == {
        "value": "original"
    }
    assert JsonDataModel.get_for_tenant(session, "tenant-a", updated_hash).data == {
        "value": "updated"
    }
    assert JsonDataModel.get_for_tenant(session, "tenant-b", updated_hash) is None
