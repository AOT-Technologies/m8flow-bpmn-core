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
        "Task_decide_finance_threshold",
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
        "Flow_submit_to_decision",
        "Flow_decision_to_gateway",
        "Flow_gateway_to_finance",
        "Flow_gateway_to_review",
        "Flow_finance_to_decision",
        "Flow_finance_approved_to_review",
        "Flow_finance_rejected_to_prepare_email",
        "Flow_review_to_prepare_email",
        "Flow_prepare_email_to_send_email",
        "Flow_send_email_to_end",
    }.issubset(edge_ids)


def test_sample_app_demo_dmn_defines_finance_review_threshold() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "sample_app"
        / "fixtures"
        / "sample_app_demo.dmn"
    )
    root = ET.fromstring(fixture_path.read_text(encoding="utf-8"))

    namespaces = {
        "dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/",
    }

    decision = root.find(
        ".//dmn:decision[@id='demo_finance_review_threshold']",
        namespaces,
    )
    assert decision is not None

    decision_table = decision.find("dmn:decisionTable", namespaces)
    assert decision_table is not None

    input_expression = decision_table.find(
        "dmn:input/dmn:inputExpression/dmn:text",
        namespaces,
    )
    assert input_expression is not None
    assert "".join(input_expression.itertext()).strip() == "amount"

    output = decision_table.find("dmn:output", namespaces)
    assert output is not None
    assert output.get("name") == "requires_finance_review"
    assert output.get("typeRef") == "boolean"

    rules = decision_table.findall("dmn:rule", namespaces)
    assert len(rules) == 2
    first_rule_input = rules[0].find("dmn:inputEntry/dmn:text", namespaces)
    first_rule_output = rules[0].find("dmn:outputEntry/dmn:text", namespaces)
    second_rule_input = rules[1].find("dmn:inputEntry/dmn:text", namespaces)
    second_rule_output = rules[1].find("dmn:outputEntry/dmn:text", namespaces)
    assert first_rule_input is not None
    assert first_rule_output is not None
    assert second_rule_input is not None
    assert second_rule_output is not None
    assert "".join(first_rule_input.itertext()).strip() == "<= 1000"
    assert "".join(first_rule_output.itertext()).strip() == "False"
    assert "".join(second_rule_input.itertext()).strip() == "> 1000"
    assert "".join(second_rule_output.itertext()).strip() == "True"


def test_sample_app_demo_bpmn_uses_dmn_for_finance_threshold() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "sample_app"
        / "fixtures"
        / "sample_app_demo.bpmn"
    )
    root = ET.fromstring(fixture_path.read_text(encoding="utf-8"))

    namespaces = {
        "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
        "spiffworkflow": "http://spiffworkflow.org/bpmn/schema/1.0/core",
    }

    called_decision = root.find(
        ".//bpmn:businessRuleTask[@id='Task_decide_finance_threshold']"
        "/bpmn:extensionElements/spiffworkflow:calledDecisionId",
        namespaces,
    )
    assert called_decision is not None
    assert "".join(called_decision.itertext()).strip() == (
        "demo_finance_review_threshold"
    )

    finance_condition = root.find(
        ".//bpmn:sequenceFlow[@id='Flow_gateway_to_finance']"
        "/bpmn:conditionExpression",
        namespaces,
    )
    review_condition = root.find(
        ".//bpmn:sequenceFlow[@id='Flow_gateway_to_review']"
        "/bpmn:conditionExpression",
        namespaces,
    )
    assert finance_condition is not None
    assert review_condition is not None
    assert "".join(finance_condition.itertext()).strip() == (
        "requires_finance_review == True"
    )
    assert "".join(review_condition.itertext()).strip() == (
        "requires_finance_review == False"
    )


def test_sample_app_demo_bpmn_uses_m8flow_secret_references_for_smtp() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "sample_app"
        / "fixtures"
        / "sample_app_demo.bpmn"
    )
    root = ET.fromstring(fixture_path.read_text(encoding="utf-8"))

    namespaces = {
        "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
        "spiffworkflow": "http://spiffworkflow.org/bpmn/schema/1.0/core",
    }

    smtp_parameters = {
        element.get("id"): element.get("value")
        for element in root.findall(
            ".//bpmn:serviceTask[@id='Task_send_email']"
            "/bpmn:extensionElements/spiffworkflow:serviceTaskOperator"
            "/spiffworkflow:parameters/spiffworkflow:parameter",
            namespaces,
        )
    }

    assert smtp_parameters["smtp_host"] == "'M8FLOW_SECRET:SMTP_HOST'"
    assert smtp_parameters["smtp_port"] == "'M8FLOW_SECRET:SMTP_PORT'"
    assert smtp_parameters["smtp_user"] == "'M8FLOW_SECRET:SMTP_USER'"
    assert smtp_parameters["smtp_password"] == "'M8FLOW_SECRET:SMTP_PASSWORD'"
    assert smtp_parameters["smtp_starttls"] == "'M8FLOW_SECRET:SMTP_STARTTLS'"
    assert smtp_parameters["email_from"] == "'M8FLOW_SECRET:SMTP_FROM_EMAIL'"

    smtp_parameter_types = {
        element.get("id"): element.get("type")
        for element in root.findall(
            ".//bpmn:serviceTask[@id='Task_send_email']"
            "/bpmn:extensionElements/spiffworkflow:serviceTaskOperator"
            "/spiffworkflow:parameters/spiffworkflow:parameter",
            namespaces,
        )
    }
    assert smtp_parameter_types["smtp_port"] == "int"
    assert smtp_parameter_types["smtp_starttls"] == "bool"
