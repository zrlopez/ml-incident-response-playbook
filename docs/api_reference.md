# API Reference

> **Status:** Active | **Version:** 1.0.0 | **Last Updated:** 2026-05-23

This document describes every HTTP endpoint exposed by the ML Incident Response Platform's FastAPI service layer. All endpoints require a valid JWT bearer token issued by the `/auth/token` route unless marked `[public]`. Tokens expire after 3600 seconds; refresh using `/auth/refresh`.

Base URL (local dev): `http://localhost:8000/api/v1`  
Base URL (staging): `https://staging.mlplatform.internal/api/v1`

---

## Authentication

### `POST /auth/token` `[public]`

Issues a short-lived JWT access token and a long-lived refresh token stored in an httpOnly cookie.

**Request body:**
```json
{
  "username": "string",
  "password": "string"
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Errors:**
- `401 Unauthorized` — invalid credentials
- `429 Too Many Requests` — rate limit exceeded (10 attempts / 60s per IP)

### `POST /auth/refresh`

Issues a new access token using the refresh token in the httpOnly cookie. The old refresh token is immediately invalidated and added to the Redis JWT denylist (`jwt:denylist:{jti}` key with TTL equal to remaining token lifetime).

**Response `200`:** Same shape as `/auth/token`.

**Errors:**
- `401 Unauthorized` — refresh token absent, expired, or denylisted

### `POST /auth/logout`

Adds the current access token's `jti` claim to the Redis denylist, effectively invalidating it before its natural expiry. The refresh token cookie is cleared.

**Response `204 No Content`**

---

## Incidents

### `GET /incidents`

Returns a paginated list of all incidents the authenticated user has read access to.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 25 | Results per page (max 100) |
| `status` | enum | `all` | Filter by `open`, `investigating`, `resolved`, `closed` |
| `severity` | enum | `all` | Filter by `P1`, `P2`, `P3`, `P4` |
| `model_id` | UUID | — | Filter to incidents for a specific model |
| `since` | ISO 8601 | — | Return incidents created after this timestamp |

**Response `200`:**
```json
{
  "total": 142,
  "page": 1,
  "page_size": 25,
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Drift detected: credit-risk-v3 PSI > 0.25",
      "severity": "P2",
      "status": "investigating",
      "model_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "created_at": "2026-05-22T14:33:00Z",
      "updated_at": "2026-05-23T09:12:44Z",
      "assigned_to": "mlops-on-call"
    }
  ]
}
```

### `POST /incidents`

Creates a new incident. This endpoint is also called by the drift detection pipeline's automated alerting hook.

**Request body:**
```json
{
  "title": "string (required)",
  "severity": "P1 | P2 | P3 | P4 (required)",
  "model_id": "UUID (required)",
  "description": "string (optional)",
  "trigger_source": "manual | automated_drift | automated_performance | pagerduty",
  "labels": ["string"]
}
```

**Response `201 Created`:** Full incident object.

**Errors:**
- `422 Unprocessable Entity` — missing required fields or invalid severity
- `409 Conflict` — an open incident for the same `model_id` and severity already exists

### `GET /incidents/{incident_id}`

Returns full detail for a single incident including timeline events and linked runbook.

**Path parameter:** `incident_id` (UUID)

**Response `200`:** Extended incident object with `timeline: []` and `runbook_url: string | null`.

**Errors:** `404 Not Found` if incident does not exist or caller lacks read access.

### `PATCH /incidents/{incident_id}`

Partially updates an incident. Accepts any subset of mutable fields: `status`, `severity`, `assigned_to`, `description`, `labels`.

Status transitions are validated against the state machine defined in `governance.md`. Invalid transitions (e.g., `resolved` → `investigating`) return `409 Conflict`.

### `POST /incidents/{incident_id}/timeline`

Appends a timestamped event to the incident timeline. Used by automated scripts and on-call engineers alike to maintain a chronological audit trail.

**Request body:**
```json
{
  "event_type": "note | status_change | escalation | runbook_step_completed",
  "body": "string",
  "author": "string (defaults to JWT sub claim)"
}
```

---

## Models

### `GET /models`

Returns registered ML models tracked by the platform.

**Query parameters:** `page`, `page_size`, `team`, `stage` (`staging | production | deprecated`)

### `GET /models/{model_id}/drift`

Returns the most recent drift assessment for a model, including PSI scores per feature and the composite KS-test p-value.

**Response `200`:**
```json
{
  "model_id": "UUID",
  "assessed_at": "2026-05-23T08:00:00Z",
  "psi_composite": 0.18,
  "ks_p_value": 0.031,
  "drift_detected": true,
  "feature_scores": {
    "age": 0.04,
    "credit_score": 0.21,
    "income_band": 0.09
  },
  "alert_threshold_psi": 0.15
}
```

---

## Error Format

All errors follow RFC 7807 Problem Details:

```json
{
  "type": "https://mlplatform.internal/errors/drift-threshold-exceeded",
  "title": "Drift Threshold Exceeded",
  "status": 422,
  "detail": "PSI composite score 0.28 exceeds configured threshold 0.15 for model credit-risk-v3",
  "instance": "/api/v1/models/3fa8.../drift"
}
```

---

## Rate Limits

| Endpoint group | Limit | Window |
|---|---|---|
| `/auth/*` | 10 requests | 60 seconds per IP |
| `/incidents` write operations | 30 requests | 60 seconds per user |
| All other endpoints | 200 requests | 60 seconds per user |

Rate limit headers are returned on every response: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

---

## Versioning

The API is versioned via URL path prefix (`/api/v1`). Breaking changes increment the major version. The current v1 is the only supported version. Deprecation notices will appear in response headers (`Deprecation: true`, `Sunset: <date>`) at least 90 days before a version is retired.
