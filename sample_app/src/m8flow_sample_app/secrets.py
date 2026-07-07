from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import NotFoundError, ValidationError
from m8flow_bpmn_core.models.user import UserModel
from m8flow_sample_app.models import SecretModel


@dataclass(frozen=True, slots=True)
class SecretListItem:
    secret: SecretModel
    user: UserModel | None


def list_secrets(session: Session, *, tenant_id: str) -> list[SecretListItem]:
    rows = session.execute(
        select(SecretModel, UserModel)
        .outerjoin(UserModel, UserModel.id == SecretModel.user_id)
        .where(SecretModel.m8f_tenant_id == tenant_id)
        .order_by(SecretModel.key.asc(), SecretModel.id.asc())
    )
    return [
        SecretListItem(secret=secret, user=user)
        for secret, user in rows
    ]


def get_secret(session: Session, *, tenant_id: str, secret_id: int) -> SecretModel:
    secret = session.scalar(
        select(SecretModel).where(
            SecretModel.m8f_tenant_id == tenant_id,
            SecretModel.id == secret_id,
        )
    )
    if secret is None:
        raise NotFoundError(
            f"Secret {secret_id} was not found for tenant {tenant_id}."
        )
    return secret


def create_secret(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    key: str,
    value: str,
) -> SecretModel:
    normalized_key = _normalize_key(key)
    normalized_value = _normalize_value(value)
    now = round(time.time())

    secret = SecretModel(
        m8f_tenant_id=tenant_id,
        key=normalized_key,
        value=normalized_value,
        user_id=user_id,
        created_at_in_seconds=now,
        updated_at_in_seconds=now,
    )
    session.add(secret)
    _flush_with_uniqueness_guard(session, normalized_key)
    return secret


def update_secret(
    session: Session,
    *,
    tenant_id: str,
    secret_id: int,
    user_id: int,
    key: str,
    value: str | None,
) -> SecretModel:
    secret = get_secret(session, tenant_id=tenant_id, secret_id=secret_id)
    secret.key = _normalize_key(key)
    if value is not None:
        secret.value = _normalize_value(value)
    secret.user_id = user_id
    secret.updated_at_in_seconds = round(time.time())
    _flush_with_uniqueness_guard(session, secret.key)
    return secret


def delete_secret(session: Session, *, tenant_id: str, secret_id: int) -> None:
    secret = get_secret(session, tenant_id=tenant_id, secret_id=secret_id)
    session.delete(secret)
    session.flush()


def _normalize_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValidationError("Secret key cannot be blank.")
    if len(normalized) > 50:
        raise ValidationError("Secret key must be 50 characters or fewer.")
    return normalized


def _normalize_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationError("Secret value cannot be blank.")
    return normalized


def _flush_with_uniqueness_guard(session: Session, key: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationError(
            f"A secret with key {key!r} already exists for this tenant."
        ) from exc
