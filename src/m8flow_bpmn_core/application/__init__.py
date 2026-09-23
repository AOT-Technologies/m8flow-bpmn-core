from __future__ import annotations

from importlib import import_module

from m8flow_bpmn_core.application.commands import (
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
from m8flow_bpmn_core.application.queries import (
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ListErrorProcessInstancesQuery,
    ListProcessInstancesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
)


def __getattr__(name: str):
    if name in {"execute_command", "execute_query"}:
        dispatcher = import_module("m8flow_bpmn_core.application.dispatcher")
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
