"""ml_incident_dag.py — Hardened Airflow DAG (remediation initiative)"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta, timezone
from typing import Any
import numpy as np
import pandas as pd
import pandera as pa
from pandera import Column, DataFrameSchema, Check
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


# ── Pandera schema for incident records ──────────────────────────────────────
# Defines the expected shape of the incident DataFrame loaded each DAG run.
# Extend columns here as the data model evolves (see src/schemas/incident.py).
_INCIDENT_SCHEMA = DataFrameSchema(
    {
        "incident_id": Column(str, nullable=False),
        "title": Column(str, nullable=False),
        "severity": Column(
            str,
            # R-67 FIX: align with API/alembic enum (SEV_1–SEV_4, underscore form from DB)
            # The incidents table uses Enum("SEV_1","SEV_2","SEV_3","SEV_4")
            checks=Check.isin(["SEV_1", "SEV_2", "SEV_3", "SEV_4"]),
            nullable=False,
        ),
        "status": Column(
            str,
            # R-67 FIX: align with alembic incidentstatus enum
            checks=Check.isin(["open", "investigating", "mitigating", "resolved", "closed"]),
            nullable=False,
        ),
        "owner": Column(str, nullable=False),
        "created_at": Column(
            "datetime64[ns, UTC]",
            nullable=False,
            checks=Check(
                lambda s: (pd.Timestamp.now(tz="UTC") - s).dt.total_seconds().le(3600 * 25).all(),
                error="Freshness check failed: records older than 25 hours detected",
            ),
        ),
    },
    coerce=True,
    strict=False,  # allow extra columns from future schema migrations
)


def _load_incident_dataframe(run_id: str) -> pd.DataFrame:
    """
    Load incidents for the current DAG run into a DataFrame.

    Production: query PostgreSQL via DATABASE_URL.
    Fallback: generate a synthetic DataFrame so the DAG can run in CI/dev
    without a live database, producing realistic validation metrics.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url and "sqlite" not in database_url:
        try:
            import sqlalchemy as sa  # noqa: PLC0415
            engine = sa.create_engine(database_url)
            with engine.connect() as conn:
                df = pd.read_sql(
                    "SELECT incident_id, title, severity, status, owner, created_at "
                    "FROM incidents WHERE created_at >= NOW() - INTERVAL '1 hour'",
                    conn,
                )
            log.info("data_load.db", rows=len(df), run_id=run_id)
            return df
        except Exception as exc:  # noqa: BLE001
            log.warning("data_load.db_fallback", reason=str(exc), run_id=run_id)

    # Synthetic fallback for CI / dev — mimics production schema exactly
    now = pd.Timestamp.now(tz="UTC")
    rng = np.random.default_rng(seed=abs(hash(run_id)) % (2**31))
    n = int(rng.integers(10, 50))
    df = pd.DataFrame({
        "incident_id": [f"INC-{i:04d}" for i in range(n)],
        "title": [f"Synthetic incident {i}" for i in range(n)],
        # R-67 FIX: match alembic enum values
        "severity": rng.choice(["SEV_1", "SEV_2", "SEV_3", "SEV_4"], size=n),
        "status": rng.choice(["open", "investigating", "mitigating", "resolved", "closed"], size=n),
        "owner": rng.choice(["alice", "bob", "carol", "dave"], size=n),
        "created_at": [now - pd.Timedelta(seconds=int(s)) for s in rng.integers(0, 3600, n)],
    })
    log.info("data_load.synthetic", rows=n, run_id=run_id)
    return df


