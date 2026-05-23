from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator
from airflow.utils.task_group import TaskGroup

DEFAULT_ARGS = {
    "owner": "ml-platform",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": True,
    "email": [os.getenv("ALERT_EMAIL", "ml-ops@example.com")],
}


def validate_data(**context):
    return {"status": "passed", "checks": ["schema", "nulls", "freshness"]}


def detect_anomaly(**context):
    return {"status": "passed", "signals": ["drift", "volume", "latency"]}


def publish_metrics(**context):
    return {"status": "published"}


def alert_on_failure(context):
    print(f"Alert triggered for task {context['task_instance'].task_id}")


with DAG(
    dag_id="ml_incident_response_pipeline",
    description="Operational DAG for ML incident validation, monitoring, and escalation.",
    start_date=datetime(2026, 1, 1),
    schedule_interval="0 * * * *",
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["ml", "ops", "monitoring", "validation"],
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    with TaskGroup(group_id="validation") as validation_group:
        validate_schema = PythonOperator(
            task_id="validate_schema",
            python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        validate_freshness = PythonOperator(
            task_id="validate_freshness",
            python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        validate_nulls = PythonOperator(
            task_id="validate_nulls",
            python_callable=validate_data,
            on_failure_callback=alert_on_failure,
        )
        chain(validate_schema, validate_freshness, validate_nulls)

    with TaskGroup(group_id="monitoring") as monitoring_group:
        detect_drift = PythonOperator(
            task_id="detect_drift",
            python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        detect_volume_spike = PythonOperator(
            task_id="detect_volume_spike",
            python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        detect_latency_regression = PythonOperator(
            task_id="detect_latency_regression",
            python_callable=detect_anomaly,
            on_failure_callback=alert_on_failure,
        )
        chain(detect_drift, detect_volume_spike, detect_latency_regression)

    publish = PythonOperator(
        task_id="publish_metrics",
        python_callable=publish_metrics,
        on_failure_callback=alert_on_failure,
    )

    chain(start, validation_group, monitoring_group, publish, end)
