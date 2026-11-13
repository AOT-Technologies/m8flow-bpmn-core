#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Domain error hierarchy for the m8flow-core public API.

Every error raised by a public service or dispatcher path is a subclass of
``BpmnCoreError``. Each leaf class additionally inherits from the matching
builtin exception (``ValueError`` / ``PermissionError`` / ``LookupError``)
so callers can catch either the domain class or the builtin, whichever
fits the call site.
"""

from __future__ import annotations


class BpmnCoreError(Exception):
    """Base class for every public error raised by m8flow-core."""

    compensation_recovery_flushed: bool = False


class ValidationError(BpmnCoreError, ValueError):
    """Inputs to a command or query are malformed or contradictory."""


class InvalidStateError(ValidationError):
    """The target entity is in a state that does not allow the operation.

    Examples: suspending a terminal process instance, resuming one that is not
    suspended, claiming a completed task.
    """


class AuthorizationError(BpmnCoreError, PermissionError):
    """The caller is not allowed to perform the operation.

    Raised for tenant-membership and task-assignment violations.
    """


class NotFoundError(BpmnCoreError, LookupError):
    """The requested entity does not exist for the supplied tenant."""


class CompensationExecutionError(BpmnCoreError, RuntimeError):
    """A non-connector compensation handler failed during execution."""


class ServiceTaskExecutionError(BpmnCoreError, RuntimeError):
    """A BPMN service task failed while invoking a registered connector."""


class CallActivityResolutionError(ValidationError):
    """A call activity's referenced process definition cannot be resolved."""


__all__ = [
    "AuthorizationError",
    "BpmnCoreError",
    "CallActivityResolutionError",
    "CompensationExecutionError",
    "InvalidStateError",
    "NotFoundError",
    "ServiceTaskExecutionError",
    "ValidationError",
]
