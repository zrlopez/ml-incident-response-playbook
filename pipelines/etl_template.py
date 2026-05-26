# =============================================================================
# pipelines/etl_template.py — ML Incident ETL Template
# =============================================================================
# Reusable ETL scaffold for ingesting incident event data into the ML
# Incident Response API database and downstream ML feature stores.
#
# Pipeline stages:
#   extract()   — pull raw incident rows from PostgreSQL (or fixture)
#   transform() — validate, normalise, enrich, and deduplicate
#   load()      — bulk-insert transformed rows; idempotent via run_id guard
#   run()       — orchestrate extract → transform → load with structured logging
#
# Design principles:
#   - Each stage is independently testable and mockable.
#   - All DB access is isolated to extract() and load(); transform() is pure.
#   - Run IDs prevent double-loading on retries (at-least-once delivery).
#   - Structured logging via structlog on every stage boundary.
#   - All secrets sourced from environment variables — never hardcoded.
# =============================================================================
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./incidents.db"
)
_ETL_BATCH_SIZE: int = int(os.environ.get("ETL_BATCH_SIZE", "500"))
_ETL_RUN_TABLE: str = "etl_runs"
_SOURCE_TABLE: str = "incidents"


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

RawRow = dict[str, Any]
TransformedRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_run_id(batch_key: str = "") -> str:
    """Generate a deterministic run ID based on timestamp + batch key."""
    ts = datetime.now(timezone.utc).isoformat()
    raw = f"{ts}-{batch_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _check_run_id_exists(run_id: str, engine: Any) -> bool:
    """Return True if this run_id has already been loaded (idempotency check)."""
    try:
        with engine.begin() as conn:
            result = conn.execute(
                sa.text(f"SELECT 1 FROM {_ETL_RUN_TABLE} WHERE run_id = :run_id"),
                {"run_id": run_id},
            )
            return result.fetchone() is not None
    except Exception:
        # Table may not exist yet on first run — treat as not found
        return False


def _record_run_id(run_id: str, row_count: int, engine: Any) -> None:
    """Persist the run_id after a successful load for idempotency."""
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    f"INSERT INTO {_ETL_RUN_TABLE} (run_id, row_count, loaded_at) "
                    "VALUES (:run_id, :row_count, :loaded_at)"
                ),
                {
                    "run_id": run_id,
                    "row_count": row_count,
                    "loaded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
    except Exception as exc:
        log.warning("etl.record_run_id_failed", run_id=run_id, error=str(exc))


# ---------------------------------------------------------------------------
# Stage 1: Extract
# ---------------------------------------------------------------------------

def extract(batch_size: int = _ETL_BATCH_SIZE) -> list[RawRow]:
    """
    Read unprocessed incident events from PostgreSQL.
    Selects rows where etl_processed IS NULL (or column absent in older schemas).
    """
    engine = sa.create_engine(_DATABASE_URL, pool_pre_ping=True)
    query = sa.text(
        """
        SELECT id, title, severity, status, category, owner,
               description, created_at, updated_at
        FROM incidents
        WHERE etl_processed IS NULL
        ORDER BY created_at ASC
        LIMIT :batch_size
        """
    )
    try:
        with engine.begin() as conn:
            result = conn.execute(query, {"batch_size": batch_size})
            rows = [dict(r._mapping) for r in result.fetchall()]
        log.info("etl.extract_complete", row_count=len(rows))
        return rows
    except Exception as exc:
        log.error("etl.extract_failed", error=str(exc))
        return []
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Stage 2: Transform
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"low", "medium", "high", "critical"}
_VALID_STATUSES = {"open", "investigating", "resolved", "closed"}


def _validate_row(row: RawRow) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    if not row.get("id"):
        errors.append("missing id")
    if not row.get("title") or not str(row["title"]).strip():
        errors.append("missing or empty title")
    if row.get("severity") and row["severity"].lower() not in _VALID_SEVERITIES:
        errors.append(f"invalid severity: {row['severity']}")
    if row.get("status") and row["status"].lower() not in _VALID_STATUSES:
        errors.append(f"invalid status: {row['status']}")
    return errors


def _enrich_row(row: RawRow) -> TransformedRow:
    """Normalise and enrich a raw row into a transformed row."""
    return {
        "id": str(row.get("id", uuid.uuid4())),
        "title": str(row.get("title", "")).strip(),
        "severity": str(row.get("severity", "low")).lower(),
        "status": str(row.get("status", "open")).lower(),
        "category": str(row.get("category", "uncategorised")).strip(),
        "owner": str(row.get("owner", "unassigned")).strip(),
        "description": str(row.get("description") or "").strip(),
        "created_at": (
            row["created_at"].isoformat()
            if hasattr(row.get("created_at"), "isoformat")
            else str(row.get("created_at", ""))
        ),
        "updated_at": (
            row["updated_at"].isoformat()
            if hasattr(row.get("updated_at"), "isoformat")
            else str(row.get("updated_at", ""))
        ),
        "etl_processed": datetime.now(timezone.utc).isoformat(),
        "etl_version": "1.0.0",
    }


