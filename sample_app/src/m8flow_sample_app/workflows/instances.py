from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventModel
from m8flow_bpmn_core.models.process_instance_metadata import (
    ProcessInstanceMetadataModel,
)


@dataclass(frozen=True, slots=True)
class ProcessInstanceDetail:
    process_instance: ProcessInstanceModel
    metadata: list[ProcessInstanceMetadataModel]
    events: list[ProcessInstanceEventModel]
    human_tasks: list[HumanTaskModel]


def list_process_instances(
    session: Session,
    *,
    tenant_id: str,
) -> list[ProcessInstanceModel]:
    return api.execute_query(
        session,
        api.ListProcessInstancesQuery(
            tenant_id=tenant_id,
            status=None,
        ),
    )


def get_process_instance_detail(
    session: Session,
    *,
    tenant_id: str,
    process_instance_id: int,
) -> ProcessInstanceDetail:
    process_instance = api.execute_query(
        session,
        api.GetProcessInstanceQuery(
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        ),
    )
    metadata = api.execute_query(
        session,
        api.GetProcessInstanceMetadataQuery(
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        ),
    )
    events = api.execute_query(
        session,
        api.GetProcessInstanceEventsQuery(
            tenant_id=tenant_id,
            process_instance_id=process_instance_id,
        ),
    )
    human_tasks = sorted(process_instance.human_tasks, key=lambda item: item.id)
    return ProcessInstanceDetail(
        process_instance=process_instance,
        metadata=metadata,
        events=events,
        human_tasks=human_tasks,
    )
