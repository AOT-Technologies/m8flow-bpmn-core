from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "advance_process_instance_workflow",
    "claim_task",
    "complete_task",
    "create_process_instance",
    "error_process_instance",
    "get_pending_tasks",
    "get_process_instance_events",
    "get_process_instance_metadata",
    "initialize_process_instance_workflow",
    "list_error_process_instances",
    "list_suspended_process_instances",
    "list_terminated_process_instances",
    "record_process_instance_event",
    "resolve_lane_assignment_id",
    "run_due_scheduler_jobs",
    "schedule_process_instance_retry",
    "resume_process_instance",
    "retry_process_instance",
    "suspend_process_instance",
    "terminate_process_instance",
    "upsert_process_instance_metadata",
]

_EXPORT_MODULES = {
    "advance_process_instance_workflow": (
        "m8flow_bpmn_core.services.workflow_runtime"
    ),
    "initialize_process_instance_workflow": (
        "m8flow_bpmn_core.services.workflow_runtime"
    ),
    "resolve_lane_assignment_id": "m8flow_bpmn_core.services.workflow_runtime",
    "claim_task": "m8flow_bpmn_core.services.tasks",
    "complete_task": "m8flow_bpmn_core.services.tasks",
    "get_pending_tasks": "m8flow_bpmn_core.services.tasks",
    "create_process_instance": "m8flow_bpmn_core.services.process_instances",
    "error_process_instance": "m8flow_bpmn_core.services.process_instances",
    "get_process_instance_events": "m8flow_bpmn_core.services.process_instances",
    "get_process_instance_metadata": "m8flow_bpmn_core.services.process_instances",
    "list_error_process_instances": (
        "m8flow_bpmn_core.services.process_instances"
    ),
    "list_suspended_process_instances": (
        "m8flow_bpmn_core.services.process_instances"
    ),
    "list_terminated_process_instances": (
        "m8flow_bpmn_core.services.process_instances"
    ),
    "record_process_instance_event": (
        "m8flow_bpmn_core.services.process_instances"
    ),
    "schedule_process_instance_retry": (
        "m8flow_bpmn_core.services.process_instances"
    ),
    "resume_process_instance": "m8flow_bpmn_core.services.process_instances",
    "retry_process_instance": "m8flow_bpmn_core.services.process_instances",
    "run_due_scheduler_jobs": "m8flow_bpmn_core.services.scheduler_runtime",
    "suspend_process_instance": "m8flow_bpmn_core.services.process_instances",
    "terminate_process_instance": "m8flow_bpmn_core.services.process_instances",
    "upsert_process_instance_metadata": (
        "m8flow_bpmn_core.services.process_instances"
    ),
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