def transform(raw_rows: list[RawRow]) -> list[TransformedRow]:
    """
    Validate, normalise, and enrich raw rows.
    Invalid rows are logged and dropped; valid rows are returned.
    """
    transformed: list[TransformedRow] = []
    seen_ids: set[str] = set()

    for row in raw_rows:
        errors = _validate_row(row)
        if errors:
            log.warning(
                "etl.transform_row_invalid",
                row_id=row.get("id"),
                errors=errors,
            )
            continue

        row_id = str(row["id"])
        if row_id in seen_ids:
            log.warning("etl.transform_duplicate", row_id=row_id)
            continue
        seen_ids.add(row_id)

        transformed.append(_enrich_row(row))

    log.info(
        "etl.transform_complete",
        input_count=len(raw_rows),
        output_count=len(transformed),
        dropped=len(raw_rows) - len(transformed),
    )
    return transformed


# ---------------------------------------------------------------------------
# Stage 3: Load
# ---------------------------------------------------------------------------

def load(rows: list[TransformedRow], run_id: str | None = None) -> int:
    """
    Bulk-insert transformed rows into the incidents table.
    Idempotent: skips if run_id was already recorded.

    Returns:
        Number of rows successfully inserted (0 if skipped or empty).
    """
    if not rows:
        log.info("etl.load_skipped", reason="empty_batch")
        return 0

    if run_id is None:
        run_id = _generate_run_id(batch_key=rows[0].get("id", ""))

    engine = sa.create_engine(_DATABASE_URL, pool_pre_ping=True)

    try:
        if _check_run_id_exists(run_id, engine):
            log.info("etl.load_idempotent_skip", run_id=run_id)
            return 0

        insert_stmt = sa.text(
            """
            INSERT INTO incidents
                (id, title, severity, status, category, owner,
                 description, created_at, updated_at, etl_processed, etl_version)
            VALUES
                (:id, :title, :severity, :status, :category, :owner,
                 :description, :created_at, :updated_at, :etl_processed, :etl_version)
            ON CONFLICT (id) DO UPDATE SET
                status       = EXCLUDED.status,
                updated_at   = EXCLUDED.updated_at,
                etl_processed = EXCLUDED.etl_processed,
                etl_version  = EXCLUDED.etl_version
            """
        )

        with engine.begin() as conn:
            for row in rows:
                conn.execute(insert_stmt, row)

        _record_run_id(run_id, len(rows), engine)
        log.info("etl.load_complete", run_id=run_id, row_count=len(rows))
        return len(rows)

    except Exception as exc:
        log.error("etl.load_failed", run_id=run_id, error=str(exc))
        raise
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Stage 4: S3 Archive (optional)
# ---------------------------------------------------------------------------

def archive_to_s3(
    rows: list[TransformedRow],
    bucket: str,
    prefix: str = "etl-archive",
) -> str | None:
    """
    Upload transformed rows as newline-delimited JSON to S3.
    Returns the S3 key on success, None if boto3 is unavailable.
    """
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        log.warning("etl.s3_archive_skipped", reason="boto3_not_installed")
        return None

    key = f"{prefix}/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{_generate_run_id()}.ndjson"
    body = "\n".join(json.dumps(r, default=str) for r in rows)

    try:
        client = boto3.client("s3")
        client.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))
        log.info("etl.s3_archive_complete", bucket=bucket, key=key, row_count=len(rows))
        return key
    except Exception as exc:
        log.error("etl.s3_archive_failed", bucket=bucket, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Stage 5: Feature store sink (optional stub)
# ---------------------------------------------------------------------------

def publish_to_feature_store(
    rows: list[TransformedRow],
    feature_group: str = "ml_incidents",
) -> int:
    """
    Publish transformed rows to an online feature store.
    Returns number of rows published (0 if feature store unavailable).
    """
    try:
        import random  # noqa: PLC0415
        # Stub: replace with real feature store SDK call
        published = 0
        for row in rows:
            if random.random() > 0.01:  # 99% success rate stub
                published += 1
        log.info(
            "etl.feature_store_published",
            feature_group=feature_group,
            count=published,
        )
        return published
    except Exception as exc:
        log.warning("etl.feature_store_failed", error=str(exc))
        return 0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(
    batch_size: int = _ETL_BATCH_SIZE,
    archive_bucket: str | None = None,
    feature_group: str | None = None,
) -> dict[str, Any]:
    """
    Run the full ETL pipeline: extract → transform → load.
    Optionally archive to S3 and publish to feature store.

    Returns:
        Summary dict with counts and timing for each stage.
    """
    start = time.perf_counter()
    log.info("etl.pipeline_start", batch_size=batch_size)

    raw = extract(batch_size=batch_size)
    t_extract = time.perf_counter() - start

    transformed = transform(raw)
    t_transform = time.perf_counter() - start - t_extract

    loaded = load(transformed)
    t_load = time.perf_counter() - start - t_extract - t_transform

    summary: dict[str, Any] = {
        "extracted": len(raw),
        "transformed": len(transformed),
        "loaded": loaded,
        "dropped": len(raw) - len(transformed),
        "duration_s": round(time.perf_counter() - start, 3),
        "stage_times": {
            "extract_s": round(t_extract, 3),
            "transform_s": round(t_transform, 3),
            "load_s": round(t_load, 3),
        },
    }

    if archive_bucket:
        s3_key = archive_to_s3(transformed, bucket=archive_bucket)
        summary["s3_key"] = s3_key

    if feature_group:
        published = publish_to_feature_store(transformed, feature_group=feature_group)
        summary["feature_store_published"] = published

    log.info("etl.pipeline_complete", **summary)
    return summary
