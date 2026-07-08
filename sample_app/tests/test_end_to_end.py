from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from m8flow_bpmn_core import api
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

    captured_smtp_requests: list[api.ServiceTaskRequest] = []

    def _build_fake_connector_proxy_registry(
        *_args: object,
        **_kwargs: object,
    ) -> api.ServiceTaskRegistry:
        return api.ServiceTaskRegistry(
            connectors=(
                _FakeSmtpConnector(captured_requests=captured_smtp_requests),
            )
        )

    monkeypatch.setattr(
        "m8flow_sample_app.service_tasks.api.build_connector_proxy_service_task_registry",
        _build_fake_connector_proxy_registry,
    )
    get_settings.cache_clear()
    app = create_app()
    app.config.update(TESTING=True)
    app.extensions["captured_smtp_requests"] = captured_smtp_requests

    with app.test_client() as client:
        yield client

    get_settings.cache_clear()


def test_high_value_request_routes_through_finance_and_sends_email(
    app_client: FlaskClient,
) -> None:
    (
        tenant,
        admin_user,
        operator_user,
        finance_user,
        reviewer_user,
    ) = _load_seeded_users()
    process_instance_id = _deploy_and_start_demo_workflow(
        app_client,
        tenant_id=tenant.id,
        admin_user_id=admin_user.id,
    )

    _login(app_client, tenant_id=tenant.id, user_id=operator_user.id)
    response = app_client.get("/tasks")
    assert "Submit Reimbursement Request" in response.get_data(as_text=True)

    submit_task_id = _task_id_for_title(tenant.id, "Submit Reimbursement Request")
    response = app_client.post(
        f"/tasks/{submit_task_id}/claim",
        follow_redirects=True,
    )
    assert "claimed" in response.get_data(as_text=True)

    response = app_client.post(
        f"/tasks/{submit_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "requester_name": "Andre Example",
                    "requester_email": "andre@example.com",
                    "expense_description": "Conference hotel and travel",
                    "amount": 1250,
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    _login(app_client, tenant_id=tenant.id, user_id=finance_user.id)
    response = app_client.get("/tasks")
    assert "Finance Review" in response.get_data(as_text=True)

    finance_task_id = _task_id_for_title(tenant.id, "Finance Review")
    app_client.post(f"/tasks/{finance_task_id}/claim", follow_redirects=True)
    response = app_client.post(
        f"/tasks/{finance_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "finance_recommendation": "approved",
                    "finance_comment": "Budget is available for reimbursement.",
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
                    "review_comment": "Approved after policy review.",
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    response = app_client.get(f"/process-instances/{process_instance_id}")
    response_text = response.get_data(as_text=True)
    assert "requester_email" in response_text
    assert "finance_recommendation" in response_text
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
        assert metadata_map["amount"] == "1250"
        assert metadata_map["requester_name"] == "Andre Example"
        assert metadata_map["requester_email"] == "andre@example.com"
        assert metadata_map["expense_description"] == "Conference hotel and travel"
        assert metadata_map["finance_recommendation"] == "approved"
        assert (
            metadata_map["finance_comment"]
            == "Budget is available for reimbursement."
        )
        assert metadata_map["review_outcome"] == "approved"
        assert metadata_map["review_comment"] == "Approved after policy review."
        assert "email_body" not in metadata_map
        assert all(
            not key.endswith("_raw") and not key.endswith("_text")
            for key in metadata_map
        )

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
            "task_completed",
            "process_instance_completed",
        ]

    captured_smtp_requests = app_client.application.extensions["captured_smtp_requests"]
    assert len(captured_smtp_requests) == 1
    smtp_request = captured_smtp_requests[0]
    assert smtp_request.operation_id == "smtp/SendHTMLEmail"
    assert smtp_request.context is not None
    assert smtp_request.context.tenant_id == tenant.id
    assert smtp_request.parameters is not None
    assert smtp_request.parameters["email_to"] == "andre@example.com"
    assert smtp_request.parameters["smtp_host"] == "sandbox.smtp.mailtrap.io"
    assert smtp_request.parameters["smtp_port"] == 2525
    assert smtp_request.parameters["smtp_user"] == "fce006e9972d8b"
    assert smtp_request.parameters["smtp_password"] == "CHANGE_ME_IN_SECRETS_UI"
    assert smtp_request.parameters["smtp_starttls"] is True
    assert (
        smtp_request.parameters["email_from"]
        == "sample-app-reimbursements@example.com"
    )
    assert "approved" in str(smtp_request.parameters["email_subject"]).lower()
    assert smtp_request.parameters["email_body"] == smtp_request.parameters["email_body_html"]


def test_high_value_request_rejected_by_finance_skips_review_and_sends_email(
    app_client: FlaskClient,
) -> None:
    (
        tenant,
        admin_user,
        operator_user,
        finance_user,
        reviewer_user,
    ) = _load_seeded_users()
    process_instance_id = _deploy_and_start_demo_workflow(
        app_client,
        tenant_id=tenant.id,
        admin_user_id=admin_user.id,
    )

    _login(app_client, tenant_id=tenant.id, user_id=operator_user.id)
    submit_task_id = _task_id_for_title(tenant.id, "Submit Reimbursement Request")
    app_client.post(f"/tasks/{submit_task_id}/claim", follow_redirects=True)
    response = app_client.post(
        f"/tasks/{submit_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "requester_name": "Andre Example",
                    "requester_email": "andre@example.com",
                    "expense_description": "Executive travel",
                    "amount": 2200,
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    _login(app_client, tenant_id=tenant.id, user_id=finance_user.id)
    finance_task_id = _task_id_for_title(tenant.id, "Finance Review")
    app_client.post(f"/tasks/{finance_task_id}/claim", follow_redirects=True)
    response = app_client.post(
        f"/tasks/{finance_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "finance_recommendation": "rejected",
                    "finance_comment": "Manager pre-approval is missing.",
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    _login(app_client, tenant_id=tenant.id, user_id=reviewer_user.id)
    response = app_client.get("/tasks")
    assert "No pending tasks are currently assigned to this user." in (
        response.get_data(as_text=True)
    )

    with session_scope() as db_session:
        process_instance = db_session.get(ProcessInstanceModel, process_instance_id)
        assert process_instance is not None
        assert process_instance.status == "complete"

        metadata_map = {
            row.key: row.value
            for row in db_session.scalars(
                select(ProcessInstanceMetadataModel)
                .where(
                    ProcessInstanceMetadataModel.m8f_tenant_id == tenant.id,
                    ProcessInstanceMetadataModel.process_instance_id
                    == process_instance_id,
                )
                .order_by(ProcessInstanceMetadataModel.key.asc())
            )
        }
        assert metadata_map["amount"] == "2200"
        assert metadata_map["finance_recommendation"] == "rejected"
        assert metadata_map["finance_comment"] == "Manager pre-approval is missing."
        assert "review_outcome" not in metadata_map
        assert "email_body" not in metadata_map
        assert all(
            not key.endswith("_raw") and not key.endswith("_text")
            for key in metadata_map
        )

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

    captured_smtp_requests = app_client.application.extensions["captured_smtp_requests"]
    assert len(captured_smtp_requests) == 1
    smtp_request = captured_smtp_requests[0]
    assert smtp_request.parameters is not None
    assert "rejected" in str(smtp_request.parameters["email_subject"]).lower()
    assert "Manager pre-approval is missing." in str(
        smtp_request.parameters["email_body_html"]
    )
    assert smtp_request.parameters["email_body"] == smtp_request.parameters["email_body_html"]


def test_low_value_request_skips_finance_review(
    app_client: FlaskClient,
) -> None:
    (
        tenant,
        admin_user,
        operator_user,
        finance_user,
        reviewer_user,
    ) = _load_seeded_users()
    process_instance_id = _deploy_and_start_demo_workflow(
        app_client,
        tenant_id=tenant.id,
        admin_user_id=admin_user.id,
    )

    _login(app_client, tenant_id=tenant.id, user_id=operator_user.id)
    submit_task_id = _task_id_for_title(tenant.id, "Submit Reimbursement Request")
    app_client.post(f"/tasks/{submit_task_id}/claim", follow_redirects=True)
    response = app_client.post(
        f"/tasks/{submit_task_id}/complete",
        data={
            "task_payload_json": json.dumps(
                {
                    "requester_name": "Andre Example",
                    "requester_email": "andre@example.com",
                    "expense_description": "Office supplies",
                    "amount": 125,
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    _login(app_client, tenant_id=tenant.id, user_id=finance_user.id)
    response = app_client.get("/tasks")
    response_text = response.get_data(as_text=True)
    assert "No pending tasks are currently assigned to this user." in response_text

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
                    "review_outcome": "rejected",
                    "review_comment": "Receipt was incomplete.",
                }
            )
        },
        follow_redirects=True,
    )
    assert "completed" in response.get_data(as_text=True)

    with session_scope() as db_session:
        process_instance = db_session.get(ProcessInstanceModel, process_instance_id)
        assert process_instance is not None
        assert process_instance.status == "complete"

        metadata_map = {
            row.key: row.value
            for row in db_session.scalars(
                select(ProcessInstanceMetadataModel)
                .where(
                    ProcessInstanceMetadataModel.m8f_tenant_id == tenant.id,
                    ProcessInstanceMetadataModel.process_instance_id
                    == process_instance_id,
                )
                .order_by(ProcessInstanceMetadataModel.key.asc())
            )
        }
        assert metadata_map["amount"] == "125"
        assert metadata_map["review_outcome"] == "rejected"
        assert "finance_recommendation" not in metadata_map
        assert "email_body" not in metadata_map
        assert all(
            not key.endswith("_raw") and not key.endswith("_text")
            for key in metadata_map
        )

    captured_smtp_requests = app_client.application.extensions["captured_smtp_requests"]
    assert len(captured_smtp_requests) == 1
    assert captured_smtp_requests[0].parameters is not None
    assert (
        captured_smtp_requests[0].parameters["email_subject"]
        == "Reimbursement request rejected: $125.00 for Andre Example"
    )
    assert (
        captured_smtp_requests[0].parameters["email_body"]
        == captured_smtp_requests[0].parameters["email_body_html"]
    )


def test_session_selection_page_shows_standalone_mode_banner(
    app_client: FlaskClient,
) -> None:
    response = app_client.get("/session/select")

    assert response.status_code == 200
    assert "Standalone Sample-App Mode" in response.get_data(as_text=True)


def test_secret_crud_works_through_the_sample_app(app_client: FlaskClient) -> None:
    tenant, admin_user, _operator_user, _finance_user, _reviewer_user = (
        _load_seeded_users()
    )
    _login(app_client, tenant_id=tenant.id, user_id=admin_user.id)

    with session_scope() as db_session:
        seeded_secret_keys = set(
            db_session.scalars(
                select(SecretModel.key).where(SecretModel.m8f_tenant_id == tenant.id)
            )
        )
    assert {
        "MAILTRAP_SMTP_HOST",
        "MAILTRAP_SMTP_PORT",
        "MAILTRAP_SMTP_USERNAME",
        "MAILTRAP_SMTP_PASSWORD",
        "MAILTRAP_SMTP_STARTTLS",
        "MAILTRAP_EMAIL_FROM",
    }.issubset(seeded_secret_keys)

    response = app_client.post(
        "/secrets/new",
        data={
            "key": "CUSTOM_REIMBURSEMENT_TOKEN",
            "value": "super-secret",
        },
        follow_redirects=True,
    )
    response_text = response.get_data(as_text=True)
    assert "created" in response_text
    assert "CUSTOM_REIMBURSEMENT_TOKEN" in response_text
    assert "super-secret" not in response_text

    with session_scope() as db_session:
        secret = db_session.scalars(
            select(SecretModel)
            .where(
                SecretModel.m8f_tenant_id == tenant.id,
                SecretModel.key == "CUSTOM_REIMBURSEMENT_TOKEN",
            )
            .order_by(SecretModel.id.desc())
        ).first()
        assert secret is not None
        secret_id = secret.id
        assert secret.value == "super-secret"

    response = app_client.post(
        f"/secrets/{secret_id}/edit",
        data={
            "key": "CUSTOM_REIMBURSEMENT_TOKEN",
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


def _deploy_and_start_demo_workflow(
    app_client: FlaskClient,
    *,
    tenant_id: str,
    admin_user_id: int,
) -> int:
    _login(app_client, tenant_id=tenant_id, user_id=admin_user_id)

    response = app_client.post(
        "/process-definitions/deploy-demo",
        data={
            "process_model_identifier": "sample-app/e2e-demo",
            "bpmn_name": "Sample App E2E Demo",
        },
        follow_redirects=True,
    )
    assert "deployed" in response.get_data(as_text=True)

    with session_scope() as db_session:
        definition = db_session.scalars(
            select(BpmnProcessDefinitionModel)
            .where(BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id)
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
    assert "Started process instance" in response.get_data(as_text=True)

    with session_scope() as db_session:
        process_instance = db_session.scalars(
            select(ProcessInstanceModel)
            .where(ProcessInstanceModel.m8f_tenant_id == tenant_id)
            .order_by(ProcessInstanceModel.id.desc())
        ).first()
        assert process_instance is not None
        assert process_instance.status == "user_input_required"
        return process_instance.id


def _load_seeded_users() -> tuple[
    M8flowTenantModel,
    UserModel,
    UserModel,
    UserModel,
    UserModel,
]:
    with session_scope() as db_session:
        tenant = db_session.scalar(
            select(M8flowTenantModel).where(
                M8flowTenantModel.slug == "sample-tenant-alpha"
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
            user_by_username["alpha-finance-reviewer"],
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


class _FakeSmtpConnector:
    connector_key = "smtp"

    def __init__(
        self,
        *,
        captured_requests: list[api.ServiceTaskRequest],
    ) -> None:
        self._captured_requests = captured_requests
        self._command = api.ServiceTaskCommandDefinition(
            connector_key="smtp",
            command_name="SendHTMLEmail",
        )

    def list_commands(self) -> tuple[api.ServiceTaskCommandDefinition, ...]:
        return (self._command,)

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        self._captured_requests.append(request)
        return api.ServiceTaskResult(
            payload={"message": "fake smtp delivery accepted"},
            metadata={"status_code": 200},
        )
