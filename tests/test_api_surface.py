from __future__ import annotations

from m8flow_bpmn_core import api

# Snapshot of the public surface. Any change here is a deliberate
# contract change and should be paired with an entry in ``doc/api.md``.
EXPECTED_PUBLIC_API = frozenset(
    {
        # Errors
        "AuthorizationError",
        "AuthorizationDecision",
        "AuthorizationPolicy",
        "AuthorizationPolicyFactory",
        "AuthorizationRequest",
        "BpmnCoreError",
        "InvalidStateError",
        "NotFoundError",
        "ValidationError",
        # Commands
        "ClaimTaskCommand",
        "CompleteTaskCommand",
        "CreateProcessInstanceCommand",
        "DatabaseAuthorizationPolicy",
        "ErrorProcessInstanceCommand",
        "ImportBpmnProcessDefinitionCommand",
        "InitializeProcessInstanceFromDefinitionCommand",
        "InitializeProcessInstanceWorkflowCommand",
        "PROCESS_DEFINITION_IMPORT_COMMAND",
        "PROCESS_RESUME_COMMAND",
        "PROCESS_RETRY_COMMAND",
        "PROCESS_START_COMMAND",
        "PROCESS_SUSPEND_COMMAND",
        "PROCESS_TERMINATE_COMMAND",
        "RecordProcessInstanceEventCommand",
        "ResumeProcessInstanceCommand",
        "RetryProcessInstanceCommand",
        "SuspendProcessInstanceCommand",
        "TASK_CLAIM_COMMAND",
        "TASK_COMPLETE_COMMAND",
        "TerminateProcessInstanceCommand",
        "UpsertProcessInstanceMetadataCommand",
        # Queries
        "GetPendingTasksQuery",
        "GetProcessInstanceEventsQuery",
        "GetProcessInstanceMetadataQuery",
        "GetProcessInstanceQuery",
        "ListErrorProcessInstancesQuery",
        "ListProcessInstancesQuery",
        "ListSuspendedProcessInstancesQuery",
        "ListTerminatedProcessInstancesQuery",
        # Enums
        "ProcessInstanceEventType",
        "ProcessInstanceStatus",
        # Dispatchers
        "authorization_policy_scope",
        "execute_command",
        "execute_query",
        # Service functions
        "advance_process_instance_workflow",
        "claim_task",
        "complete_task",
        "create_process_instance",
        "error_process_instance",
        "get_pending_tasks",
        "get_process_instance",
        "get_process_instance_events",
        "get_process_instance_metadata",
        "list_error_process_instances",
        "list_process_instances",
        "list_suspended_process_instances",
        "list_terminated_process_instances",
        "record_process_instance_event",
        "resolve_lane_assignment_id",
        "resume_process_instance",
        "retry_process_instance",
        "set_default_authorization_policy_factory",
        "suspend_process_instance",
        "terminate_process_instance",
        "upsert_process_instance_metadata",
    }
)


def test_public_api_surface_is_frozen() -> None:
    assert frozenset(api.__all__) == EXPECTED_PUBLIC_API
    for name in EXPECTED_PUBLIC_API:
        assert hasattr(api, name), f"Missing public name: {name}"


def test_public_api_re_exports_task_services() -> None:
    assert callable(api.get_pending_tasks)
    assert callable(api.claim_task)
    assert callable(api.complete_task)
    assert callable(api.DatabaseAuthorizationPolicy)
    assert callable(api.authorization_policy_scope)
    assert callable(api.set_default_authorization_policy_factory)
    assert callable(api.error_process_instance)
    assert callable(api.list_error_process_instances)
    assert callable(api.list_suspended_process_instances)
    assert callable(api.list_terminated_process_instances)
    assert callable(api.retry_process_instance)
    assert callable(api.execute_command)
    assert callable(api.execute_query)
    assert callable(api.create_process_instance)
    assert callable(api.ClaimTaskCommand)
    assert callable(api.CompleteTaskCommand)
    assert callable(api.CreateProcessInstanceCommand)
    assert callable(api.ErrorProcessInstanceCommand)
    assert callable(api.ImportBpmnProcessDefinitionCommand)
    assert callable(api.InitializeProcessInstanceFromDefinitionCommand)
    assert callable(api.InitializeProcessInstanceWorkflowCommand)
    assert callable(api.SuspendProcessInstanceCommand)
    assert callable(api.ResumeProcessInstanceCommand)
    assert callable(api.RetryProcessInstanceCommand)
    assert callable(api.TerminateProcessInstanceCommand)
    assert callable(api.RecordProcessInstanceEventCommand)
    assert callable(api.GetPendingTasksQuery)
    assert callable(api.GetProcessInstanceQuery)
    assert callable(api.GetProcessInstanceEventsQuery)
    assert callable(api.GetProcessInstanceMetadataQuery)
    assert callable(api.ListErrorProcessInstancesQuery)
    assert callable(api.ListSuspendedProcessInstancesQuery)
    assert callable(api.ListTerminatedProcessInstancesQuery)
    assert callable(api.ListProcessInstancesQuery)
    assert api.PROCESS_DEFINITION_IMPORT_COMMAND == "process_definition.import"
    assert api.PROCESS_SUSPEND_COMMAND == "process.suspend"
    assert api.PROCESS_RESUME_COMMAND == "process.resume"
    assert api.PROCESS_RETRY_COMMAND == "process.retry"
    assert api.PROCESS_TERMINATE_COMMAND == "process.terminate"
    assert api.PROCESS_START_COMMAND == "process.start"
    assert api.TASK_CLAIM_COMMAND == "task.claim"
    assert api.TASK_COMPLETE_COMMAND == "task.complete"


def test_error_hierarchy_preserves_builtin_compatibility() -> None:
    # Each domain error also subclasses the matching builtin so callers
    # can catch either the domain class or the builtin.
    assert issubclass(api.ValidationError, api.BpmnCoreError)
    assert issubclass(api.ValidationError, ValueError)
    assert issubclass(api.InvalidStateError, api.ValidationError)
    assert issubclass(api.AuthorizationError, api.BpmnCoreError)
    assert issubclass(api.AuthorizationError, PermissionError)
    assert issubclass(api.NotFoundError, api.BpmnCoreError)
    assert issubclass(api.NotFoundError, LookupError)