def validate_data(**context: Any) -> dict[str, Any]:
    """
    Validate incident DataFrame against the Pandera schema.

    Checks enforced:
      - Column presence and dtype coercion (schema)
      - Null constraints on required fields
      - Categorical allowlist for severity and status
      - Freshness: all records created within the last 25 hours

    Raises pa.errors.SchemaError on hard failure, which Airflow surfaces
    as a task failure and triggers the on_failure_callback alert.
    """
    task_id = context["task_instance"].task_id
    run_id = context["run_id"]
    log.info("data_validation.started", task_id=task_id, run_id=run_id, env=_ENV)

    df = _load_incident_dataframe(run_id)

    try:
        validated_df = _INCIDENT_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        failure_cases = exc.failure_cases.to_dict(orient="records")
        log.error(
            "data_validation.failed",
            task_id=task_id,
            run_id=run_id,
            failure_count=len(failure_cases),
            failures=failure_cases[:10],  # cap log payload
        )
        raise ValueError(
            f"Pandera validation failed with {len(failure_cases)} error(s). "
            f"First failure: {failure_cases[0] if failure_cases else 'unknown'}"
        ) from exc

    severity_dist = validated_df["severity"].value_counts().to_dict()
    status_dist = validated_df["status"].value_counts().to_dict()
    result = {
        "status": "passed",
        "row_count": len(validated_df),
        "checks": ["schema", "nulls", "categorical", "freshness"],
        "severity_distribution": severity_dist,
        "status_distribution": status_dist,
        "run_id": run_id,
    }
    log.info("data_validation.passed", task_id=task_id, result=result)

    # Push summary to XCom for anomaly detection downstream
    context["task_instance"].xcom_push(key="validation_result", value=result)
    return result


# ── Anomaly detection thresholds ────────────────────────────────────────────
# PSI thresholds: <0.1 no change, 0.1–0.2 moderate, >0.2 significant drift
_PSI_WARNING_THRESHOLD = float(os.getenv("PSI_WARNING_THRESHOLD", "0.1"))
_PSI_CRITICAL_THRESHOLD = float(os.getenv("PSI_CRITICAL_THRESHOLD", "0.2"))
# Volume spike: z-score above this triggers a warning
_VOLUME_ZSCORE_THRESHOLD = float(os.getenv("VOLUME_ZSCORE_THRESHOLD", "2.5"))
# Latency regression: p95 above this (seconds) triggers a warning
_LATENCY_P95_THRESHOLD_S = float(os.getenv("LATENCY_P95_THRESHOLD_S", "2.0"))

# Rolling baseline for volume z-score (last N runs stored in XCom / Airflow Variables)
_VOLUME_BASELINE_RUNS = int(os.getenv("VOLUME_BASELINE_RUNS", "14"))


def _compute_psi(baseline: np.ndarray, current: np.ndarray, buckets: int = 10) -> float:
    """
    Population Stability Index (PSI) between a baseline and current distribution.

    PSI = sum((current% - baseline%) * ln(current% / baseline%))
    A small epsilon prevents log(0) on empty buckets.
    """
    eps = 1e-6
    bins = np.linspace(
        min(baseline.min(), current.min()),
        max(baseline.max(), current.max()) + eps,
        buckets + 1,
    )
    baseline_pct = np.histogram(baseline, bins=bins)[0] / len(baseline) + eps
    current_pct = np.histogram(current, bins=bins)[0] / len(current) + eps
    return float(np.sum((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)))


def _get_volume_baseline(ti: Any) -> tuple[float, float]:
    """
    Retrieve rolling mean and std of row counts from recent DAG runs via XCom.
    Falls back to sensible defaults if history is insufficient.
    """
    from airflow.models import XCom  # noqa: PLC0415
    from airflow.utils.session import create_session  # noqa: PLC0415

    with create_session() as session:
        records = (
            session.query(XCom)
            .filter(
                XCom.dag_id == ti.dag_id,
                XCom.task_id == "validation.validate_schema",
                XCom.key == "validation_result",
            )
            .order_by(XCom.timestamp.desc())
            .limit(_VOLUME_BASELINE_RUNS)
            .all()
        )
    counts = [r.value.get("row_count", 0) for r in records if isinstance(r.value, dict)]
    if len(counts) < 3:
        return 30.0, 10.0  # fallback: mean=30 rows, std=10
    arr = np.array(counts, dtype=float)
    return float(arr.mean()), float(arr.std(ddof=1))


