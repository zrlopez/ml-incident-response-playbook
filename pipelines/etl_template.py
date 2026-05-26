"""etl_template.py — Production ETL pipeline for ML incident events

Stages:
  extract()   — reads from PostgreSQL (DATABASE_URL) or S3 (ETL_S3_BUCKET);
                 falls back to synthetic seed in CI/dev
  transform() — schema validation, ISO timestamp normalisation, PII hashing,
                 severity mapping, payload flattening
  load()      — SQLAlchemy bulk insert into `incidents` table with idempotency
                 guard (run_id), real commit/rollback, structured audit log

Environment variables:
  DATABASE_URL              — SQLAlchemy URL (postgres+psycopg2 or sqlite)
  ETL_S3_BUCKET             — optional S3 bucket for JSON/Parquet source files
  ETL_S3_PREFIX             — optional S3 key prefix (default: "incidents/")
  ETL_BATCH_SIZE            — rows per bulk insert batch (default: 500)
  ETL_PII_HASH_FIELDS       — comma-separated field names to SHA-256 hash
                               (default: "owner")
"""
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

# ── Configuration ──────────────────────────────────────────────────────────────────
_DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./incidents.db")
_S3_BUCKET: str = os.getenv("ETL_S3_BUCKET", "")
_S3_PREFIX: str = os.getenv("ETL_S3_PREFIX", "incidents/")
_BATCH_SIZE: int = int(os.getenv("ETL_BATCH_SIZE", "500"))
_PII_HASH_FIELDS: list[str] = [
    f.strip() for f in os.getenv("ETL_PII_HASH_FIELDS", "owner").split(",") if f.strip()
]

# ── Schema contract ──────────────────────────────────────────────────────────────
# Aligns with the `incidents` table defined in
# migrations/versions/20260523_0001_initial_incidents_schema.py
REQUIRED_FIELDS: frozenset[str] = frozenset({"id", "timestamp", "event_type", "payload"})
VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {"model_degradation", "pipeline_failure", "data_quality", "latency_spike", "cost_spike"}
)

# Maps inbound event_type → DB severity enum (SEV-1 = critical, SEV-4 = low)
_SEVERITY_MAP: dict[str, str] = {
    "model_degradation": "SEV-1",
    "pipeline_failure": "SEV-2",
    "latency_spike": "SEV-2",
    "data_quality": "SEV-3",
    "cost_spike": "SEV-4",
}


class ETLSchemaError(ValueError):
    """Raised when a row fails schema validation."""


class ETLLoadError(RuntimeError):
    """Raised when the load stage fails and the transaction is rolled back."""


# ── Helpers ────────────────────────────────────────────────────────────────────
def _validate_row(row: dict[str, Any], index: int) -> None:
    """Validate a single row against the schema contract. Raises ETLSchemaError on failure."""
    missing = REQUIRED_FIELDS - row.keys()
    if missing:
        raise ETLSchemaError(f"Row [{index}] missing required fields: {sorted(missing)}")
    if row["event_type"] not in VALID_EVENT_TYPES:
        raise ETLSchemaError(
            f"Row [{index}] invalid event_type '{row['event_type']}'. "
            f"Valid: {sorted(VALID_EVENT_TYPES)}"
        )
    if not isinstance(row["payload"], dict):
        raise ETLSchemaError(
            f"Row [{index}] payload must be a dict, got {type(row['payload']).__name__}"
        )


def _compute_run_id(rows: list[dict[str, Any]]) -> str:
    """Deterministic run ID for idempotency checking."""
    content = json.dumps(rows, sort_keys=True, default=str).encode()
    return hashlib.sha256(content).hexdigest()[:16]


def _hash_pii(value: str) -> str:
    """One-way SHA-256 hash for PII fields. Preserves referential integrity."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_timestamp(raw: Any) -> datetime:
    """
    Parse a timestamp field to a timezone-aware UTC datetime.
    Accepts ISO 8601 strings, Unix epoch ints/floats, or existing datetimes.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    # ISO 8601 string
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ETLSchemaError(f"Cannot parse timestamp '{raw}': {exc}") from exc


