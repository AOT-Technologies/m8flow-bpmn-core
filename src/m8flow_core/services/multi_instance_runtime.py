"""Multi-instance runtime semantics on top of SpiffWorkflow (M8F-640).

Instance-local adapters, like ``signal_runtime``: no Spiff class is patched
globally. Each loop iteration keeps its own input item, output item and
runtime counters; outputs are collected in input order; and a later
activation of the same activity starts a new output collection.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from SpiffWorkflow.bpmn.specs.mixins.multiinstance_task import MultiInstanceTask
from SpiffWorkflow.spiff.serializer.task_spec import SpiffMultiInstanceConverter
from SpiffWorkflow.spiff.specs.defaults import (
    ParallelMultiInstanceTask,
    SequentialMultiInstanceTask,
)
from SpiffWorkflow.util.deep_merge import DeepMerge
from SpiffWorkflow.util.task import TaskState

# BPMN 2.0 multi-instance runtime values. loopCounter is zero-based, as in Camunda 7.
COUNTERS = (
    "loopCounter",
    "nrOfInstances",
    "nrOfActiveInstances",
    "nrOfCompletedInstances",
)


class _IterationScope(MultiInstanceTask):
    """Iteration-local data, runtime counters and input-ordered outputs.

    Listed after the Spiff class in each runtime class below, so Spiff's
    sequential and parallel control flow reaches these through ``super()``.
    """

    sequential: bool

    def create_child(self, my_task: Any, item: Any, key_or_index: Any = None) -> None:
        # SpiffWorkflow 3.2.0 MultiInstanceTask.create_child, plus the counters.
        task_spec = my_task.workflow.spec.task_specs[self.task_spec]
        if not task_spec.completed_event.is_connected(self.merge_child):
            task_spec.completed_event.connect(self.merge_child)
        for data_item in (self.input_item, self.output_item):
            if data_item is not None and data_item.exists(my_task):
                self.raise_data_exception(
                    f"Multiinstance item {data_item.bpmn_id} already exists.", my_task
                )
        counters = {
            "loopCounter": sum(1 for _ in self._instances(my_task)),
            **self._counters(my_task),
        }
        child = my_task._add_child(task_spec, TaskState.WAITING)
        child.triggered = True
        if self.input_item is not None:
            self.input_item.set(child, deepcopy(item))
        if key_or_index is not None:
            child.internal_data["key_or_index"] = key_or_index
        else:
            child.internal_data["item"] = item
        child.data.update(counters)  # visible to an instance pre-script
        child.task_spec._update(child)
        child.data.update(counters)  # data inheritance overwrites them in nested loops

    def merge_child(self, workflow: Any, child: Any) -> None:
        my_task = child.parent
        mapped = self.data_output is not None and self.output_item is not None
        if mapped and not self.output_item.exists(child):
            self.raise_data_exception("Expected an output item", child)
        my_task.internal_data["merged"].append(str(child.id))
        if not mapped:
            # Without an output mapping, everything but the iteration's own
            # variables flows back, as in Spiff.
            local = set(COUNTERS) | {
                ref.bpmn_id
                for ref in (self.input_item, self.output_item)
                if ref is not None
            }
            DeepMerge.merge(
                my_task.data,
                {key: value for key, value in child.data.items() if key not in local},
            )
        else:
            output = self.data_output.get(my_task)
            data_input = (
                self.data_input.get(my_task) if self.data_input is not None else None
            )
            key_or_index = child.internal_data.get("key_or_index")
            if key_or_index is not None and (
                isinstance(output, Mapping) or output is data_input
            ):
                output[key_or_index] = self.output_item.get(child)  # type: ignore[index]
            else:
                merged = set(my_task.internal_data["merged"])
                # ponytail: rebuilds the list on every merge, O(n^2) per loop;
                # write into a pre-sized list if large loops get slow.
                self.data_output.set(
                    my_task,
                    [
                        self.output_item.get(instance)
                        for instance in self._instances(my_task)
                        if str(instance.id) in merged
                    ],
                )
        counters = self._counters(my_task)
        child.data.update(counters)  # the completion condition reads this child
        for instance in self._instances(my_task):
            if not instance.has_state(TaskState.FINISHED_MASK):
                instance.data.update(counters)

    def init_data_output_with_input_data(self, my_task: Any, input_data: Any) -> None:
        # A later activation (a rework loop) starts a new list instead of
        # failing on the previous activation's results.
        output = self.data_output.get(my_task)
        if isinstance(output, list) and output is not input_data:
            self.data_output.set(my_task, [])
        super().init_data_output_with_input_data(my_task, input_data)

    def init_data_output_with_cardinality(self, my_task: Any) -> None:
        # Replaces Spiff 3.2.0's version, which raises NameError on an
        # existing output collection.
        if not isinstance(self.data_output.get(my_task), Mapping):
            self.data_output.set(my_task, [])

    def _counters(self, my_task: Any) -> dict[str, int]:
        if "m8f_total" not in my_task.internal_data:
            if self.data_input is not None:
                total = len(self.data_input.get(my_task))
            else:
                total = my_task.internal_data.get("cardinality")
                if total is None:  # parallel loops don't keep it
                    total = my_task.workflow.script_engine.evaluate(
                        my_task, self.cardinality
                    )
            my_task.internal_data["m8f_total"] = total
        total = my_task.internal_data["m8f_total"]
        completed = len(my_task.internal_data.get("merged", []))
        remaining = total - completed
        return {
            "nrOfInstances": total,
            "nrOfActiveInstances": min(1, remaining) if self.sequential else remaining,
            "nrOfCompletedInstances": completed,
        }


class RuntimeSequentialMultiInstanceTask(SequentialMultiInstanceTask, _IterationScope):
    sequential = True


class RuntimeParallelMultiInstanceTask(ParallelMultiInstanceTask, _IterationScope):
    sequential = False


_RUNTIME_CLASSES: dict[type, type] = {
    SequentialMultiInstanceTask: RuntimeSequentialMultiInstanceTask,
    ParallelMultiInstanceTask: RuntimeParallelMultiInstanceTask,
}


def configure_multi_instance_serializer(registry: Any) -> None:
    # Keep the historical wire typenames, while restoring into the runtime classes.
    for spiff_class, runtime_class in _RUNTIME_CLASSES.items():
        SpiffMultiInstanceConverter(
            runtime_class, registry, typename=spiff_class.__name__
        )


def adapt_multi_instance_specs(root_spec: Any, subprocess_specs: Any) -> None:
    for spec in [root_spec, *subprocess_specs.values()]:
        for task_spec in spec.task_specs.values():
            runtime_class = _RUNTIME_CLASSES.get(type(task_spec))
            if runtime_class is not None:
                task_spec.__class__ = runtime_class


def in_iteration(task: Any) -> bool:
    """True when the task runs inside a multi-instance iteration, at any depth."""
    while task is not None:
        # The task after a loop is a child of the loop task too; only the
        # loop's own instances count.
        loop = getattr(task.parent, "task_spec", None)
        if (
            isinstance(loop, MultiInstanceTask)
            and task.task_spec.name == loop.task_spec
        ):
            return True
        workflow = task.workflow
        parent_workflow = getattr(workflow, "parent_workflow", None)
        task = (
            parent_workflow.get_task_from_id(workflow.parent_task_id)
            if parent_workflow is not None
            else None
        )
    return False
