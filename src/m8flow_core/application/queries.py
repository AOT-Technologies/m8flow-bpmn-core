#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from dataclasses import dataclass

from m8flow_core.models.process_instance import ProcessInstanceStatus


@dataclass(frozen=True, slots=True)
class GetSignalPublicationQuery:
    tenant_id: str
    occurrence_id: str


@dataclass(frozen=True, slots=True)
class ListSignalDeliveriesQuery:
    tenant_id: str
    occurrence_id: str


@dataclass(frozen=True, slots=True)
class GetPendingTasksQuery:
    tenant_id: str
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class GetProcessInstanceEventsQuery:
    tenant_id: str
    process_instance_id: int


@dataclass(frozen=True, slots=True)
class GetProcessInstanceMetadataQuery:
    tenant_id: str
    process_instance_id: int


@dataclass(frozen=True, slots=True)
class GetProcessInstanceQuery:
    tenant_id: str
    process_instance_id: int


@dataclass(frozen=True, slots=True)
class ListErrorProcessInstancesQuery:
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListSuspendedProcessInstancesQuery:
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListTerminatedProcessInstancesQuery:
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ListProcessInstancesQuery:
    tenant_id: str
    status: ProcessInstanceStatus | str | None = None


@dataclass(frozen=True, slots=True)
class GetProcessInstanceCompensationsQuery:
    tenant_id: str
    process_instance_id: int
