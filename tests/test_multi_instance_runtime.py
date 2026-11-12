#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

FIXTURES = Path(__file__).with_name("fixtures")


# Each fixture runs a sequential then a parallel script-task loop, and its
# final script raises unless both output collections hold [10, 20, 30]. A
# complete instance proves each loop ran once per instance and mapped its
# outputs (M8F-668).
@pytest.mark.parametrize(
    "fixture",
    [
        # Collection-driven: loopDataInputRef over [1, 2, 3].
        "simple-multi-instance.bpmn",
        # loopCardinality: a literal 3 (sequential) and an expression (parallel);
        # each instance gets its index in inputDataItem.
        "multi-instance-cardinality.bpmn",
    ],
)
def test_sequential_and_parallel_multi_instance_complete(
    session: Session, fixture: str
) -> None:
    tenant = M8flowTenantModel(id="mi-tenant", name="Loops", slug="mi-tenant")
    user = UserModel(
        username="looper",
        email="looper@example.com",
        service="http://localhost/realms/mi-tenant",
        service_id="looper",
        display_name="Looper",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([tenant, user])
    session.flush()
    ensure_v1_role(
        session, tenant_id=tenant.id, role_name=ROLE_ADMIN, user_ids=[user.id]
    )

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_identifier="multi-instance-model",
            user_id=user.id,
            source_bpmn_xml=(FIXTURES / fixture).read_text(encoding="utf-8"),
        ),
    )
    instance = api.execute_command(
        session,
        api.InitializeProcessInstanceFromDefinitionCommand(
            tenant_id=tenant.id,
            bpmn_process_definition_id=definition.id,
            process_initiator_id=user.id,
        ),
    )

    assert instance.status == api.ProcessInstanceStatus.complete
