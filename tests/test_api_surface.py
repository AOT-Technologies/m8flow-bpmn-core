from __future__ import annotations

from m8flow_bpmn_core import api


def test_public_api_re_exports_task_services() -> None:
    assert callable(api.get_pending_tasks)
    assert callable(api.claim_task)
    assert callable(api.complete_task)
    assert callable(api.error_process_instance)
    assert callable(api.list_error_process_instances)
    assert callable(api.list_suspended_process_instances)
    assert callable(api.list_terminated_process_instances)
    assert callable(api.retry_process_instance)
    assert callable(api.execute_command)
    assert callable(api.execute_query)
    assert callable(api.ClaimTaskCommand)
    assert callable(api.CompleteTaskCommand)
    assert callable(api.ErrorProcessInstanceCommand)
    assert callable(api.SuspendProcessInstanceCommand)
    assert callable(api.ResumeProcessInstanceCommand)
    assert callable(api.RetryProcessInstanceCommand)
    assert callable(api.TerminateProcessInstanceCommand)
    assert callable(api.GetPendingTasksQuery)
    assert callable(api.GetProcessInstanceQuery)
    assert callable(api.RecordProcessInstanceEventCommand)
    assert callable(api.ListErrorProcessInstancesQuery)
    assert callable(api.ListSuspendedProcessInstancesQuery)
    assert callable(api.ListTerminatedProcessInstancesQuery)
    assert callable(api.ListProcessInstancesQuery)
