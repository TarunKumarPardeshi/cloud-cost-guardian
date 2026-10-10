"""Slack Block Kit and SNS Notification Engine for Cost Alerts.

Builds structured Slack Block Kit messages with visual severity indicators,
cost breakdown metrics, anomaly delta badges, and action buttons.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
import requests

logger = logging.getLogger("cloud-cost-guardian.notifier")


def build_slack_block_kit_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    """Generates Slack Block Kit payload from an anomaly report."""
    target_date = report.get("date", "Yesterday")
    total_yesterday = report.get("total_yesterday", 0.0)
    total_baseline = report.get("total_baseline", 0.0)
    overall_delta = report.get("overall_delta", 0.0)
    overall_spike_percent = report.get("overall_spike_percent", 0.0)
    anomalies = report.get("anomalies", [])

    delta_prefix = "+" if overall_delta >= 0 else ""
    pct_prefix = "+" if overall_spike_percent >= 0 else ""

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🚨 AWS Cost Spike Alert | Cloud Cost Guardian",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Target Date:* `{target_date}`\n"
                    f"*Spend:* `${total_yesterday:,.2f}` | "
                    f"*14-Day Baseline:* `${total_baseline:,.2f}` | "
                    f"*Variance:* `{delta_prefix}${overall_delta:,.2f} ({pct_prefix}{overall_spike_percent}%)`"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⚠️ Detected Service Anomalies ({len(anomalies)} services exceeding threshold):*",
            },
        },
    ]

    for anomaly in anomalies:
        service_name = anomaly.get("service", "Unknown Service")
        current_cost = anomaly.get("current_cost", 0.0)
        baseline_cost = anomaly.get("baseline_cost", 0.0)
        delta_amount = anomaly.get("delta_amount", 0.0)
        percent_increase = anomaly.get("percent_increase", 0.0)
        rec = anomaly.get("recommendation", "Review resource utilization in AWS console.")

        blocks.append({
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Service:*\n`{service_name}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Daily Spend:*\n`${current_cost:,.2f}` _(Baseline: ${baseline_cost:,.2f})_",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Spike:* `+{percent_increase}% (+${delta_amount:,.2f})`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Action Hint:*\n_{rec}_",
                },
            ],
        })

    # Top 5 Spend Breakdown
    top_services = report.get("service_breakdown", {})
    if top_services:
        breakdown_text = " • ".join([f"*{k}*: `${v:,.2f}`" for k, v in top_services.items()])
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"📊 *Top Daily Spend Drivers:* {breakdown_text}",
                }
            ],
        })

    # Action buttons
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📊 Open AWS Cost Explorer", "emoji": True},
                "url": "https://console.aws.amazon.com/costmanagement/home#/cost-explorer",
                "style": "primary",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🛡️ View CloudTrail Logs", "emoji": True},
                "url": "https://console.aws.amazon.com/cloudtrail/home#/events",
            },
        ],
    })

    return {
        "text": f"🚨 AWS Cost Spike Alert: Spend was ${total_yesterday:,.2f} on {target_date} ({pct_prefix}{overall_spike_percent}% vs baseline).",
        "blocks": blocks,
    }


def send_cost_alert(
    report: Dict[str, Any],
    slack_webhook_url: Optional[str] = None,
    sns_topic_arn: Optional[str] = None,
) -> Dict[str, Any]:
    """Sends structured alerts via Slack Incoming Webhook and/or AWS SNS Topic."""
    results: Dict[str, Any] = {"slack": False, "sns": False}
    payload = build_slack_block_kit_payload(report)

    # 1. Slack Webhook Dispatch
    if slack_webhook_url:
        try:
            resp = requests.post(
                slack_webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Successfully posted alert to Slack.")
                results["slack"] = True
            else:
                logger.error("Slack webhook returned status %d: %s", resp.status_code, resp.text)
        except Exception as err:
            logger.error("Error dispatching Slack webhook: %s", err)
    else:
        logger.info("No SLACK_WEBHOOK_URL specified; printing Slack payload to stdout.")
        logger.debug(json.dumps(payload, indent=2))
        results["slack_skipped"] = True

    # 2. SNS Topic Dispatch
    if sns_topic_arn:
        try:
            sns_client = boto3.client("sns")
            subject = f"🚨 AWS Cost Anomaly Alert - {report.get('date', '')} Spike Detected"
            message_body = (
                f"{payload['text']}\n\n"
                f"Anomalies Breakdown:\n"
                + "\n".join(
                    [
                        f" - {a['service']}: ${a['current_cost']} (Baseline: ${a['baseline_cost']}, +{a['percent_increase']}%)"
                        for a in report.get("anomalies", [])
                    ]
                )
            )
            sns_client.publish(
                TopicArn=sns_topic_arn,
                Subject=subject[:100],
                Message=message_body,
            )
            logger.info("Published alert to SNS Topic: %s", sns_topic_arn)
            results["sns"] = True
        except Exception as err:
            logger.error("Failed to publish to SNS: %s", err)

    return results