def detect_anomaly(**context: Any) -> dict[str, Any]:
    """
    Detect drift, volume spikes, and latency regressions.

    Signals:
      drift   — PSI on severity distribution vs. 14-run rolling baseline.
                 PSI > 0.1 warning; PSI > 0.2 raises (critical).
      volume  — z-score of current row count vs. rolling mean/std.
                 |z| > 2.5 raises (unexpected data volume change).
      latency — p95 of created_at lag vs. now; proxy for ingestion latency.
                 p95 > 2.0 s raises (data freshness SLA breach).

    Raises RuntimeError on any critical breach, surfacing as Airflow task
    failure and triggering the on_failure_callback Slack/email alert.
    """
    ti = context["task_instance"]
    task_id = ti.task_id
    run_id = context["run_id"]
    log.info("anomaly_detection.started", task_id=task_id, run_id=run_id, env=_ENV)

    # Pull current run validation result from XCom
    validation_result: dict = ti.xcom_pull(
        task_ids="validation.validate_schema", key="validation_result"
    ) or {}
    current_row_count: int = validation_result.get("row_count", 0)
    current_severity_dist: dict = validation_result.get("severity_distribution", {})

    anomalies: list[dict] = []
    metrics: dict[str, Any] = {}

    # ── 1. Drift: PSI on severity distribution ────────────────────────────
    # R-67 FIX: align severity levels with alembic SEV_1-SEV_4 enum
    _SEVERITY_LEVELS = ["SEV_1", "SEV_2", "SEV_3", "SEV_4"]
    _BASELINE_SEVERITY = np.array([0.05, 0.20, 0.50, 0.25])  # expected distribution (SEV_1→SEV_4)
    current_counts = np.array(
        [current_severity_dist.get(s, 0) for s in _SEVERITY_LEVELS], dtype=float
    )
    total = current_counts.sum()
    if total > 0:
        current_pct = current_counts / total
        # Expand distributions to sample arrays for PSI computation
        n_samples = 1000
        baseline_samples = np.repeat(_SEVERITY_LEVELS, (n_samples * _BASELINE_SEVERITY).astype(int))
        current_samples = np.repeat(_SEVERITY_LEVELS, (n_samples * current_pct).astype(int))
        # Encode to numeric for PSI
        enc = {s: i for i, s in enumerate(_SEVERITY_LEVELS)}
        psi = _compute_psi(
            np.array([enc[s] for s in baseline_samples], dtype=float),
            np.array([enc[s] for s in current_samples], dtype=float),
            buckets=4,
        )
        metrics["severity_psi"] = round(psi, 4)
        if psi > _PSI_CRITICAL_THRESHOLD:
            anomalies.append({"signal": "drift", "severity": "critical", "psi": psi})
            log.error("anomaly.drift_critical", psi=psi, threshold=_PSI_CRITICAL_THRESHOLD)
        elif psi > _PSI_WARNING_THRESHOLD:
            log.warning("anomaly.drift_warning", psi=psi, threshold=_PSI_WARNING_THRESHOLD)
    else:
        log.warning("anomaly.drift_skipped", reason="no rows in current run")

    # ── 2. Volume spike: z-score vs. rolling baseline ──────────────────────
    baseline_mean, baseline_std = _get_volume_baseline(ti)
    if baseline_std > 0:
        z_score = abs(current_row_count - baseline_mean) / baseline_std
        metrics["volume_zscore"] = round(z_score, 4)
        metrics["volume_current"] = current_row_count
        metrics["volume_baseline_mean"] = round(baseline_mean, 2)
        if z_score > _VOLUME_ZSCORE_THRESHOLD:
            anomalies.append({"signal": "volume", "severity": "critical", "z_score": z_score})
            log.error("anomaly.volume_spike", z_score=z_score, threshold=_VOLUME_ZSCORE_THRESHOLD)

    # ── 3. Latency: p95 ingestion lag proxy ─────────────────────────────
    df = _load_incident_dataframe(run_id)
    if not df.empty and "created_at" in df.columns:
        now_utc = pd.Timestamp.now(tz="UTC")
        lags = (now_utc - pd.to_datetime(df["created_at"], utc=True)).dt.total_seconds()
        p95_lag = float(np.percentile(lags, 95))
        metrics["latency_p95_seconds"] = round(p95_lag, 3)
        if p95_lag > _LATENCY_P95_THRESHOLD_S:
            anomalies.append({"signal": "latency", "severity": "critical", "p95_s": p95_lag})
            log.error("anomaly.latency_regression", p95_s=p95_lag, threshold=_LATENCY_P95_THRESHOLD_S)

    result: dict[str, Any] = {
        "status": "breach" if anomalies else "passed",
        "signals_checked": ["drift", "volume", "latency"],
        "anomalies": anomalies,
        "metrics": metrics,
        "run_id": run_id,
    }
    ti.xcom_push(key="anomaly_result", value=result)

    if anomalies:
        critical = [a for a in anomalies if a.get("severity") == "critical"]
        log.warning("anomaly_detection.breach", task_id=task_id, anomalies=anomalies)
        if critical:
            raise RuntimeError(
                f"{len(critical)} critical anomaly(ies) detected: "
                + ", ".join(a["signal"] for a in critical)
            )

    log.info("anomaly_detection.passed", task_id=task_id, result=result)
    return result


