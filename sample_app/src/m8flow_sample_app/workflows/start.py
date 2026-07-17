from __future__ import annotations

import time

from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_sample_app import service_tasks as sample_app_service_tasks


def start_process_instance(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    definition_id: int,
    summary: str | None,
) -> ProcessInstanceModel:
    with api.service_task_registry_scope(
        sample_app_service_tasks.build_sample_app_service_task_registry
    ):
        return api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=tenant_id,
                bpmn_process_definition_id=definition_id,
                process_initiator_id=user_id,
                summary=summary,
                started_at_in_seconds=round(time.time()),
            ),
        )
