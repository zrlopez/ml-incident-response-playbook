# ADR-004: structlog + OpenTelemetry Observability Model

| Field        | Value                               |
|---|---|
| Status       | Accepted                            |
| Date         | 2026-05-23                          |
| Deciders     | Platform Engineering, SRE           |

## Context
The system requires structured, searchable logs; distributed trace context
propagation; and a standardised audit trail routable to SIEM. Standard
Python `logging` produces unstructured text that is difficult to query
at scale.

## Decision
Use `structlog` for all application logging with JSON rendering in production
and console rendering in development. Use `opentelemetry-sdk` with OTLP gRPC
export for distributed tracing. Inject `trace_id` into every log event via
`structlog.contextvars`.

## Key design choices
- `log_type='audit'` field tags all security-relevant events for SIEM routing.
- PII scrubbing processor runs on every log pipeline to prevent credential
  or personal data leakage into log aggregators.
- Alert dispatch (Slack + PagerDuty) is fire-and-forget via
  `asyncio.create_task()` so alert failures never affect request handling.
- `AlertLevel` enum values match PagerDuty Events v2 severity strings exactly,
  enabling direct pass-through without mapping.

## Consequences
- `configure_logging()` must be called at application startup before any
  `structlog.get_logger()` call.
- Log aggregator (Datadog / Splunk / CloudWatch) must be configured to parse
  JSON and index `log_type`, `trace_id`, `incident_id` as facets.
- OTel collector endpoint is required in production; the default
  `localhost:4317` is only suitable for local development.
