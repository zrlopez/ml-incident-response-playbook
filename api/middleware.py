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
  (RequestIdMiddleware is wired via app.py add_middleware, not here)

Registration in api/app.py:
    from api.middleware import MaxBodySizeMiddleware, SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(MaxBodySizeMiddleware)

Production note:
  MaxBodySizeMiddleware uses Content-Length header inspection as a fast
  first-pass check. For chunked-transfer requests without Content-Length,
  it streams and counts bytes, rejecting when the running total exceeds
  MAX_BYTES. This prevents both announced and unannounced large uploads.
"""
from __future__ import annotations

import time
import asyncio
from typing import Any, Awaitable, Callable, Dict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp
import structlog

log = structlog.get_logger(__name__)


# ───────────────────────────────────────────────────────────────────
class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """
    HIGH-C REMEDIATION: Reject requests whose bodies exceed MAX_BYTES.

    Two-pass approach:
      1. Content-Length header: fast reject before any body reads.
      2. Streaming byte count: catches chunked/multipart requests
         that omit Content-Length.

    Tuning:
      MAX_BYTES = 1 MB is appropriate for JSON API payloads.
      If you add file-upload endpoints, override per-route with a
      dedicated endpoint-level check rather than raising the global limit.

    Security justification:
      Without a body size limit, a single unauthenticated client can POST
      a multi-GB body to /auth/token, consuming memory until OOM kill.
      FastAPI/Starlette's default has NO body size limit.
    """

    MAX_BYTES: int = 1 * 1024 * 1024  # 1 MB — tune per deployment

    async def dispatch(self, request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
        # Fast path: Content-Length header is present and oversized
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
                    {
                        "detail": (
                            f"Request body too large. "
                            f"Maximum allowed size is {self.MAX_BYTES // 1024} KB."
                        )
                    },
                    status_code=413,
                    headers={"Content-Type": "application/json"},
                )

        # Slow path: stream body and count bytes for chunked transfers
        # Wrap the receive channel to intercept body chunks
        received_bytes = 0
        original_receive = request._receive

        async def counting_receive() -> dict:  # type: ignore[return-value]
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
                    # Signal 413 by raising; the exception handler returns HTTP 413
                    raise BodyTooLargeError(
                        f"Streaming body exceeded {self.MAX_BYTES} bytes."
                    )
            return message

        request._receive = counting_receive  # type: ignore[method-assign]

        try:
            return await call_next(request)
        except BodyTooLargeError:
            return JSONResponse(
                {
                    "detail": (
                        f"Request body too large. "
                        f"Maximum allowed size is {self.MAX_BYTES // 1024} KB."
                    )
                },
                status_code=413,
            )


class BodyTooLargeError(Exception):
    """Internal signal raised by counting_receive when body limit is exceeded."""


# ───────────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    MED-E REMEDIATION: Inject OWASP-recommended security headers on every response.

    Headers applied:
      X-Content-Type-Options: nosniff
        Prevents MIME-type sniffing attacks in browsers.

      X-Frame-Options: DENY
        Blocks the API responses from being framed (clickjacking).
        APIs serving no UI content should always deny framing.

      Referrer-Policy: strict-origin-when-cross-origin
        Limits referrer leakage on cross-origin requests.

      Permissions-Policy: geolocation=(), microphone=(), camera=()
        Disables dangerous browser APIs. Appropriate for an API service.

      Content-Security-Policy: default-src 'none'
        APIs should serve no scripts, images, or frames.
        Adjusted to allow /docs (Swagger) and /redoc when needed.

      X-Request-ID:
        Echoes back the request ID from trace middleware for correlation.

      Strict-Transport-Security:
        Added for production. Requires HTTPS. Do NOT add in local dev
        where HTTP is expected — controlled by ENVIRONMENT env var.

    Note: These headers complement, not replace, your reverse proxy's
    header configuration. Both layers should apply them (defense in depth).
    """

    # Docs/redoc paths need relaxed CSP for Swagger UI assets
    _DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

    def __init__(self, app: ASGIApp, environment: str = "development") -> None:
        super().__init__(app)
        self._environment = environment

    async def dispatch(self, request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
        response = await call_next(request)

        # Core security headers — all environments
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        response.headers["X-XSS-Protection"] = "0"  # Modern browsers: rely on CSP

        # CSP: relaxed for docs paths, strict for all other API paths
        if request.url.path in self._DOCS_PATHS:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' ;"
            )
        else:
            response.headers["Content-Security-Policy"] = "default-src 'none'"

        # HSTS: production only (local dev often runs over HTTP)
        if self._environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )

        # Propagate request ID for distributed tracing correlation
        request_id = request.state.__dict__.get("request_id", "")
        if request_id:
            response.headers["X-Request-ID"] = request_id

        return response


# ───────────────────────────────────────────────────────────────────
class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """
    Enforce a per-request wall-clock timeout to prevent slow-loris and
    long-running request resource exhaustion.

    Default: 30 seconds. Override per-route with BackgroundTask for
    intentionally long-running endpoints (e.g., large report generation).

    On timeout: returns HTTP 504 Gateway Timeout with a Retry-After header.
    The in-flight request coroutine is cancelled via asyncio.wait_for.
    """

    TIMEOUT_SECONDS: float = 30.0

    async def dispatch(self, request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
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
