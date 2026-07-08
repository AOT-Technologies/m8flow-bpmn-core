from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET


def test_sample_app_demo_bpmn_contains_diagram_metadata() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "sample_app"
        / "fixtures"
        / "sample_app_demo.bpmn"
    )
    root = ET.fromstring(fixture_path.read_text(encoding="utf-8"))

    namespaces = {
        "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
        "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    }

    assert root.find("bpmn:collaboration", namespaces) is not None
    assert root.find("bpmndi:BPMNDiagram", namespaces) is not None

    shape_ids = {
        element.attrib["bpmnElement"]
        for element in root.findall(".//bpmndi:BPMNShape", namespaces)
    }
    edge_ids = {
        element.attrib["bpmnElement"]
        for element in root.findall(".//bpmndi:BPMNEdge", namespaces)
    }

    assert {
        "Participant_sample_app_demo",
        "Lane_operations",
        "Lane_finance",
        "Lane_review",
        "Lane_system",
        "StartEvent_1",
        "Task_submit_request",
        "Gateway_amount_threshold",
        "Task_review",
        "Task_finance_review",
        "Gateway_finance_decision",
        "Task_prepare_email",
        "Task_send_email",
        "EndEvent_1",
    }.issubset(shape_ids)
    assert {
        "Flow_start_to_submit",
        "Flow_submit_to_gateway",
        "Flow_gateway_to_finance",
        "Flow_gateway_to_review",
        "Flow_finance_to_decision",
        "Flow_finance_approved_to_review",
        "Flow_finance_rejected_to_prepare_email",
        "Flow_review_to_prepare_email",
        "Flow_prepare_email_to_send_email",
        "Flow_send_email_to_end",
    }.issubset(edge_ids)