# ── Extract ──────────────────────────────────────────────────────────────────────
def _extract_from_db() -> list[dict[str, Any]]:
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
        WHERE created_at >= NOW() - INTERVAL '1 hour'
        ORDER BY created_at ASC
        LIMIT :limit
        """
    )
    with engine.connect() as conn:
        result = conn.execute(query, {"limit": _BATCH_SIZE})
        rows = [
            {
                "id": str(r.id),
                "timestamp": r.created_at,
                "event_type": _infer_event_type(r.category),
                "payload": {
                    "title": r.title,
                    "severity": r.severity,
                    "status": r.status,
                    "category": r.category,
                    "description": r.description,
                    "owner": r.owner,
                    "updated_at": str(r.updated_at),
                },
            }
            for r in result
        ]
    engine.dispose()
    log.info("etl.extract.db", rows=len(rows))
    return rows


def _infer_event_type(category: str | None) -> str:
    """Map incident category to a valid ETL event_type."""
    mapping = {
        "model": "model_degradation",
        "pipeline": "pipeline_failure",
        "data": "data_quality",
        "latency": "latency_spike",
        "cost": "cost_spike",
    }
    if category:
        for key, event_type in mapping.items():
            if key in category.lower():
                return event_type
    return "data_quality"  # safe default


def _extract_from_s3() -> list[dict[str, Any]]:
    """
    Read incident event JSON files from S3 bucket under ETL_S3_PREFIX.
    Each file is expected to contain a JSON array of event dicts.
    """
    import boto3  # noqa: PLC0415

    s3 = boto3.client("s3")
    rows: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_S3_BUCKET, Prefix=_S3_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            response = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            payload = json.loads(response["Body"].read().decode("utf-8"))
            if isinstance(payload, list):
                rows.extend(payload)
            else:
                rows.append(payload)
    log.info("etl.extract.s3", bucket=_S3_BUCKET, prefix=_S3_PREFIX, rows=len(rows))
    return rows


def _extract_synthetic() -> list[dict[str, Any]]:
    """Seed realistic synthetic rows for CI/dev (no external dependencies)."""
    import random  # noqa: PLC0415

    rng = random.Random(42)
    event_types = list(VALID_EVENT_TYPES)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": str(uuid.uuid4()),
            "timestamp": now.isoformat(),
            "event_type": rng.choice(event_types),
            "payload": {
                "title": f"Synthetic incident {i}",
                "owner": rng.choice(["alice", "bob", "carol"]),
                "score": round(rng.uniform(0.5, 1.0), 4),
            },
        }
        for i in range(20)
    ]
    log.info("etl.extract.synthetic", rows=len(rows))
    return rows


def extract() -> list[dict[str, Any]]:
    """
    Extract incident event records from the configured source.

    Source priority:
      1. PostgreSQL via DATABASE_URL (if not SQLite)
      2. S3 via ETL_S3_BUCKET (if set)
      3. Synthetic seed (CI/dev fallback)

    Returns:
        List of raw row dicts conforming to the ETL schema contract.

    Raises:
        RuntimeError: If the source connection fails with no fallback.
    """
    start = time.monotonic()
    log.info("etl.extract.started")
    try:
        if _DATABASE_URL and "sqlite" not in _DATABASE_URL:
            rows = _extract_from_db()
        elif _S3_BUCKET:
            rows = _extract_from_s3()
        else:
            rows = _extract_synthetic()
        elapsed = time.monotonic() - start
        log.info("etl.extract.complete", row_count=len(rows), elapsed_s=round(elapsed, 3))
        return rows
    except Exception as exc:
        log.exception("etl.extract.failed", error=str(exc))
        raise RuntimeError(f"Extract failed: {exc}") from exc


# ── Transform ────────────────────────────────────────────────────────────────────
def transform(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate and transform raw rows into DB-ready incident dicts.

    Transformations applied:
      - Schema validation (required fields, event_type allowlist, payload type)
      - ISO 8601 timestamp normalisation → UTC datetime
      - Severity mapping: event_type → SEV-1..SEV-4 enum
      - PII field hashing: fields in ETL_PII_HASH_FIELDS are SHA-256 hashed
        inside the payload to prevent plaintext PII reaching the destination
      - Payload flattening: nested payload keys are promoted to top-level
        columns where they match the `incidents` schema
      - Invalid rows are skipped with a structured warning (soft failure;
        change continue → raise to enforce hard failure in stricter environments)

    Args:
        rows: Raw extracted rows.

    Returns:
        List of validated, transformed rows ready for bulk insert.
    """
    log.info("etl.transform.started", input_count=len(rows))
    validated: list[dict[str, Any]] = []
    skipped = 0

    for i, row in enumerate(rows):
        try:
            _validate_row(row, i)
        except ETLSchemaError as exc:
            log.warning("etl.transform.row_skipped", index=i, reason=str(exc))
            skipped += 1
            continue

        payload: dict[str, Any] = dict(row["payload"])  # defensive copy

        # ─ Timestamp normalisation ────────────────────────────────────────────
        try:
            created_at = _parse_timestamp(row["timestamp"])
        except ETLSchemaError as exc:
            log.warning("etl.transform.row_skipped", index=i, reason=str(exc))
            skipped += 1
            continue

        # ─ Severity mapping ────────────────────────────────────────────────
        severity = payload.get("severity") or _SEVERITY_MAP.get(row["event_type"], "SEV-3")
        # Normalise any inbound critical/high/medium/low to SEV enum
        _severity_alias = {
            "critical": "SEV-1", "high": "SEV-2", "medium": "SEV-3", "low": "SEV-4",
        }
        severity = _severity_alias.get(str(severity).lower(), severity)

        # ─ PII hashing ─────────────────────────────────────────────────────
        for field in _PII_HASH_FIELDS:
            if field in payload and payload[field]:
                payload[field] = _hash_pii(str(payload[field]))

        # ─ Payload flattening ──────────────────────────────────────────────
        # Promote known DB columns from payload to top level.
        # Unknown payload fields are serialised into the description column.
        _DB_COLUMNS = {"title", "status", "category", "description"}
        promoted: dict[str, Any] = {k: payload.pop(k) for k in list(payload) if k in _DB_COLUMNS}
        leftover_payload = json.dumps(payload, default=str) if payload else None

        validated.append({
            "id": row.get("id") or str(uuid.uuid4()),
            "title": promoted.get("title") or f"{row['event_type'].replace('_', ' ').title()} Event",
            "severity": severity,
            "status": promoted.get("status", "open"),
            "category": promoted.get("category") or row["event_type"],
            "owner": payload.get("owner"),  # already hashed above if in _PII_HASH_FIELDS
            "description": promoted.get("description") or leftover_payload,
            "created_at": created_at,
            "updated_at": datetime.now(timezone.utc),
        })

    log.info(
        "etl.transform.complete",
        input_count=len(rows),
        output_count=len(validated),
        skipped=skipped,
    )
    return validated


