from __future__ import annotations

# These are the BPMN task type names that the core treats as user-facing work.
# Keep the domain vocabulary independent from any persistence model so runtime
# and import code can share one source of truth.
USER_TASK_TYPENAMES = frozenset({"ManualTask", "NoneTask", "UserTask"})
