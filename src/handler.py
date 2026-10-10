"""AWS Cost Anomaly Detection Lambda Handler.

Monitors daily AWS spend against a 14-day rolling baseline across services.
Detects cost spikes exceeding a configurable threshold and triggers actionable
Slack Block Kit notifications with service breakdowns and cost-saving tips.
Supports DEMO_MODE=true with realistic synthetic spend data for testing.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

try:
    from src.notifier import send_cost_alert
except ImportError:
    from notifier import send_cost_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("cloud-cost-guardian")

# Configuration thresholds
SPIKE_THRESHOLD = float(os.getenv("SPIKE_THRESHOLD", "0.20"))  # 20% over 14-day baseline
MIN_SPIKE_AMOUNT_USD = float(os.getenv("MIN_SPIKE_AMOUNT_USD", "10.0"))  # Ignore spikes below $10
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
CURRENCY = os.getenv("CURRENCY", "USD")


def get_cost_explorer_client():
    """Initializes AWS Cost Explorer client."""
    return boto3.client("ce")


def generate_synthetic_cost_data() -> Dict[str, Any]:
    """Generates realistic synthetic 14-day spend data with an injected anomaly for demo/testing."""
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    start_date = yesterday - timedelta(days=LOOKBACK_DAYS)

    services = [
        ("Amazon Elastic Compute Cloud - Compute", 120.0, 15.0),
        ("Amazon Relational Database Service", 85.0, 5.0),
        ("Amazon Simple Storage Service", 25.0, 2.0),
        ("Amazon Elastic Kubernetes Service", 45.0, 4.0),
        ("AmazonCloudWatch", 18.0, 2.0),
        ("AWS Lambda", 12.0, 1.5),
        ("AWS Key Management Service", 5.0, 0.5),
    ]

    results_by_time = []
    current_day = start_date

    while current_day <= yesterday:
        day_str = current_day.strftime("%Y-%m-%d")
        next_day_str = (current_day + timedelta(days=1)).strftime("%Y-%m-%d")
        is_anomaly_day = current_day == yesterday

        groups = []
        for service_name, base_cost, variance in services:
            cost = base_cost + (variance * ((hash(f"{service_name}-{day_str}") % 11) - 5) / 5.0)
            
            # Inject anomaly on yesterday for EC2 (e.g. unattached instances / rogue GPU workload)
            if is_anomaly_day and service_name == "Amazon Elastic Compute Cloud - Compute":
                cost = 245.80  # ~105% surge over baseline!
            elif is_anomaly_day and service_name == "Amazon Simple Storage Service":
                cost = 48.50   # ~94% surge due to bucket replication

            groups.append({
                "Keys": [service_name],
                "Metrics": {
                    "UnblendedCost": {
                        "Amount": f"{cost:.2f}",
                        "Unit": "USD"
                    }
                }
            })

        results_by_time.append({
            "TimePeriod": {"Start": day_str, "End": next_day_str},
            "Total": {},
            "Groups": groups,
            "Estimated": False
        })
        current_day += timedelta(days=1)

    return {"ResultsByTime": results_by_time}


def fetch_aws_cost_data(ce_client, start_date: str, end_date: str) -> Dict[str, Any]:
    """Queries AWS Cost Explorer API for unblended costs grouped by service."""
    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date, "End": end_date},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        return response
    except (BotoCoreError, ClientError) as err:
        logger.error("Failed to query AWS Cost Explorer: %s", err)
        raise


def analyze_costs(
    cost_data: Dict[str, Any],
    spike_threshold: float = SPIKE_THRESHOLD,
    min_spike_amount: float = MIN_SPIKE_AMOUNT_USD,
) -> Dict[str, Any]:
    """Analyzes daily spend against baseline to detect service-level anomalies.

    Args:
        cost_data: Cost Explorer ResultsByTime payload
        spike_threshold: Fractional increase to flag (e.g. 0.20 = 20%)
        min_spike_amount: Minimum dollar surge required to alert

    Returns:
        Structured anomaly report summary.
    """
    results = cost_data.get("ResultsByTime", [])
    if not results or len(results) < 2:
        logger.warning("Insufficient cost data points to compute baseline.")
        return {
            "has_anomalies": False,
            "total_yesterday": 0.0,
            "total_baseline": 0.0,
            "anomalies": [],
            "service_breakdown": {},
        }

    latest_day_data = results[-1]
    history_days = results[:-1]

    target_date = latest_day_data["TimePeriod"]["Start"]
    baseline_days_count = len(history_days)

    # Calculate baseline average per service over history
    service_history: Dict[str, List[float]] = {}
    for day in history_days:
        for group in day.get("Groups", []):
            svc = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            service_history.setdefault(svc, []).append(amount)

    service_baseline: Dict[str, float] = {
        svc: sum(amounts) / max(len(amounts), 1)
        for svc, amounts in service_history.items()
    }

    # Extract latest day spend
    latest_service_spend: Dict[str, float] = {}
    total_yesterday = 0.0
    for group in latest_day_data.get("Groups", []):
        svc = group["Keys"][0]
        amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
        latest_service_spend[svc] = amount
        total_yesterday += amount

    total_baseline = sum(service_baseline.values())
    anomalies: List[Dict[str, Any]] = []

    for svc, current_cost in latest_service_spend.items():
        baseline_cost = service_baseline.get(svc, 0.0)
        dollar_diff = current_cost - baseline_cost

        # Calculate percentage spike
        if baseline_cost > 0:
            pct_diff = dollar_diff / baseline_cost
        else:
            pct_diff = 1.0 if current_cost > min_spike_amount else 0.0

        if pct_diff >= spike_threshold and dollar_diff >= min_spike_amount:
            # Generate contextual remediation hint
            hint = get_service_remediation_hint(svc)
            anomalies.append({
                "service": svc,
                "current_cost": round(current_cost, 2),
                "baseline_cost": round(baseline_cost, 2),
                "delta_amount": round(dollar_diff, 2),
                "percent_increase": round(pct_diff * 100, 1),
                "recommendation": hint,
            })

    # Sort anomalies by dollar delta descending
    anomalies.sort(key=lambda x: x["delta_amount"], reverse=True)

    has_anomalies = len(anomalies) > 0
    overall_spike_pct = ((total_yesterday - total_baseline) / total_baseline * 100) if total_baseline > 0 else 0.0

    return {
        "date": target_date,
        "baseline_days": baseline_days_count,
        "has_anomalies": has_anomalies,
        "total_yesterday": round(total_yesterday, 2),
        "total_baseline": round(total_baseline, 2),
        "overall_delta": round(total_yesterday - total_baseline, 2),
        "overall_spike_percent": round(overall_spike_pct, 1),
        "anomalies": anomalies,
        "service_breakdown": {
            svc: round(cost, 2)
            for svc, cost in sorted(latest_service_spend.items(), key=lambda x: x[1], reverse=True)[:5]
        },
    }


def get_service_remediation_hint(service_name: str) -> str:
    """Returns domain-specific FinOps diagnostic recommendations."""
    lower_svc = service_name.lower()
    if "compute" in lower_svc or "ec2" in lower_svc:
        return "Check for newly launched unreserved on-demand instances, oversized GPU instances, or unattached EBS volumes."
    elif "relational" in lower_svc or "rds" in lower_svc:
        return "Inspect read replicas, multi-AZ provisioning, automated snapshot retention, and IOPS allocations."
    elif "storage" in lower_svc or "s3" in lower_svc:
        return "Review S3 cross-region replication, missing lifecycle expiration rules, and Athena/Glue scan queries."
    elif "kubernetes" in lower_svc or "eks" in lower_svc:
        return "Verify cluster Autoscaler / Karpenter scale-down events and node group instance types."
    elif "cloudwatch" in lower_svc:
        return "Audit high-cardinality custom metrics, unbounded log group retention, and verbose container log ingestion."
    elif "lambda" in lower_svc:
        return "Check for recursive invocations, long execution runtimes, or high provisioned concurrency."
    elif "nat" in lower_svc or "transfer" in lower_svc:
        return "Check inter-AZ cross-traffic and VPC NAT Gateway data processing spikes."
    return "Audit AWS CloudTrail events for recently created resources or configuration changes."


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entry point for daily cost anomaly detection."""
    is_demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true" or event.get("demo_mode") is True

    logger.info("Starting Cloud Cost Guardian run (DEMO_MODE=%s)", is_demo_mode)

    try:
        if is_demo_mode:
            logger.info("Executing in DEMO MODE with synthetic 14-day AWS spend data.")
            cost_data = generate_synthetic_cost_data()
        else:
            today = datetime.now(timezone.utc).date()
            yesterday = today - timedelta(days=1)
            start_date = (yesterday - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
            logger.info("Querying Cost Explorer from %s to %s", start_date, end_date)
            ce_client = get_cost_explorer_client()
            cost_data = fetch_aws_cost_data(ce_client, start_date, end_date)

        report = analyze_costs(cost_data)
        logger.info(
            "Analysis complete. Date: %s | Total Spend: $%s (Baseline: $%s) | Anomalies Detected: %d",
            report.get("date"),
            report.get("total_yesterday"),
            report.get("total_baseline"),
            len(report.get("anomalies", [])),
        )

        notification_result = {"delivered": False, "reason": "No anomalies"}
        if report["has_anomalies"]:
            slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")
            sns_topic_arn = os.getenv("SNS_TOPIC_ARN")
            notification_result = send_cost_alert(
                report=report,
                slack_webhook_url=slack_webhook_url,
                sns_topic_arn=sns_topic_arn,
            )

        return {
            "statusCode": 200,
            "status": "ok",
            "report": report,
            "notification": notification_result,
        }

    except Exception as err:
        logger.exception("Cost Guardian execution failed: %s", err)
        return {
            "statusCode": 500,
            "status": "error",
            "error": str(err),
        }


if __name__ == "__main__":
    # Local CLI execution
    os.environ["DEMO_MODE"] = "true"
    result = lambda_handler({"demo_mode": True}, None)
    print("\n--- Cost Guardian Local Execution Result ---")
    print(json.dumps(result, indent=2))
