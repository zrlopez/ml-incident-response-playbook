"""
api/middleware.py — Request hardening middleware stack
======================================================
Remediation: HIGH-C (Phase 0)

Findings addressed:
  HIGH-C  No request body size limit — memory exhaustion DoS
  HIGH-C  No request timeout — slow-loris / resource exhaustion
  MED-E   No security headers (X-Content-Type-Options, etc.)
  LOW-C   No request ID propagation for cross-service trace correlation

Middleware execution order (outermost → innermost):
  1. SecurityHeadersMiddleware   — adds headers to every response
  2. MaxBodySizeMiddleware       — rejects oversized requests before parsing
  3. RequestTimeoutMiddleware    — cancels requests that exceed time budget
  4. trace_and_security_headers  — @app.middleware("http") trace + header sweep

R-GOD: trace_and_security_headers extracted from api/app.py and added here.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import MutableMapping
from typing import Any, Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = structlog.get_logger(__name__)


# ── trace_and_security_headers ───────────────────────────────────────────────
async def trace_and_security_headers(
    request: Request,
    call_next: Callable[..., Awaitable[Response]],
) -> Response:
    """
    @app.middleware("http") handler: attaches trace_id, measures duration,
    and writes security / cache-control headers on every response.

    R-GOD: extracted from api/app.py inline definition.
    """
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        method=request.method,
        path=str(request.url.path),
        client_ip=request.client.host if request.client else "unknown",
    )
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    log.info("http.request", status_code=response.status_code, duration_ms=duration_ms)
    structlog.contextvars.clear_contextvars()
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store"
    if "server" in response.headers:
        del response.headers["server"]
    if "x-powered-by" in response.headers:
        del response.headers["x-powered-by"]
    return response


# ── MaxBodySizeMiddleware ────────────────────────────────────────────────────
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """
    HIGH-C REMEDIATION: Reject requests whose bodies exceed MAX_BYTES.

    Two-pass approach:
      1. Content-Length header: fast reject before any body reads.
      2. Streaming byte count: catches chunked/multipart requests
         that omit Content-Length.
    """

    MAX_BYTES: int = 1 * 1024 * 1024  # 1 MB

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        content_length_header = request.headers.get("content-length")
        if content_length_header is not None:
            try:
                declared_length = int(content_length_header)
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid Content-Length header."},
                    status_code=400,
                )
            if declared_length > self.MAX_BYTES:
                log.warning(
                    "request.body_too_large",
                    path=request.url.path,
                    declared_bytes=declared_length,
                    limit_bytes=self.MAX_BYTES,
                    client=request.client.host if request.client else "unknown",
                )
                return JSONResponse(
                    {"detail": f"Request body too large. Maximum allowed size is {self.MAX_BYTES // 1024} KB."},  # noqa: E501
                    status_code=413,
                    headers={"Content-Type": "application/json"},
                )

        received_bytes = 0
        original_receive = request._receive

        async def counting_receive() -> MutableMapping[str, Any]:
            nonlocal received_bytes
            message = await original_receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > self.MAX_BYTES:
                    log.warning(
                        "request.body_too_large_streaming",
                        path=request.url.path,
                        received_bytes=received_bytes,
                        limit_bytes=self.MAX_BYTES,
                        client=request.client.host if request.client else "unknown",
                    )
                    raise BodyTooLargeError(f"Streaming body exceeded {self.MAX_BYTES} bytes.")
            return message

        request._receive = counting_receive  # type: ignore[method-assign]

        try:
            return await call_next(request)
        except BodyTooLargeError:
            return JSONResponse(
                {"detail": f"Request body too large. Maximum allowed size is {self.MAX_BYTES // 1024} KB."},  # noqa: E501
                status_code=413,
            )


class BodyTooLargeError(Exception):
    """Internal signal raised by counting_receive when body limit is exceeded."""


# ── SecurityHeadersMiddleware ────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """MED-E REMEDIATION: Inject OWASP-recommended security headers on every response."""

    _DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

    def __init__(self, app: ASGIApp, environment: str = "development") -> None:
        super().__init__(app)
        self._environment = environment

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=()"  # noqa: E501
        response.headers["X-XSS-Protection"] = "0"
        if request.url.path in self._DOCS_PATHS:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self';"
            )
        else:
            response.headers["Content-Security-Policy"] = "default-src 'none'"
        if self._environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"  # noqa: E501
        request_id = request.state.__dict__.get("request_id", "")
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response


# ── RequestTimeoutMiddleware ─────────────────────────────────────────────────
class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a per-request wall-clock timeout to prevent slow-loris exhaustion."""

    TIMEOUT_SECONDS: float = 30.0

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        try:
            return await asyncio.wait_for(
                call_next(request),
                timeout=self.TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning(
                "request.timeout",
                path=request.url.path,
                method=request.method,
                timeout_seconds=self.TIMEOUT_SECONDS,
                client=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                {"detail": "Request timed out. Please retry."},
                status_code=504,
                headers={"Retry-After": str(int(self.TIMEOUT_SECONDS))},
            )
