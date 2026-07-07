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
        "Lane_review",
        "StartEvent_1",
        "Task_prepare",
        "Task_review",
        "EndEvent_1",
    }.issubset(shape_ids)
    assert {"Flow_1", "Flow_2", "Flow_3"}.issubset(edge_ids)
