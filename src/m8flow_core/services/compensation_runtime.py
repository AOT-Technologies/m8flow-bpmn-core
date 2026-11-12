#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Compensation coordinator, serialized with the owning workflow.

The snapshot is the recovery image; journal tables are its queryable projection.
Both are written in the caller's transaction. No callback commits a transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from SpiffWorkflow.bpmn.serializer.helpers.encoder import create_encoder
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.util.task import TaskState

from m8flow_core.errors import (
    CompensationExecutionError,
    InvalidStateError,
    NotFoundError,
    ServiceTaskExecutionError,
)
from m8flow_core.models.process_instance_event import TaskEventType


def coordinator(workflow: Any) -> CompensationCoordinator | None:
    return getattr(workflow, "_m8f_compensation", None)


def execution_workflows(workflow: Any) -> list[Any]:
    comp = coordinator(workflow)
    return [workflow, *comp.handlers.values()] if comp else [workflow]


def runtime_tasks(workflow: Any, **kwargs: Any) -> list[Any]:
    return [
        task
        for tree in execution_workflows(workflow)
        for task in tree.get_tasks(**kwargs)
    ]


def runtime_task(workflow: Any, task_id: UUID) -> Any:
    for tree in execution_workflows(workflow):
        for scope in [tree, *tree.subprocesses.values()]:
            task = scope.tasks.get(task_id)
            if task is not None:
                return task
    raise NotFoundError(f"Runtime task {task_id} was not found")


def runtime_completed(workflow: Any) -> bool:
    comp = coordinator(workflow)
    return workflow.completed and (not comp or not comp.pending)


def connector_compensation_metadata(task: Any) -> dict[str, Any]:
    """Give each connector occurrence its own retry-stable deduplication key.

    A structural path also remains stable if creating a handler is rolled back;
    random task UUIDs in a newly created handler would not have that property.
    """
    context = getattr(task.workflow.top_workflow, "_m8f_compensation_context", None)
    if context is None:
        return {}
    path = []
    current = task
    while current is not None:
        path.append(current.task_spec.name)
        parent = current.parent
        if parent is None and current.workflow.parent_task_id is not None:
            parent = current.workflow.top_workflow.get_task_from_id(
                current.workflow.parent_task_id
            )
        current = parent
    context = dict(context)
    context["idempotency_key"] = str(
        uuid5(UUID(context["idempotency_key"]), "/".join(path))
    )
    return {"compensation": context}


