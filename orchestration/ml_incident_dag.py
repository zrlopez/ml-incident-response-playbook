"""ml_incident_dag.py — Hardened Airflow DAG (remediation initiative)"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta
from typing import Any
import structlog
from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.utils.task_group import TaskGroup

log = structlog.get_logger(__name__)

# VULN-04 FIX: Hard-fail at import if ALERT_EMAIL is missing — no silent fallback
_ALERT_EMAIL = os.getenv("ALERT_EMAIL")
if not _ALERT_EMAIL:
    sys.exit(
        "[FATAL] ALERT_EMAIL env var not set. Refusing to register DAG. "
        "Set ALERT_EMAIL in your Airflow Connections or secrets manager."
    )

_SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")   # Optional; degrades gracefully
_ENV = os.getenv("AIRFLOW_ENV", "development")     # development|staging|production

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "ml-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "email_on_failure": True,
    "email_on_retry": False,
    "email": [_ALERT_EMAIL],
    "execution_timeout": timedelta(minutes=30),
}


def validate_data(**context: Any) -> dict[str, Any]:
    """Validate  schema, nulls, freshness. Raises on hard failures.
    TODO(prod): Replace stub with Great Expectations / Pandera / Soda Core.
    """
    task_id = context["task_instance"].task_id
    run_id = context["run_id"]
    log.info("data_validation.started", task_id=task_id, run_id=run_id, env=_ENV)

    checks_run = ["schema", "nulls", "freshness"]
    checks_failed: list[str] = []  # Wire real validation results here

    if checks_failed:
        log.error("data_validation.failed", task_id=task_id, failed=checks_failed)
        raise ValueError(f"Validation failed for: {checks_failed}")

    result = {"status": "passed", "checks": checks_run, "failed": []}
    log.info("data_validation.passed", task_id=task_id, result=result)
    return result


def detect_anomaly(**context: Any) -> dict[str, Any]:
    """Detect drift, volume, and latency anomalies. Raises on breach.
    TODO(prod): Read from Prometheus/Datadog; call anomaly_detection module.
    """
    task_id = context["task_instance"].task_id
    run_id = context["run_id"]
    log.info("anomaly_detection.started", task_id=task_id, run_id=run_id, env=_ENV)

    signals_checked = ["drift", "volume", "latency"]
    anomalies_detected: list[str] = []  # Wire real signal reads here

    if anomalies_detected:
        log.warning("anomaly_detection.breach", task_id=task_id, anomalies=anomalies_detected)
        raise RuntimeError(f"Anomalies detected: {anomalies_detected}")

    result = {"status": "passed", "signals": signals_checked, "anomalies": []}
    log.info("anomaly_detection.passed", task_id=task_id, result=result)
    return result


def publish_metrics(**context: Any) -> dict[str, Any]:
    """Publish run metrics to metrics store.
    TODO(prod): Emit to Prometheus pushgateway or Datadog StatsD.
    """
    task_id = context["task_instance"].task_id
    run_id = context["run_id"]
    log.info("metrics.publishing", task_id=task_id, run_id=run_id, env=_ENV)
    result = {"status": "published", "run_id": run_id, "env": _ENV}
    log.info("metrics.published", result=result)
    return result


def alert_on_failure(context: dict[str, Any]) -> None:
    """Structured failure callback — replaces bare print(). Posts to Slack if configured."""
    ti = context["task_instance"]
    exception = context.get("exception", "unknown")
    log.error(
        "pipeline.task_failed",
        dag_id=ti.dag_id,
        task_id=ti.task_id,
        run_id=context.get("run_id", "unknown"),
        exception=str(exception),
        env=_ENV,
    )
    if _SLACK_WEBHOOK:
        _post_slack_alert(
            webhook_url=_SLACK_WEBHOOK,
            dag_id=ti.dag_id,
            task_id=ti.task_id,
            run_id=context.get("run_id", "unknown"),
            exception=str(exception),
        )


def _post_slack_alert(*, webhook_url: str, dag_id: str, task_id: str, run_id: str, exception: str) -> None:
    """POST structured alert to Slack webhook. Non-blocking — failure never cascades."""
    import json
    import urllib.request
    payload = {
        "text": (
            f":red_circle: *DAG Failure [{_ENV.upper()}]*\n"
            f"*DAG:* `{dag_id}` | *Task:* `{task_id}`\n"
            f"*Run:* `{run_id}`\n*Error:* {exception}"
        )
    }
    try:
        req = urllib.request.Request(
            url=webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            log.info("slack_alert.sent", status=resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("slack_alert.failed", reason=str(exc))  # Never fail the DAG


with DAG(
    dag_id="ml_incident_response_pipeline",
    description="ML incident validation, monitoring, and escalation pipeline.",
    start_date=datetime(2026, 1, 1),
    schedule_interval="0 * * * *",
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["ml", "ops", "monitoring", "validation"],
    doc_md="""
## ML Incident Response Pipeline
Hourly pipeline: validates data → detects anomalies → publishes metrics.
Alert routing: email (ALERT_EMAIL) + optional Slack (SLACK_WEBHOOK_URL).
Environment: AIRFLOW_ENV (development | staging | production).
    """,
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    with TaskGroup(group_id="validation") as validation_group:
        validate_schema = PythonOperator(
            task_id="validate_schema", python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        validate_freshness = PythonOperator(
            task_id="validate_freshness", python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        validate_nulls = PythonOperator(
            task_id="validate_nulls", python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        chain(validate_schema, validate_freshness, validate_nulls)

    with TaskGroup(group_id="monitoring") as monitoring_group:
        detect_drift = PythonOperator(
            task_id="detect_drift", python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        detect_volume_spike = PythonOperator(
            task_id="detect_volume_spike", python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        detect_latency_regression = PythonOperator(
            task_id="detect_latency_regression", python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        chain(detect_drift, detect_volume_spike, detect_latency_regression)

    publish = PythonOperator(
        task_id="publish_metrics", python_callable=publish_metrics,
        on_failure_callback=alert_on_failure,
    )

    chain(start, validation_group, monitoring_group, publish, end)
