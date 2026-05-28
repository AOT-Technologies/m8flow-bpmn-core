"""Verify that ``import_bpmn_process_definition`` rejects bad BPMN at import.

Before this validation existed, an unparseable or empty BPMN file would land
in the database and surface as a workflow-runtime error on first execution.
The contract now fails fast with ``api.ValidationError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.tenant import M8flowTenantModel

VALID_BPMN_PATH = (
    Path(__file__).with_name("fixtures") / "invoice_approval_poc.bpmn"
)
TENANT_ID = "tenant-bpmn-validation"
TENANT_SLUG = "bpmn-validation"


def _make_tenant(session: Session) -> M8flowTenantModel:
    tenant = M8flowTenantModel(id=TENANT_ID, name="BPMN Validation", slug=TENANT_SLUG)
    session.add(tenant)
    session.flush()
    return tenant


def test_import_accepts_well_formed_bpmn(session: Session) -> None:
    _make_tenant(session)
    bpmn_xml = VALID_BPMN_PATH.read_text(encoding="utf-8")

    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=TENANT_ID,
            bpmn_identifier="invoice-approval",
            source_bpmn_xml=bpmn_xml,
        ),
    )

    assert definition.id is not None
    assert definition.source_bpmn_xml == bpmn_xml


def test_import_rejects_malformed_xml(session: Session) -> None:
    _make_tenant(session)

    with pytest.raises(api.ValidationError, match="not parseable"):
        api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_identifier="bad-xml",
                source_bpmn_xml="<this is not valid xml",
            ),
        )


def test_import_rejects_xml_with_no_executable_process(session: Session) -> None:
    _make_tenant(session)
    # Well-formed BPMN file but with no <bpmn:process> defined.
    empty_bpmn = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<bpmn:definitions '
        'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
        'targetNamespace="http://example.com/bpmn" />'
    )

    with pytest.raises(api.ValidationError):
        api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_identifier="empty-bpmn",
                source_bpmn_xml=empty_bpmn,
            ),
        )


def test_import_rejects_malformed_dmn(session: Session) -> None:
    _make_tenant(session)
    bpmn_xml = VALID_BPMN_PATH.read_text(encoding="utf-8")

    with pytest.raises(api.ValidationError, match="not parseable"):
        api.execute_command(
            session,
            api.ImportBpmnProcessDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_identifier="invoice-approval-bad-dmn",
                source_bpmn_xml=bpmn_xml,
                source_dmn_xml="<bad dmn",
            ),
        )
