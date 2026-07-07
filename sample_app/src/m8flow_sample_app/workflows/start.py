from __future__ import annotations

import time

from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel


def start_process_instance(
    session: Session,
    *,
    tenant_id: str,
    user_id: int,
    definition_id: int,
    summary: str | None,
) -> ProcessInstanceModel:
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
