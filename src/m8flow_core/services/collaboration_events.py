from __future__ import annotations

from typing import Any

from SpiffWorkflow.bpmn.util import BpmnEvent
from SpiffWorkflow.spiff.specs.event_definitions import MessageEventDefinition


class CollaborationCatchDefinition(MessageEventDefinition):
    """Wait for delivery to this specific node through the scheduler."""

    def catches(self, my_task: Any, event: BpmnEvent) -> bool:
        return False


class CollaborationThrowDefinition(MessageEventDefinition):
    """Send a routed throw through the deployment scheduler."""

    def throw(self, my_task: Any) -> None:
        # A participant spec can also be called as a nested workflow.
        if my_task.workflow.parent_workflow is not None:
            local_event = MessageEventDefinition(
                self.name,
                self.correlation_properties,
                self.expression,
                self.message_var,
                self.process_correlations,
            )
            local_event.throw(my_task)
            return
        my_task.workflow.bpmn_events.append(
            BpmnEvent(
                self,
                payload={
                    "source_ref": my_task.task_spec.name,
                    "task_id": str(my_task.id),
                },
            )
        )
