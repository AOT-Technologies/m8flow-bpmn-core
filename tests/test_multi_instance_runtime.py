from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from m8flow_core import api
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.models.user import UserModel
from m8flow_core.services.authorization import ROLE_ADMIN, ensure_v1_role

# Sequential then parallel script-task loops over [1, 2, 3]. Its final script
# raises unless both output collections hold [10, 20, 30], so a complete
# instance proves each loop ran per item and mapped its outputs (M8F-668).
MULTI_INSTANCE_BPMN = (
    Path(__file__).with_name("fixtures") / "simple-multi-instance.bpmn"
).read_text(encoding="utf-8")


def test_sequential_and_parallel_multi_instance_complete(session: Session) -> None:
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
            source_bpmn_xml=MULTI_INSTANCE_BPMN,
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
