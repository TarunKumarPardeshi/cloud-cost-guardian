"""Unit and integration tests for Cloud Cost Guardian."""
import os
import pytest
from unittest.mock import MagicMock, patch

from src.handler import (
    analyze_costs,
    generate_synthetic_cost_data,
    get_service_remediation_hint,
    lambda_handler,
)
from src.notifier import build_slack_block_kit_payload, send_cost_alert


def test_generate_synthetic_cost_data():
    """Verify synthetic dataset format and anomaly injection."""
    data = generate_synthetic_cost_data()
    assert "ResultsByTime" in data
    assert len(data["ResultsByTime"]) == 15  # 14 baseline days + 1 target day
    
    # Check that EC2 on target day has the injected spike
    target_day = data["ResultsByTime"][-1]
    ec2_groups = [g for g in target_day["Groups"] if "Compute" in g["Keys"][0]]
    assert len(ec2_groups) == 1
    assert float(ec2_groups[0]["Metrics"]["UnblendedCost"]["Amount"]) > 200.0


def test_analyze_costs_detects_anomaly():
    """Verify statistical analysis correctly detects service cost anomalies."""
    synthetic_data = generate_synthetic_cost_data()
    report = analyze_costs(synthetic_data, spike_threshold=0.20, min_spike_amount=10.0)

    assert report["has_anomalies"] is True
    assert len(report["anomalies"]) >= 1
    
    top_anomaly = report["anomalies"][0]
    assert "Compute" in top_anomaly["service"]
    assert top_anomaly["delta_amount"] > 50.0
    assert top_anomaly["percent_increase"] > 50.0
    assert "EC2" in top_anomaly["recommendation"] or "instances" in top_anomaly["recommendation"]


def test_build_slack_block_kit_payload():
    """Verify Slack Block Kit blocks are correctly generated."""
    synthetic_data = generate_synthetic_cost_data()
    report = analyze_costs(synthetic_data)
    payload = build_slack_block_kit_payload(report)

    assert "text" in payload
    assert "blocks" in payload
    assert any(b.get("type") == "header" for b in payload["blocks"])
    assert any("Detected Service Anomalies" in str(b) for b in payload["blocks"])


def test_send_cost_alert_mocked():
    """Test webhook dispatching with mocked HTTP request."""
    synthetic_data = generate_synthetic_cost_data()
    report = analyze_costs(synthetic_data)

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = "ok"

        res = send_cost_alert(
            report=report,
            slack_webhook_url="https://hooks.slack.com/services/MOCK/WEBHOOK/123",
        )
        assert res["slack"] is True
        assert mock_post.called


def test_lambda_handler_demo_mode(monkeypatch):
    """Test full handler pipeline in DEMO_MODE."""
    monkeypatch.setenv("DEMO_MODE", "true")
    res = lambda_handler({}, None)
    assert res["statusCode"] == 200
    assert res["status"] == "ok"
    assert res["report"]["has_anomalies"] is True


def test_service_remediation_hints():
    """Ensure heuristic tips are tailored per AWS service."""
    assert "EBS" in get_service_remediation_hint("Amazon Elastic Compute Cloud - Compute")
    assert "lifecycle" in get_service_remediation_hint("Amazon Simple Storage Service")
    assert "snapshot" in get_service_remediation_hint("Amazon Relational Database Service")
