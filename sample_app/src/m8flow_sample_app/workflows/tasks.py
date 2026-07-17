from __future__ import annotations

import json

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.errors import NotFoundError
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.human_task_user import HumanTaskUserModel
from m8flow_sample_app import service_tasks as sample_app_service_tasks


def list_pending_tasks(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
) -> list[HumanTaskModel]:
    return api.execute_query(
        session,
        api.GetPendingTasksQuery(
            tenant_id=tenant_id,
            user_id=user_id,
        ),
    )


def claim_task(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    human_task_id: int,
) -> HumanTaskModel:
    return api.execute_command(
        session,
        api.ClaimTaskCommand(
            tenant_id=tenant_id,
            human_task_id=human_task_id,
            user_id=user_id,
        ),
    )


def complete_task(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    human_task_id: int,
    task_payload: dict[str, object] | None,
) -> HumanTaskModel:
    with api.service_task_registry_scope(
        sample_app_service_tasks.build_sample_app_service_task_registry
    ):
        return api.execute_command(
            session,
            api.CompleteTaskCommand(
                tenant_id=tenant_id,
                human_task_id=human_task_id,
                user_id=user_id,
                task_payload=task_payload,
            ),
        )


def get_accessible_task(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    human_task_id: int,
) -> HumanTaskModel:
    task = session.scalar(
        select(HumanTaskModel).where(
            HumanTaskModel.m8f_tenant_id == tenant_id,
            HumanTaskModel.id == human_task_id,
            exists(
                select(1).where(
                    HumanTaskUserModel.m8f_tenant_id == tenant_id,
                    HumanTaskUserModel.human_task_id == HumanTaskModel.id,
                    HumanTaskUserModel.user_id == user_id,
                )
            ),
        )
    )
    if task is None:
        raise NotFoundError(
            f"Task {human_task_id} was not found for the selected tenant and user."
        )
    return task


def build_task_payload_from_json_text(
    json_text: str,
) -> dict[str, object] | None:
    normalized = json_text.strip()
    if not normalized:
        return None

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Task payload is not valid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Task payload JSON must decode to an object.")

    for key in payload.keys():
        if not isinstance(key, str):
            raise ValueError("Task payload keys must be strings.")
    return payload
