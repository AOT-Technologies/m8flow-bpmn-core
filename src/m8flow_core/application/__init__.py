from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from m8flow_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ErrorProcessInstanceCommand,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    ScheduleProcessInstanceRetryCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
)
from m8flow_core.application.queries import (
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ListErrorProcessInstancesQuery,
    ListProcessInstancesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
)

if TYPE_CHECKING:
    from m8flow_core.application.dispatcher import execute_command, execute_query


def __getattr__(name: str) -> Any:
    if name in {"execute_command", "execute_query"}:
        dispatcher = import_module("m8flow_core.application.dispatcher")
        return getattr(dispatcher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ClaimTaskCommand",
    "CompleteTaskCommand",
    "CreateProcessInstanceCommand",
    "ErrorProcessInstanceCommand",
    "GetPendingTasksQuery",
    "GetProcessInstanceEventsQuery",
    "GetProcessInstanceMetadataQuery",
    "GetProcessInstanceQuery",
    "ImportBpmnProcessDefinitionCommand",
    "InitializeProcessInstanceFromDefinitionCommand",
    "InitializeProcessInstanceWorkflowCommand",
    "ListErrorProcessInstancesQuery",
    "ListProcessInstancesQuery",
    "ListSuspendedProcessInstancesQuery",
    "ListTerminatedProcessInstancesQuery",
    "RecordProcessInstanceEventCommand",
    "ResumeProcessInstanceCommand",
    "RetryProcessInstanceCommand",
    "ScheduleProcessInstanceRetryCommand",
    "SuspendProcessInstanceCommand",
    "TerminateProcessInstanceCommand",
    "UpsertProcessInstanceMetadataCommand",
    "execute_command",
    "execute_query",
]
