from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventModel
from m8flow_bpmn_core.models.process_instance_metadata import (
    ProcessInstanceMetadataModel,
)
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_sample_app.app import create_app
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.models import SecretModel
from m8flow_sample_app.settings import get_settings


@pytest.fixture
def app_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[FlaskClient]:
    database_path = tmp_path / "sample_app_test.sqlite"
    monkeypatch.setenv(
        "M8FLOW_SAMPLE_APP_DATABASE_URL",
        f"sqlite+pysqlite:///{database_path}",
    )
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_SECRET_KEY", "sample-app-test-secret")
    get_settings.cache_clear()
    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as client:
        yield client

    get_settings.cache_clear()


def test_workflow_can_run_end_to_end_through_the_sample_app(
    app_client: FlaskClient,
) -> None:
    tenant, admin_user, operator_user, reviewer_user = _load_seeded_users()

    _login(app_client, tenant_id=tenant.id, user_id=admin_user.id)

    response = app_client.post(
        "/process-definitions/deploy-demo",
        data={
            "process_model_identifier": "sample-app/e2e-demo",
            "bpmn_name": "Sample App E2E Demo",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)
    assert "deployed" in response_text

    with session_scope() as db_session:
        definition = db_session.scalars(
            select(BpmnProcessDefinitionModel)
            .where(BpmnProcessDefinitionModel.m8f_tenant_id == tenant.id)
            .order_by(BpmnProcessDefinitionModel.id.desc())
        ).first()
        assert definition is not None
        definition_id = definition.id

    response = app_client.post(
        "/process-instances/start",
        data={
            "definition_id": str(definition_id),
            "summary": "Started by the sample-app integration test",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)
    assert "Started process instance" in response_text

    with session_scope() as db_session:
        process_instance = db_session.scalars(
            select(ProcessInstanceModel)
            .where(ProcessInstanceModel.m8f_tenant_id == tenant.id)
            .order_by(ProcessInstanceModel.id.desc())
        ).first()
        assert process_instance is not None
        process_instance_id = process_instance.id
        assert process_instance.status == "user_input_required"

    _login(app_client, tenant_id=tenant.id, user_id=operator_user.id)

    response = app_client.get("/tasks")
    assert "Prepare Request" in response.get_data(as_text=True)

    prepare_task_id = _task_id_for_title(tenant.id, "Prepare Request")
    response = app_client.post(
        f"/tasks/{prepare_task_id}/claim",
        follow_redirects=True,
    )
    assert "claimed" in response.get_data(as_text=True)

    response = app_client.post(
        f"/tasks/{prepare_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "operator_note": "Prepared by the operations user",
                    "amount": 125,
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    _login(app_client, tenant_id=tenant.id, user_id=reviewer_user.id)

    response = app_client.get("/tasks")
    assert "Review Request" in response.get_data(as_text=True)

    review_task_id = _task_id_for_title(tenant.id, "Review Request")
    app_client.post(f"/tasks/{review_task_id}/claim", follow_redirects=True)
    response = app_client.post(
        f"/tasks/{review_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "review_outcome": "approved",
                    "review_comment": "Approved by the reviewer",
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    response = app_client.get(f"/process-instances/{process_instance_id}")
    response_text = response.get_data(as_text=True)
    assert "operator_note" in response_text
    assert "review_outcome" in response_text
    assert "process_instance_completed" in response_text

    with session_scope() as db_session:
        process_instance = db_session.get(ProcessInstanceModel, process_instance_id)
        assert process_instance is not None
        assert process_instance.status == "complete"

        metadata_rows = list(
            db_session.scalars(
                select(ProcessInstanceMetadataModel)
                .where(
                    ProcessInstanceMetadataModel.m8f_tenant_id == tenant.id,
                    ProcessInstanceMetadataModel.process_instance_id
                    == process_instance_id,
                )
                .order_by(ProcessInstanceMetadataModel.key.asc())
            )
        )
        metadata_map = {row.key: row.value for row in metadata_rows}
        assert metadata_map == {
            "amount": "125",
            "operator_note": "Prepared by the operations user",
            "review_comment": "Approved by the reviewer",
            "review_outcome": "approved",
        }

        events = list(
            db_session.scalars(
                select(ProcessInstanceEventModel)
                .where(
                    ProcessInstanceEventModel.m8f_tenant_id == tenant.id,
                    ProcessInstanceEventModel.process_instance_id
                    == process_instance_id,
                )
                .order_by(ProcessInstanceEventModel.id.asc())
            )
        )
        assert [event.event_type for event in events] == [
            "process_instance_created",
            "task_completed",
            "task_completed",
            "process_instance_completed",
        ]


def test_secret_crud_works_through_the_sample_app(app_client: FlaskClient) -> None:
    tenant, admin_user, _operator_user, _reviewer_user = _load_seeded_users()
    _login(app_client, tenant_id=tenant.id, user_id=admin_user.id)

    response = app_client.post(
        "/secrets/new",
        data={
            "key": "SMTP_PASSWORD",
            "value": "super-secret",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)
    assert "created" in response_text
    assert "SMTP_PASSWORD" in response_text
    assert "super-secret" not in response_text

    with session_scope() as db_session:
        secret = db_session.scalars(
            select(SecretModel)
            .where(
                SecretModel.m8f_tenant_id == tenant.id,
                SecretModel.key == "SMTP_PASSWORD",
            )
            .order_by(SecretModel.id.desc())
        ).first()
        assert secret is not None
        secret_id = secret.id
        assert secret.value == "super-secret"

    response = app_client.post(
        f"/secrets/{secret_id}/edit",
        data={
            "key": "SMTP_PASSWORD",
            "value": "updated-secret",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)
    assert "updated" in response_text
    assert "updated-secret" not in response_text

    with session_scope() as db_session:
        secret = db_session.get(SecretModel, secret_id)
        assert secret is not None
        assert secret.value == "updated-secret"

    response = app_client.post(
        f"/secrets/{secret_id}/delete",
        follow_redirects=True,
    )
    assert "deleted" in response.get_data(as_text=True)

    with session_scope() as db_session:
        assert db_session.get(SecretModel, secret_id) is None


def _load_seeded_users() -> tuple[M8flowTenantModel, UserModel, UserModel, UserModel]:
    with session_scope() as db_session:
        tenant = db_session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.id == "sample-tenant-alpha"
            )
        )
        assert tenant is not None
        users = list(
            db_session.scalars(
                select(UserModel)
                .where(
                    UserModel.tenant_specific_field_1 == tenant.id,
                )
                .order_by(UserModel.username.asc())
            )
        )
        user_by_username = {user.username: user for user in users}
        return (
            tenant,
            user_by_username["alpha-admin"],
            user_by_username["alpha-operator"],
            user_by_username["alpha-reviewer"],
        )


def _login(app_client: FlaskClient, *, tenant_id: str, user_id: int) -> None:
    response = app_client.post(
        "/session/select",
        data={
            "tenant_id": tenant_id,
            "user_id": str(user_id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200


def _task_id_for_title(tenant_id: str, task_title: str) -> int:
    with session_scope() as db_session:
        task = db_session.scalars(
            select(HumanTaskModel)
            .where(
                HumanTaskModel.m8f_tenant_id == tenant_id,
                HumanTaskModel.task_title == task_title,
                HumanTaskModel.completed.is_(False),
            )
            .order_by(HumanTaskModel.id.asc())
        ).first()
        assert task is not None
        return task.id