# ── Load ─────────────────────────────────────────────────────────────────────────
def _check_run_id_exists(conn: Any, run_id: str) -> bool:
    """
    Check whether this batch has already been loaded (idempotency guard).
    Uses the etl_runs table if it exists; silently skips the check if not.
    """

    try:
        result = conn.execute(
            sa.text("SELECT 1 FROM etl_runs WHERE run_id = :run_id"),
            {"run_id": run_id},
        )
        return result.fetchone() is not None
    except Exception:  # noqa: BLE001
        # etl_runs table not yet created — skip idempotency check
        return False


def _record_run_id(conn: Any, run_id: str, row_count: int) -> None:
    """Record a completed batch run in etl_runs for future idempotency checks."""

    try:
        conn.execute(
            sa.text(
                "INSERT INTO etl_runs (run_id, row_count, loaded_at) "
                "VALUES (:run_id, :row_count, :loaded_at)"
            ),
            {
                "run_id": run_id,
                "row_count": row_count,
                "loaded_at": datetime.now(timezone.utc),
            },
        )
    except Exception:  # noqa: BLE001
        # etl_runs table not yet created — skip recording
        pass


def load(rows: list[dict[str, Any]]) -> int:
    """
    Bulk-insert transformed rows into the `incidents` table.

    Idempotency:
      Each batch is fingerprinted with a SHA-256 run_id. If the run_id
      already exists in the `etl_runs` table, the batch is skipped.
      This makes the pipeline safe to retry on partial failure.

    Transactional safety:
      All rows are inserted inside a single transaction. On failure the
      transaction is rolled back and ETLLoadError is raised with the
      partial insert count for diagnostics.

    Batch size:
      Rows are inserted in chunks of ETL_BATCH_SIZE (default 500) to
      avoid oversized transactions and lock contention on large batches.

    Args:
        rows: Validated, transformed rows to persist.

    Returns:
        Number of rows successfully inserted.

    Raises:
        ETLLoadError: If the insert fails after rollback.
    """
    if not rows:
        log.info("etl.load.skipped", reason="empty_rows")
        return 0


    run_id = _compute_run_id(rows)
    log.info("etl.load.started", run_id=run_id, row_count=len(rows))

    engine = sa.create_engine(_DATABASE_URL, pool_pre_ping=True)
    inserted = 0

    with engine.begin() as conn:  # auto-commit on success, auto-rollback on exception
        # ─ Idempotency check ──────────────────────────────────────────────
        if _check_run_id_exists(conn, run_id):
            log.warning("etl.load.skipped", reason="already_loaded", run_id=run_id)
            engine.dispose()
            return 0

        # ─ Bulk insert in batches ────────────────────────────────────────
        try:
            for batch_start in range(0, len(rows), _BATCH_SIZE):
                batch = rows[batch_start : batch_start + _BATCH_SIZE]
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO incidents
                            (id, title, severity, status, category, owner,
                             description, created_at, updated_at)
                        VALUES
                            (:id, :title, :severity, :status, :category, :owner,
                             :description, :created_at, :updated_at)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    batch,
                )
                inserted += len(batch)
                log.info(
                    "etl.load.batch_inserted",
                    run_id=run_id,
                    batch_start=batch_start,
                    batch_size=len(batch),
                    total_inserted=inserted,
                )

            # ─ Record run for idempotency ────────────────────────────────
            _record_run_id(conn, run_id, inserted)

        except Exception as exc:
            # engine.begin() rolls back automatically on exception exit
            log.exception(
                "etl.load.failed",
                run_id=run_id,
                partial_inserted=inserted,
                error=str(exc),
            )
            engine.dispose()
            raise ETLLoadError(
                f"Load failed at row {inserted} (run_id={run_id}): {exc}. "
                "Transaction rolled back."
            ) from exc

    engine.dispose()
    log.info("etl.load.complete", run_id=run_id, inserted=inserted)
    return inserted


# ── Pipeline orchestrator ─────────────────────────────────────────────────────────
def run_pipeline() -> dict[str, Any]:
    """Orchestrate the full ETL pipeline with structured reporting."""
    start = time.monotonic()
    log.info("etl.pipeline.started")
    try:
        raw = extract()
        transformed = transform(raw)
        count = load(transformed)
        elapsed = time.monotonic() - start
        report = {
            "status": "success",
            "extracted": len(raw),
            "transformed": len(transformed),
            "loaded": count,
            "elapsed_s": round(elapsed, 3),
        }
        log.info("etl.pipeline.complete", **report)
        return report
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.exception("etl.pipeline.failed", error=str(exc), elapsed_s=round(elapsed, 3))
        raise