class CompensationCoordinator:
    def __init__(
        self, workflow: Any, serializer: Any, state: dict[str, Any] | None = None
    ) -> None:
        self.workflow = workflow
        self.serializer = serializer
        if state is not None and state.get("version") != 1:
            raise InvalidStateError("Unsupported compensation snapshot version")
        self._encoder = create_encoder(serializer.registry)
        self.state = state or {
            "version": 1,
            "bindings": {},
            "templates": {},
            "scopes": {},
            "records": {},
            "runs": {},
            "sequence": 0,
        }
        self.handlers: dict[str, Any] = {}
        self.events: list[tuple[str, str]] = []
        self._bound: set[int] = set()
        self.bind(workflow)

    @property
    def pending(self) -> bool:
        return any(
            run["status"] in {"running", "failed"}
            for run in self.state["runs"].values()
        )

    def bind(self, workflow: Any) -> None:
        workflow._m8f_compensation = self
        for spec in [workflow.spec, *workflow.subprocess_specs.values()]:
            for task_spec in spec.task_specs.values():
                if id(task_spec) not in self._bound:
                    task_spec.completed_event.connect(self.completed)
                    self._bound.add(id(task_spec))

    def scope(self, workflow: Any) -> dict[str, Any]:
        scope_id = str(workflow.parent_task_id or workflow.task_tree.id)
        scopes = self.state["scopes"]
        if scope_id not in scopes:
            parent = workflow.parent_workflow
            handler_context = getattr(workflow, "_m8f_compensation_context", {})
            scopes[scope_id] = {
                "scope_id": scope_id,
                "parent_scope_id": (
                    self.scope(parent)["scope_id"]
                    if parent
                    else handler_context.get("scope_id")
                ),
                "activity_id": workflow.spec.name,
                "completed": False,
                "data": {},
            }
        return cast(dict[str, Any], scopes[scope_id])

    def completed(self, workflow: Any, task: Any) -> None:
        scope = self.scope(workflow)
        data = self.encode_payload(task.data)
        scope["data"] = data
        activity_id = getattr(task.task_spec, "bpmn_id", None)
        binding = self.state["bindings"].get(activity_id)
        child = workflow.top_workflow.subprocesses.get(task.id)
        child_scope = self.scope(child) if child is not None else None
        if child_scope is not None:
            child_scope["completed"] = True
            child_scope["data"] = data
        implicit = child_scope and any(
            record["scope_id"] == child_scope["scope_id"]
            for record in self.state["records"].values()
        )
        if not binding and not implicit:
            return
        key = str(task.id)
        if key in self.state["records"]:
            return
        self.state["sequence"] += 1
        self.state["records"][key] = {
            "source_task_guid": key,
            "activity_id": activity_id,
            "scope_id": scope["scope_id"],
            "completion_sequence": self.state["sequence"],
            "handler_id": binding["handler_id"] if binding else None,
            "handler_task_guid": None,
            "run_id": None,
            "throw_task_guid": None,
            "status": "available",
            "attempts": 0,
            "failure_details": None,
            "data": data,
        }

    def encode_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        # Use the same encoding as Spiff task data (e.g. for UTC datetimes).
        clean = self.serializer.registry.clean(data)
        return cast(dict[str, Any], json.loads(json.dumps(clean, cls=self._encoder)))

    def trigger(self, task: Any) -> dict[str, Any]:
        key = str(task.id)
        if key in self.state["runs"]:
            return cast(dict[str, Any], self.state["runs"][key])
        scope = self.scope(task.workflow)
        scope["data"] = self.encode_payload(task.data)
        selected = self._eligible(scope["scope_id"], task.task_spec.activity_ref)
        run = {
            "run_id": str(uuid5(NAMESPACE_URL, "m8flow:compensation:" + key)),
            "throw_task_guid": key,
            "scope_id": scope["scope_id"],
            "wait_for_completion": task.task_spec.wait_for_completion,
            "status": "running" if selected else "completed",
            "records": [],
        }
        self.state["runs"][key] = run

        def reserve(record: dict[str, Any]) -> None:
            record.update(status="queued", run_id=run["run_id"], throw_task_guid=key)
            if not record["handler_id"]:
                for child in self._eligible(record["source_task_guid"], None):
                    reserve(child)
            run["records"].append(record["source_task_guid"])

        for record in selected:
            reserve(record)
        return run

    def _eligible(
        self, scope_id: str, activity_ref: str | None
    ) -> list[dict[str, Any]]:
        return sorted(
            (
                record
                for record in self.state["records"].values()
                if record["scope_id"] == scope_id
                and record["status"] == "available"
                and (activity_ref is None or record["activity_id"] == activity_ref)
            ),
            key=lambda record: record["completion_sequence"],
            reverse=True,
        )

    def _start(self, record: dict[str, Any]) -> None:
        template = self.state["templates"][record["handler_id"]]
        spec = self.serializer.from_dict(deepcopy(template["spec"]))
        sub_specs = {
            key: self.serializer.from_dict(deepcopy(value))
            for key, value in template["subprocess_specs"].items()
        }
        handler = BpmnWorkflow(
            spec, sub_specs, script_engine=self.workflow.script_engine
        )
        self.bind(handler)
        scope = self.state["scopes"][record["scope_id"]]
        data = self.serializer.registry.restore(deepcopy(scope["data"]))
        handler.data.update(deepcopy(data))
        handler.task_tree.data.update(data)
        self.handlers[record["source_task_guid"]] = handler
        target = next(
            task
            for task in handler.get_tasks()
            if getattr(task.task_spec, "bpmn_id", None) == record["handler_id"]
        )
        record["handler_task_guid"] = str(target.id)
        record["status"] = "running"
        record["attempts"] += 1
        self._context(record, handler)
        self.events.append((TaskEventType.compensation_started.value, str(target.id)))

    def _context(self, record: dict[str, Any], handler: Any) -> None:
        handler._m8f_compensation_context = {
            "source_task_guid": record["source_task_guid"],
            "scope_id": record["scope_id"],
            "run_id": record["run_id"],
            "idempotency_key": str(
                uuid5(
                    NAMESPACE_URL,
                    f"m8flow:undo:{record['source_task_guid']}:{record['handler_id']}",
                )
            ),
        }

    def run(self) -> None:
        """Advance all execution trees cooperatively, stopping at external waits."""
        if any(run["status"] == "failed" for run in self.state["runs"].values()):
            return
        while True:
            progressed = False
            for run in list(self.state["runs"].values()):
                if run["status"] != "running":
                    continue
                pending = [
                    self.state["records"][key]
                    for key in run["records"]
                    if self.state["records"][key]["status"] != "completed"
                ]
                if not pending:
                    run["status"] = "completed"
                    if run["wait_for_completion"]:
                        throw = runtime_task(
                            self.workflow, UUID(run["throw_task_guid"])
                        )
                        if throw.state == TaskState.STARTED:
                            throw.complete()
                    progressed = True
                    continue
                record = pending[0]
                if not record["handler_id"]:
                    record["status"] = "completed"
                    progressed = True
                    continue
                if record["status"] == "queued":
                    self._start(record)
                    progressed = True
                handler = self.handlers[record["source_task_guid"]]
                try:
                    progressed = self._step(handler) or progressed
                except Exception as exc:
                    record["status"] = "failed"
                    record["failure_details"] = str(exc)
                    run["status"] = "failed"
                    self.events.append(
                        (
                            TaskEventType.compensation_failed.value,
                            record["handler_task_guid"],
                        )
                    )
                    if isinstance(exc, ServiceTaskExecutionError):
                        raise
                    raise CompensationExecutionError(
                        f"Compensation handler {record['handler_id']!r} failed: {exc}"
                    ) from exc
                if handler.completed:
                    self.scope(handler)["completed"] = True
                    record["status"] = "completed"
                    self.events.append(
                        (
                            TaskEventType.compensation_completed.value,
                            record["handler_task_guid"],
                        )
                    )
                    progressed = True
            progressed = self._step(self.workflow) or progressed
            if self.workflow.completed:
                self.scope(self.workflow)["completed"] = True
            if not progressed:
                return

    @staticmethod
    def _step(workflow: Any) -> bool:
        workflow.refresh_timers()
        tasks = workflow.get_tasks(state=TaskState.READY, manual=False)
        if not tasks:
            return False
        tasks[0].run()
        return True

    def retry(self, metadata: dict[str, Any] | None = None) -> bool:
        failed = [
            record
            for record in self.state["records"].values()
            if record["status"] == "failed"
        ]
        for record in failed:
            handler = self.handlers[record["source_task_guid"]]
            for task in handler.get_tasks(state=TaskState.ERROR):
                task.data.update(metadata or {})
                task._set_state(TaskState.READY)
            record.update(
                status="running", failure_details=None, attempts=record["attempts"] + 1
            )
            self.state["runs"][record["throw_task_guid"]]["status"] = "running"
            self.events.append(
                (TaskEventType.compensation_started.value, record["handler_task_guid"])
            )
        return bool(failed)

    def snapshot(self) -> dict[str, Any]:
        return {
            **deepcopy(self.state),
            "handlers": {
                key: self.handler_snapshot(handler)
                for key, handler in self.handlers.items()
            },
        }

    def handler_snapshot(self, handler: Any) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.serializer.serialize_json(handler)))

    def restore_handlers(self, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            self.serializer.migrate(value)
            handler = self.serializer.from_dict(value)
            handler.script_engine = self.workflow.script_engine
            self.bind(handler)
            self.handlers[key] = handler
            self._context(self.state["records"][key], handler)
