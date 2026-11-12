#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Host-provided process definitions for BPMN call activities."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallActivityContext:
    """Tenant and parent identity supplied to a call-activity resolver."""

    tenant_id: str
    called_element: str
    parent_process_instance_id: int | None
    parent_process_definition_id: int | None


@dataclass(frozen=True, slots=True)
class CallActivityDefinition:
    """A child BPMN process definition returned by the host resolver.

    ``process_id`` must exactly match BPMN ``calledElement``. XML may contain
    multiple executable processes, but the named process must be present.
    """

    process_id: str
    bpmn_xml: str | bytes
    dmn_xml: str | bytes | None = None


CallActivityResolver = Callable[
    [CallActivityContext],
    CallActivityDefinition | Sequence[CallActivityDefinition] | None,
]


__all__ = [
    "CallActivityContext",
    "CallActivityDefinition",
    "CallActivityResolver",
]
