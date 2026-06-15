from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from m8flow_bpmn_core.models.base import Base


class JsonDataModel(Base):
    __tablename__ = "json_data"

    hash: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    @classmethod
    def normalized_payload(
        cls, data: Mapping[str, Any] | dict[str, Any] | None
    ) -> dict[str, Any]:
        if data is None:
            return {}
        normalized_json = json.dumps(dict(data), sort_keys=True, default=str)
        return json.loads(normalized_json)

    @classmethod
    def hash_payload(
        cls, data: Mapping[str, Any] | dict[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        normalized_payload = cls.normalized_payload(data)
        normalized_json = json.dumps(
            normalized_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(
            normalized_json.encode("utf-8")
        ).hexdigest()
        return payload_hash, normalized_payload

    @classmethod
    def create_or_update_from_payload(
        cls,
        session: Session,
        data: Mapping[str, Any] | dict[str, Any] | None,
    ) -> str:
        payload_hash, normalized_payload = cls.hash_payload(data)
        record = session.get(cls, payload_hash)
        if record is None:
            session.add(cls(hash=payload_hash, data=normalized_payload))
        else:
            record.data = normalized_payload
        return payload_hash
