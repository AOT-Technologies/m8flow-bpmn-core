from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.bpmn_process import BpmnProcessModel
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_bpmn_core.models.task import TaskModel
from m8flow_bpmn_core.models.task_definition import TaskDefinitionModel
from m8flow_bpmn_core.models.tenant import M8flowTenantModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.utils.keycloak import (
    ProvisionedKeycloakOrganization,
    ProvisionedKeycloakSharedRealmContext,
    ProvisionedKeycloakUser,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import conditional_approval_poc as example_poc  # noqa: E402


def _lane_owners() -> dict[str, list[str]]:
    return {
        "Manager": ["poc-manager", "poc-reviewer"],
        "Finance": ["poc-finance"],
    }


def test_backend_deployment_writes_process_group_model_and_files(
    tmp_path: Path,
) -> None:
    deployment = example_poc._deploy_conditional_approval_definition_to_m8flow_backend(
        process_models_root=tmp_path,
        tenant_root="tenant-conditional-approval-example",
        lane_owners=_lane_owners(),
    )

    assert deployment.deployed is True
    assert deployment.already_deployed is False
    assert deployment.warnings == ()

    group_dir = (
        tmp_path
        / "tenant-conditional-approval-example"
        / example_poc.M8FLOW_BACKEND_PROCESS_GROUP_ID
    )
    model_dir = group_dir / example_poc.M8FLOW_BACKEND_PROCESS_MODEL_ID

    assert json.loads((group_dir / "process_group.json").read_text()) == (
        example_poc._backend_process_group_payload()
    )
    assert json.loads((model_dir / "process_model.json").read_text()) == (
        example_poc._backend_process_model_payload()
    )
    assert (model_dir / example_poc.EXAMPLE_DMN_PATH.name).read_text(
        encoding="utf-8"
    ) == example_poc.EXAMPLE_DMN_PATH.read_text(encoding="utf-8")

    bpmn_text = (model_dir / example_poc.EXAMPLE_BPMN_PATH.name).read_text(
        encoding="utf-8"
    )
    assert '"Manager" : [\'poc-manager\', \'poc-reviewer\']' in bpmn_text
    assert '"Finance" : [\'poc-finance\']' in bpmn_text


def test_backend_deployment_warns_and_does_not_overwrite_existing_model(
    tmp_path: Path,
) -> None:
    first_deployment = (
        example_poc._deploy_conditional_approval_definition_to_m8flow_backend(
            process_models_root=tmp_path,
            tenant_root="tenant-conditional-approval-example",
            lane_owners=_lane_owners(),
        )
    )
    model_json_path = (
        first_deployment.process_models_root
        / first_deployment.tenant_root
        / first_deployment.process_group_id
        / first_deployment.process_model_id
        / "process_model.json"
    )
    sentinel_payload = {"display_name": "Sentinel Deployment"}
    model_json_path.write_text(
        f"{json.dumps(sentinel_payload, indent=4)}\n",
        encoding="utf-8",
    )

    second_deployment = (
        example_poc._deploy_conditional_approval_definition_to_m8flow_backend(
            process_models_root=tmp_path,
            tenant_root="tenant-conditional-approval-example",
            lane_owners=_lane_owners(),
        )
    )

    assert second_deployment.deployed is False
    assert second_deployment.already_deployed is True
    assert any(
        "already deployed" in warning for warning in second_deployment.warnings
    )
    assert any(
        "differs from the current example sources"
        in warning
        for warning in second_deployment.warnings
    )
    assert json.loads(model_json_path.read_text(encoding="utf-8")) == sentinel_payload


def test_align_shared_db_tenant_with_keycloak_organization_updates_example_rows(
    session: Session,
) -> None:
    tenant = M8flowTenantModel(
        id=example_poc.DEMO_TENANT["id"],
        name=example_poc.DEMO_TENANT["name"],
        slug=example_poc.DEMO_TENANT["slug"],
    )
    user = UserModel(
        username="poc-manager",
        email="poc-manager@example.com",
        service="http://localhost:6842/realms/m8flow",
        service_id="kc-manager",
        display_name="Manager",
        tenant_specific_field_1=tenant.id,
        tenant_specific_field_2=tenant.slug,
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="legacy-single",
        full_process_model_hash="legacy-full",
        bpmn_identifier="legacy-process",
        bpmn_name="Legacy Process",
        properties_json={"version": 1},
        created_at_in_seconds=10,
        updated_at_in_seconds=10,
    )
    session.add(tenant)
    session.flush()
    session.add_all([user, definition])
    session.flush()

    warnings: list[str] = []
    aligned_tenant = example_poc._align_shared_db_tenant_with_keycloak_organization(
        session,
        tenant=tenant,
        organization_id="org-demo",
        organization_name=example_poc.DEMO_TENANT["name"],
        warnings=warnings,
    )

    session.expire_all()
    stored_tenant = session.get(M8flowTenantModel, "org-demo")
    stored_definition = session.get(BpmnProcessDefinitionModel, definition.id)
    stored_user = session.query(UserModel).filter_by(username="poc-manager").one()

    assert aligned_tenant.id == "org-demo"
    assert stored_tenant is not None
    assert stored_tenant.slug == example_poc.DEMO_TENANT["slug"]
    assert session.get(M8flowTenantModel, example_poc.DEMO_TENANT["id"]) is None
    assert stored_definition is not None
    assert stored_definition.m8f_tenant_id == "org-demo"
    assert stored_user.tenant_specific_field_1 == "org-demo"
    assert stored_user.tenant_specific_field_2 == example_poc.DEMO_TENANT["slug"]
    assert warnings
    assert "realigned" in warnings[0]


def test_seed_demo_context_uses_keycloak_user_ids_for_shared_db(
    session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(example_poc, "_pause", lambda _prompt: None)
    monkeypatch.setattr(
        example_poc,
        "_provision_shared_db_keycloak_context",
        lambda *, tenant, other_tenants: ProvisionedKeycloakSharedRealmContext(
            shared_realm_name="m8flow",
            service_issuer="http://localhost:6842/realms/m8flow",
            organizations_by_alias={
                tenant.slug: ProvisionedKeycloakOrganization(
                    alias=tenant.slug,
                    name=tenant.name,
                    organization_id="org-demo",
                    created=True,
                ),
                other_tenants[0].slug: ProvisionedKeycloakOrganization(
                    alias=other_tenants[0].slug,
                    name=other_tenants[0].name,
                    organization_id="org-noise-a",
                    created=True,
                ),
                other_tenants[1].slug: ProvisionedKeycloakOrganization(
                    alias=other_tenants[1].slug,
                    name=other_tenants[1].name,
                    organization_id="org-noise-b",
                    created=True,
                ),
            },
            users_by_username={
                "poc-admin": ProvisionedKeycloakUser(
                    username="poc-admin",
                    email="poc-admin@example.com",
                    user_id="kc-admin",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Approvers", "Viewers"),
                    created=True,
                ),
                "poc-manager": ProvisionedKeycloakUser(
                    username="poc-manager",
                    email="poc-manager@example.com",
                    user_id="kc-manager",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Approvers", "Viewers"),
                    created=True,
                ),
                "poc-reviewer": ProvisionedKeycloakUser(
                    username="poc-reviewer",
                    email="poc-reviewer@example.com",
                    user_id="kc-reviewer",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Approvers", "Viewers"),
                    created=True,
                ),
                "poc-finance": ProvisionedKeycloakUser(
                    username="poc-finance",
                    email="poc-finance@example.com",
                    user_id="kc-finance",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Approvers", "Viewers"),
                    created=True,
                ),
                "poc-requester": ProvisionedKeycloakUser(
                    username="poc-requester",
                    email="poc-requester@example.com",
                    user_id="kc-requester",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Submitters", "Viewers"),
                    created=True,
                ),
                "poc-observer": ProvisionedKeycloakUser(
                    username="poc-observer",
                    email="poc-observer@example.com",
                    user_id="kc-observer",
                    organization_alias=tenant.slug,
                    organization_id="org-demo",
                    organization_group_names=("Viewers",),
                    created=True,
                ),
                "poc-foreign-noise": ProvisionedKeycloakUser(
                    username="poc-foreign-noise",
                    email="poc-foreign-noise@example.com",
                    user_id="kc-foreign-noise",
                    organization_alias=other_tenants[0].slug,
                    organization_id="org-noise-a",
                    organization_group_names=("Viewers",),
                    created=True,
                ),
            },
            warnings=(),
        ),
    )

    context = example_poc._seed_demo_context(
        session,
        database_url=example_poc.DEFAULT_LOCAL_DATABASE_URL,
    )

    requester = session.query(UserModel).filter_by(username="poc-requester").one()
    foreign_noise = (
        session.query(UserModel).filter_by(username="poc-foreign-noise").one()
    )

    assert context.tenant_id == "org-demo"
    assert requester.service == "http://localhost:6842/realms/m8flow"
    assert requester.service_id == "kc-requester"
    assert requester.tenant_specific_field_1 == "org-demo"
    assert requester.tenant_specific_field_2 == example_poc.DEMO_TENANT["slug"]
    assert foreign_noise.service == "http://localhost:6842/realms/m8flow"
    assert foreign_noise.service_id == "kc-foreign-noise"
    assert foreign_noise.tenant_specific_field_1 == "org-noise-a"
    assert foreign_noise.tenant_specific_field_2 == (
        example_poc.OTHER_DEMO_TENANTS[0]["slug"]
    )


def test_confirm_shared_database_usage_keeps_shared_database_when_confirmed(
    monkeypatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    selection = example_poc._confirm_shared_database_usage(
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
        "?connect_timeout=1",
        "postgresql+psycopg://postgres:***@localhost:6843/postgres"
        "?connect_timeout=1",
    )

    assert selection == (
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
        "?connect_timeout=1",
        "postgresql+psycopg://postgres:***@localhost:6843/postgres"
        "?connect_timeout=1",
        None,
    )


def test_confirm_shared_database_usage_uses_docker_fallback_when_declined(
    monkeypatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr(
        example_poc,
        "_start_temporary_postgres_container",
        lambda: (
            "postgresql+psycopg://postgres@127.0.0.1:55432/"
            "m8flow_bpmn_core_example",
            "postgresql+psycopg://postgres@127.0.0.1:55432/"
            "m8flow_bpmn_core_example",
            "temp-container-123",
        ),
    )

    selection = example_poc._confirm_shared_database_usage(
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
        "?connect_timeout=1",
        "postgresql+psycopg://postgres:***@localhost:6843/postgres"
        "?connect_timeout=1",
    )

    assert selection == (
        "postgresql+psycopg://postgres@127.0.0.1:55432/"
        "m8flow_bpmn_core_example",
        "postgresql+psycopg://postgres@127.0.0.1:55432/"
        "m8flow_bpmn_core_example",
        "temp-container-123",
    )


def test_confirm_shared_database_usage_skips_prompt_for_non_shared_database(
    monkeypatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "y",
    )

    selection = example_poc._confirm_shared_database_usage(
        "postgresql+psycopg://postgres@127.0.0.1:55432/m8flow_bpmn_core_example",
        "postgresql+psycopg://postgres@127.0.0.1:55432/m8flow_bpmn_core_example",
    )

    assert selection == (
        "postgresql+psycopg://postgres@127.0.0.1:55432/m8flow_bpmn_core_example",
        "postgresql+psycopg://postgres@127.0.0.1:55432/m8flow_bpmn_core_example",
        None,
    )
    assert prompts == []


def test_get_or_create_user_creates_principal_row_for_new_shared_db_user(
    session: Session,
) -> None:
    warnings: list[str] = []

    user = example_poc._get_or_create_user(
        session,
        service="http://localhost:6842/realms/m8flow",
        username="poc-manager",
        email="poc-manager@example.com",
        service_id="kc-manager",
        display_name="Manager",
        tenant_membership_identifiers=("org-demo", example_poc.DEMO_TENANT["slug"]),
        warnings=warnings,
        reuse_by_username_within_tenant=True,
    )

    principal_user_id = session.execute(
        text("select user_id from principal where user_id = :user_id"),
        {"user_id": user.id},
    ).scalar_one()

    assert principal_user_id == user.id
    assert any(
        "Created missing principal row for user 'poc-manager'" in warning
        for warning in warnings
    )


def test_get_or_create_user_backfills_principal_for_reused_shared_db_user(
    session: Session,
) -> None:
    existing_user = UserModel(
        username="poc-manager",
        email="poc-manager@example.com",
        service="http://localhost:7002/realms/conditional-approval-example",
        service_id="legacy-manager-id",
        display_name="Manager",
        tenant_specific_field_1="org-demo",
        tenant_specific_field_2=example_poc.DEMO_TENANT["slug"],
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add(existing_user)
    session.flush()

    warnings: list[str] = []
    user = example_poc._get_or_create_user(
        session,
        service="http://localhost:6842/realms/m8flow",
        username="poc-manager",
        email="poc-manager@example.com",
        service_id="kc-manager",
        display_name="Manager",
        tenant_membership_identifiers=("org-demo", example_poc.DEMO_TENANT["slug"]),
        warnings=warnings,
        reuse_by_username_within_tenant=True,
    )

    principal_user_id = session.execute(
        text("select user_id from principal where user_id = :user_id"),
        {"user_id": user.id},
    ).scalar_one()

    assert user.id == existing_user.id
    assert user.service == "http://localhost:6842/realms/m8flow"
    assert user.service_id == "kc-manager"
    assert principal_user_id == user.id
    assert any(
        "different service identity" in warning for warning in warnings
    )
    assert any(
        "Created missing principal row for user 'poc-manager'" in warning
        for warning in warnings
    )


def test_get_or_create_user_reuses_same_realm_username_even_without_tenant_fields(
    session: Session,
) -> None:
    existing_user = UserModel(
        username="poc-manager",
        email="poc-manager@example.com",
        service="http://localhost:6842/realms/m8flow",
        service_id="legacy-manager-id",
        display_name="Manager",
        tenant_specific_field_1=None,
        tenant_specific_field_2=None,
        tenant_specific_field_3=None,
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add(existing_user)
    session.flush()

    warnings: list[str] = []
    user = example_poc._get_or_create_user(
        session,
        service="http://localhost:6842/realms/m8flow",
        username="poc-manager",
        email="poc-manager@example.com",
        service_id="kc-manager",
        display_name="Manager",
        tenant_membership_identifiers=("org-demo", example_poc.DEMO_TENANT["slug"]),
        warnings=warnings,
        reuse_by_username_within_tenant=True,
    )

    principal_user_id = session.execute(
        text("select user_id from principal where user_id = :user_id"),
        {"user_id": user.id},
    ).scalar_one()

    assert user.id == existing_user.id
    assert user.service == "http://localhost:6842/realms/m8flow"
    assert user.service_id == "kc-manager"
    assert user.tenant_specific_field_1 == "org-demo"
    assert user.tenant_specific_field_2 == example_poc.DEMO_TENANT["slug"]
    assert principal_user_id == user.id
    assert any(
        "already exists for service 'http://localhost:6842/realms/m8flow' "
        "with a different service identity" in warning
        for warning in warnings
    )
    assert any(
        "Created missing principal row for user 'poc-manager'" in warning
        for warning in warnings
    )


def test_extract_process_models_mount_source_prefers_backend_env_target() -> None:
    inspect_payload = [
        {
            "Config": {
                "Env": [
                    "OTHER_ENV=1",
                    "M8FLOW_BACKEND_BPMN_SPEC_ABSOLUTE_DIR=/custom/process-models",
                ]
            },
            "Mounts": [
                {
                    "Destination": "/custom/process-models",
                    "Source": "C:\\dev\\repos\\m8flow\\data\\process_models",
                }
            ],
        }
    ]

    assert example_poc._extract_process_models_mount_source(inspect_payload) == (
        "C:\\dev\\repos\\m8flow\\data\\process_models"
    )


def test_extract_process_models_mount_source_normalizes_docker_desktop_host_mnt(
) -> None:
    inspect_payload = [
        {
            "Config": {
                "Env": [
                    "M8FLOW_BACKEND_BPMN_SPEC_ABSOLUTE_DIR=/app/process-models",
                ]
            },
            "Mounts": [
                {
                    "Destination": "/app/process-models",
                    "Source": "\\host_mnt\\c\\dev\\repos\\m8flow\\data\\process_models",
                }
            ],
        }
    ]

    assert example_poc._extract_process_models_mount_source(inspect_payload) == (
        "C:\\dev\\repos\\m8flow\\data\\process_models"
    )


def test_normalize_docker_desktop_mount_source_handles_slash_form() -> None:
    assert example_poc._normalize_docker_desktop_mount_source(
        "/host_mnt/c/dev/repos/m8flow/data/process_models"
    ) == "C:\\dev\\repos\\m8flow\\data\\process_models"


def test_realign_existing_example_process_model_identifiers_updates_rows(
    session: Session,
) -> None:
    tenant = M8flowTenantModel(
        id=example_poc.DEMO_TENANT["id"],
        name=example_poc.DEMO_TENANT["name"],
        slug=example_poc.DEMO_TENANT["slug"],
    )
    user = UserModel(
        username="poc-requester",
        email="poc-requester@example.com",
        service="http://localhost:7002/realms/conditional-approval-example",
        service_id="poc-requester-keycloak",
        display_name="Requester",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    session.add_all([tenant, user])
    session.flush()

    definition = BpmnProcessDefinitionModel(
        m8f_tenant_id=tenant.id,
        single_process_hash="legacy-single",
        full_process_model_hash="legacy-full",
        bpmn_identifier=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_ID,
        bpmn_name=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_DISPLAY_NAME,
        properties_json={"version": 1},
        created_at_in_seconds=10,
        updated_at_in_seconds=10,
    )
    session.add(definition)
    session.flush()

    process_instance = ProcessInstanceModel(
        m8f_tenant_id=tenant.id,
        process_model_identifier=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_ID,
        process_model_display_name=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_DISPLAY_NAME,
        process_initiator_id=user.id,
        bpmn_process_definition_id=definition.id,
        bpmn_process_id=None,
        status="running",
        created_at_in_seconds=20,
        updated_at_in_seconds=20,
    )
    session.add(process_instance)
    session.flush()

    bpmn_process = BpmnProcessModel(
        m8f_tenant_id=tenant.id,
        guid="legacy-process-guid",
        bpmn_process_definition_id=definition.id,
        top_level_process_id=None,
        direct_parent_process_id=None,
        properties_json={"legacy": True},
        json_data_hash="legacy-process-json",
    )
    session.add(bpmn_process)
    session.flush()

    process_instance.bpmn_process_id = bpmn_process.id

    task_definition = TaskDefinitionModel(
        m8f_tenant_id=tenant.id,
        bpmn_process_definition_id=definition.id,
        bpmn_identifier="legacy_task",
        bpmn_name="Legacy Task",
        typename="UserTask",
        properties_json={"legacy": True},
        created_at_in_seconds=25,
        updated_at_in_seconds=25,
    )
    session.add(task_definition)
    session.flush()

    task = TaskModel(
        m8f_tenant_id=tenant.id,
        guid="legacy-task-guid",
        bpmn_process_id=bpmn_process.id,
        process_instance_id=process_instance.id,
        task_definition_id=task_definition.id,
        state="READY",
        properties_json={"legacy": True},
        json_data_hash="legacy-task-json",
        python_env_data_hash="legacy-task-env",
    )
    session.add(task)
    session.flush()

    human_task = HumanTaskModel(
        m8f_tenant_id=tenant.id,
        process_instance_id=process_instance.id,
        task_guid=task.guid,
        task_name="legacy_task",
        task_title="Legacy Task",
        task_type="UserTask",
        task_status="READY",
        process_model_display_name=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_DISPLAY_NAME,
        bpmn_process_identifier=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_ID,
        lane_name="Manager",
        json_metadata={"legacy": True},
        completed=False,
    )
    session.add(human_task)
    session.add(
        ProcessModelBpmnVersionModel(
            m8f_tenant_id=tenant.id,
            process_model_identifier=example_poc.M8FLOW_BACKEND_PROCESS_MODEL_ID,
            bpmn_xml_hash="legacy-bpmn-hash",
            bpmn_xml_file_contents="<xml />",
            created_at_in_seconds=30,
        )
    )
    session.flush()

    warnings: list[str] = []
    example_poc._realign_existing_example_process_model_identifiers(
        session,
        tenant_id=tenant.id,
        warnings=warnings,
    )

    session.refresh(definition)
    session.refresh(process_instance)
    session.refresh(human_task)
    snapshots = session.query(ProcessModelBpmnVersionModel).all()

    assert definition.bpmn_identifier == example_poc.CONDITIONAL_APPROVAL_PROCESS_ID
    assert (
        definition.process_model_identifier
        == example_poc.CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER
    )
    assert process_instance.process_model_identifier == (
        example_poc.CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER
    )
    assert human_task.bpmn_process_identifier == (
        example_poc.CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER
    )
    assert [snapshot.process_model_identifier for snapshot in snapshots] == [
        example_poc.CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER
    ]
    assert warnings
    assert (
        example_poc.CONDITIONAL_APPROVAL_PROCESS_MODEL_IDENTIFIER in warnings[0]
    )
