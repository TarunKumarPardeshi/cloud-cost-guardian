"""Cost anomaly detection Lambda. DEMO_MODE=true runs on synthetic data."""
import os

SPIKE_THRESHOLD = 0.20  # 20% over 14-day baseline

def lambda_handler(event, context):
    # TODO: pull Cost Explorer data, detect anomalies, notify
    if os.getenv("DEMO_MODE") == "true":
        print("demo mode: using synthetic data")
    return {"status": "ok"}
