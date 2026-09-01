# Troubleshooting Guide

This guide covers the most common operational issues encountered when
running, developing, or deploying the ML Incident Response API. Each
section describes the symptom, the most likely cause, and the remediation
step.

For alert-specific runbooks, see the live docs at
[mlops.zrl.dev/runbooks](https://mlops.zrl.dev/runbooks).

---

## API Startup Failures

### `RuntimeError: Missing required environment variable`

**Symptom:** The FastAPI process exits immediately on startup with a
`RuntimeError` referencing a missing env var (e.g. `DATABASE_URL`,
`JWT_SECRET_KEY`, `REDIS_URL`).

**Cause:** `src/config.py` validates all required env vars at import time
via Pydantic `Settings`. Any missing var causes an immediate hard exit.

**Fix:**
```bash
# Copy the example env file and fill in values
cp .env.example .env
# Then re-run
docker compose up --build
```

---

### `alembic.util.exc.CommandError: Can't locate revision`

**Symptom:** The API container starts but immediately logs an Alembic error
and refuses DB connections.

**Cause:** The database schema is behind the current migration head.

**Fix:**
```bash
docker compose run --rm api alembic upgrade head
```

---

## Inference / Model Errors

### `503 Service Unavailable — Model artifact not found`

**Symptom:** `POST /api/v1/inference/anomaly` returns a 503 with
`"Model artifact not found. Run python scripts/train_model.py"`.

**Cause:** `ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib`
does not exist. The artifact is excluded from source control (`.gitignore`).

**Fix:**
```bash
python scripts/train_model.py
```
This generates the artifact at the expected path. Restart the API container
if running in Docker so the new file is picked up.

---

### `RuntimeError: Artifact hash mismatch`

**Symptom:** The API fails to start or returns 503 with a hash mismatch
error in the logs.

**Cause:** The `.joblib` artifact was modified or corrupted after the
SHA-256 manifest (`.sha256` sidecar) was generated, or the `_EXPECTED_SHA256`
constant in `registry.py` does not match the current file.

**Fix:**
```bash
# Regenerate a fresh artifact and update the manifest
python scripts/train_model.py
# Verify the new digest
sha256sum ml_models/incident_anomaly/artifacts/isolation_forest_v1.joblib
```
Update `_EXPECTED_SHA256` in `registry.py` (or the `.sha256` sidecar once
MLOPS-01 ships) with the new digest.

---

### High inference latency (`PredictionLatencyDegraded` alert)

**Symptom:** `ml_prediction_latency_seconds` p95 exceeds 1.0s. The
`PredictionLatencyDegraded` Prometheus alert fires.

**Cause:** Container CPU throttling, model artifact on slow storage, or
high GIL contention from concurrent requests.

**Fix:**
1. Check container resource limits: `kubectl top pod -n ml-incident`
2. Confirm the artifact is on local disk (not a network mount).
3. If CPU-bound, increase `--workers` in the Uvicorn start command or
   scale the deployment horizontally.

---

## Drift Alerts

### `ModelMinorDrift` / `ModelMajorDrift` firing

**Symptom:** Prometheus alert fires. `ml_psi_score` is above 0.10 (minor)
or 0.20 (major) for > 10m / > 5m respectively.

**Cause:** The live incident feature distribution has shifted from the
training distribution. Common causes: data pipeline schema change,
seasonality (e.g. end-of-quarter incident spike), or upstream data source
changes.

**Fix:**
```bash
# Inspect current PSI scores in Grafana or via promtool
curl -s http://localhost:8000/metrics | grep ml_psi_score
```
1. If PSI is in the 0.10–0.20 range (minor): increase monitoring frequency;
   schedule a retraining evaluation.
2. If PSI ≥ 0.20 (major): open a SEV-1 ML incident. Evaluate halting
   model-served decisions and falling back to rule-based routing.
   Retrain with recent data: `python scripts/train_model.py`.

See the [Drift Runbook](https://mlops.zrl.dev/runbooks/drift-runbook) for
the full decision tree.

---

## Authentication Failures

### `401 Unauthorized` on all endpoints

**Symptom:** Every API request returns 401 even with a freshly minted JWT.

**Cause (most common):** The RS-256 public key in `src/auth/key_store.py`
does not match the private key used to sign the token. This happens after
a key rotation where the public key was not updated, or when the
`JWT_PUBLIC_KEY_PATH` env var points to the wrong file.

**Fix:**
```bash
# Confirm the key pair matches
openssl rsa -in private.pem -pubout | diff - public.pem
# Re-export the public key from the current private key if needed
openssl rsa -in private.pem -pubout -out public.pem
```

---

### `JWTAuthFailureSpike` / `JWTAuthFailureCritical` alerts firing

**Symptom:** The 401 error rate for `/auth/*` routes exceeds 1/s (warning)
or 10/s (critical).

**Cause:** Credential stuffing, misconfigured client, or a key rotation
that was not propagated to all callers.

**Fix:**
1. Identify the source IP: `kubectl logs -n ml-incident deploy/api | grep 401 | awk '{print $NF}' | sort | uniq -c | sort -rn | head`
2. If attack traffic: add the IP to the Redis denylist via the admin endpoint.
3. If misconfigured client: contact the team responsible and re-issue credentials.

See the [Security Runbook](https://mlops.zrl.dev/runbooks/security-runbook).

---

## Database & Redis

### `AlembicMigrationLag` alert firing

**Symptom:** `alembic_migration_head_lag` gauge is > 0. The alert fires
after 5 minutes.

**Cause:** A new migration was merged but `alembic upgrade head` was not
run against the production database (or the migration container job failed).

**Fix:**
```bash
docker compose run --rm api alembic upgrade head
# Or in Kubernetes:
kubectl exec -n ml-incident deploy/api -- alembic upgrade head
```

---

### `RedisHighMemoryUsage` alert firing

**Symptom:** Redis memory usage exceeds 85% of `maxmemory`.

**Cause:** Denylist or rate-limit key accumulation without TTL expiry, or
`maxmemory` set too low for the current request volume.

**Fix:**
1. Check key count and TTLs: `redis-cli INFO keyspace`
2. If denylist has unbounded keys: verify TTL is being set on `SETEX` calls
   in `api/redis_denylist.py`.
3. If `maxmemory` is the constraint: increase it in `redis.conf` and
   redeploy.

---

## CI / CD Failures

### `mypy` fails in CI

**Symptom:** The `ci.yml` mypy step exits non-zero.

**Cause:** A new file was added without type annotations, or a third-party
library was updated and its stubs changed.

**Fix:**
```bash
# Run mypy locally with the same config as CI
mypy . --config-file pyproject.toml
# Add annotations or update stubs as directed by the error output
```
All modules must pass with zero errors. No `ignore_errors = true` overrides
are permitted (all were removed in the ML-04 remediation, 2026-05-28).

---

### Coverage gate fails (`fail_under = 80`)

**Symptom:** The pytest coverage step exits with
`FAIL Required test coverage of 80% not reached`.

**Cause:** New code was added without corresponding tests, or tests were
deleted.

**Fix:**
```bash
# Run with coverage to see missing lines
pytest --cov=. --cov-report=term-missing tests/unit/
# Add tests to cover the flagged lines, then re-run
```
The Q3-2026 target is 85%. Current gate is 80%.

---

## Docker Build Failures

### `ERROR: failed to solve: failed to read dockerfile`

**Symptom:** `docker build` or the CI Docker step fails immediately.

**Cause:** The `Dockerfile` path passed to the build context is wrong, or
the file has a syntax error.

**Fix:**
```bash
# Validate from repo root
docker build --no-cache -t ml-incident-api:local .
```

---

### Digest pin rejected by registry

**Symptom:** `docker build` fails with
`manifest unknown: manifest unknown` on the `FROM python:3.11-alpine3.20@sha256:…` line.

**Cause:** The pinned digest in the `Dockerfile` no longer matches the
available image in the registry (rare, but possible after a registry
maintenance event).

**Fix:**
1. Pull the latest digest for the target tag:
   ```bash
   docker pull python:3.11-alpine3.20
   docker inspect python:3.11-alpine3.20 --format '{{index .RepoDigests 0}}'
   ```
2. Update both `FROM` lines in `Dockerfile` with the new digest.
3. If Dependabot is configured (it is, as of 2026-05-28), this will be
   handled automatically via a Dependabot PR going forward.