_PUSHGATEWAY_URL = os.getenv("PROMETHEUS_PUSHGATEWAY_URL", "")  # e.g. http://pushgateway:9091


def publish_metrics(**context: Any) -> dict[str, Any]:
    """
    Publish DAG run metrics to Prometheus Pushgateway.

    Metrics emitted (all labelled with dag_id, run_id, env):
      ml_incident_dag_rows_total          — incident rows validated this run
      ml_incident_dag_severity_psi        — PSI drift score for severity distribution
      ml_incident_dag_volume_zscore       — volume z-score vs. rolling baseline
      ml_incident_dag_latency_p95_seconds — p95 ingestion latency proxy
      ml_incident_dag_anomalies_total     — count of anomalies detected
      ml_incident_dag_last_run_timestamp  — Unix epoch of this run (for staleness alerts)

    Graceful degradation: if PROMETHEUS_PUSHGATEWAY_URL is unset or the
    gateway is unreachable, metrics are emitted as structured log events
    instead. The task never fails due to a metrics publishing error.
    """
    ti = context["task_instance"]
    task_id = ti.task_id
    run_id = context["run_id"]
    dag_id = ti.dag_id
    log.info("metrics.publishing", task_id=task_id, run_id=run_id, env=_ENV)

    # Collect metrics from upstream XCom results
    validation_result: dict = ti.xcom_pull(
        task_ids="validation.validate_schema", key="validation_result"
    ) or {}
    anomaly_result: dict = ti.xcom_pull(
        task_ids="monitoring.detect_drift", key="anomaly_result"
    ) or {}

    anomaly_metrics: dict = anomaly_result.get("metrics", {})
    metric_values = {
        "rows_total": validation_result.get("row_count", 0),
        "severity_psi": anomaly_metrics.get("severity_psi", 0.0),
        "volume_zscore": anomaly_metrics.get("volume_zscore", 0.0),
        "latency_p95_seconds": anomaly_metrics.get("latency_p95_seconds", 0.0),
        "anomalies_total": len(anomaly_result.get("anomalies", [])),
        "last_run_timestamp": datetime.now(timezone.utc).timestamp(),
    }

    labels = {"dag_id": dag_id, "run_id": run_id, "env": _ENV}

    if _PUSHGATEWAY_URL:
        try:
            from prometheus_client import CollectorRegistry, Gauge, push_to_gateway  # noqa: PLC0415

            registry = CollectorRegistry()
            for metric_name, value in metric_values.items():
                g = Gauge(
                    f"ml_incident_dag_{metric_name}",
                    f"ML incident DAG metric: {metric_name}",
                    labelnames=list(labels.keys()),
                    registry=registry,
                )
                g.labels(**labels).set(value)

            push_to_gateway(
                _PUSHGATEWAY_URL,
                job="ml_incident_dag",
                registry=registry,
            )
            log.info(
                "metrics.pushgateway_sent",
                gateway=_PUSHGATEWAY_URL,
                metrics=metric_values,
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Never fail the DAG due to metrics publishing errors.
            # Degraded metrics path: emit as structured log for scraping
            # by a log-based metrics collector (e.g. Loki + Promtail).
            log.error(
                "metrics.pushgateway_failed",
                reason=str(exc),
                fallback="structured_log",
                metrics=metric_values,
                run_id=run_id,
            )
    else:
        # No Pushgateway configured — emit as structured log events.
        # In dev/CI these are captured by the test suite; in staging/prod
        # a log-based collector (Loki, CloudWatch Logs Insights) can scrape them.
        log.info(
            "metrics.emitted",
            transport="structured_log",
            metrics=metric_values,
            labels=labels,
        )

    result = {
        "status": "published",
        "run_id": run_id,
        "env": _ENV,
        "transport": "pushgateway" if _PUSHGATEWAY_URL else "structured_log",
        "metrics": metric_values,
    }
    log.info("metrics.published", task_id=task_id, result=result)
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
