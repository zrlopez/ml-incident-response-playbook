"""Minimal Airflow DAG example illustrating the ML incident monitoring pattern.

This file is a companion to `ml_incident_dag.py`. Where that file contains
the full production DAG with error handling, retries, and Prometheus callbacks,
this example strips everything down to the simplest possible shape so new
team members can understand the core pattern without noise.

Use this file when:
    - Onboarding a new engineer who is unfamiliar with Airflow.
    - Demoing the DAG concept without connecting to real infrastructure.
    - Testing Airflow locally with a minimal scheduler footprint.

Do NOT use this file in production. Use `ml_incident_dag.py` instead.

Dependencies:
    apache-airflow >= 2.7
    apache-airflow-providers-http (for the SimpleHttpOperator)

To run locally:
    airflow dags trigger ml_incident_monitoring_example
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.models.dag import DAG
from airflow.operators.python import PythonOperator

# ---------------------------------------------------------------------------
# Default arguments applied to every task in this DAG.
# In production these would include retries, email alerts, and SLA callbacks.
# ---------------------------------------------------------------------------
DEFAULT_ARGS = {
    "owner": "ml-platform",
    "depends_on_past": False,
    "start_date": datetime(2026, 1, 1),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


# ---------------------------------------------------------------------------
# Task functions
# These callables are intentionally simple so the DAG shape stays readable.
# The production equivalents live in ml_incident_dag.py.
# ---------------------------------------------------------------------------


def check_model_health(**context: dict) -> dict:
    """Check whether the primary ML model is serving within normal parameters.

    In production this task queries the Prometheus API for the
    `ml_feature_psi` and `ml_model_accuracy_delta` metrics and compares
    them against the thresholds defined in configs/settings.yml.

    For this example the function simply returns a healthy status dict that
    is passed to the next task via XCom.

    Returns:
        dict: Health status with a 'healthy' boolean and optional 'reason'.
    """
    print("[check_model_health] Querying model health metrics...")
    # --- Replace this block with a real Prometheus API call in production ---
    health_status = {"healthy": True, "reason": None}
    # ------------------------------------------------------------------------
    print(f"[check_model_health] Result: {health_status}")
    return health_status


def check_data_quality(**context: dict) -> dict:
    """Validate that the latest feature batch meets null-rate and PSI thresholds.

    In production this task calls the Great Expectations checkpoint runner
    against the feature store table for the current hour window.

    Returns:
        dict: Validation result with 'passed' boolean and 'failed_checks' list.
    """
    print("[check_data_quality] Running feature quality checks...")
    # --- Replace this block with a real Great Expectations call in production ---
    result = {"passed": True, "failed_checks": []}
    # -------------------------------------------------------------------------
    print(f"[check_data_quality] Result: {result}")
    return result


def open_incident_if_needed(**context: dict) -> None:
    """Open an incident in the tracker if either upstream check failed.

    Pulls results from both preceding tasks via XCom. If either task
    reports a failure, this function calls POST /incidents on the incident
    tracking API with the appropriate severity and category.

    In production the request includes JWT auth and structured payload
    validation via the schema in validation/schema_checks.py.
    """
    ti = context["ti"]
    model_health = ti.xcom_pull(task_ids="check_model_health")
    dq_result = ti.xcom_pull(task_ids="check_data_quality")

    if not model_health["healthy"]:
        print(f"[open_incident_if_needed] Model unhealthy: {model_health['reason']}")
        print("[open_incident_if_needed] Would POST to /incidents (model-drift, SEV-2)")
    elif not dq_result["passed"]:
        print(f"[open_incident_if_needed] Data quality failed: {dq_result['failed_checks']}")
        print("[open_incident_if_needed] Would POST to /incidents (data-quality, SEV-2)")
    else:
        print("[open_incident_if_needed] All checks passed. No incident opened.")


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="ml_incident_monitoring_example",
    description=(
        "Minimal example DAG showing the incident monitoring pattern. "
        "For production use, see ml_incident_dag.py."
    ),
    default_args=DEFAULT_ARGS,
    schedule="@hourly",
    catchup=False,
    tags=["ml-platform", "monitoring", "example"],
) as dag:

    # Task 1: Check model health via Prometheus metrics.
    t_model = PythonOperator(
        task_id="check_model_health",
        python_callable=check_model_health,
    )

    # Task 2: Validate the latest feature batch against quality thresholds.
    t_dq = PythonOperator(
        task_id="check_data_quality",
        python_callable=check_data_quality,
    )

    # Task 3: Open an incident in the tracker if either check failed.
    # Runs after both checks complete (successful or not).
    t_incident = PythonOperator(
        task_id="open_incident_if_needed",
        python_callable=open_incident_if_needed,
        trigger_rule="all_done",  # run even if upstream tasks fail
    )

    # Dependency graph: both checks run in parallel, then incident check.
    [t_model, t_dq] >> t_incident
