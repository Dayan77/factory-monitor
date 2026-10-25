"""HTTP hardening of the app (docs/security.md §3): request size limit, security headers, consistent errors.

* Request bodies above MAX_BODY_BYTES (1 MiB) get 413 — by Content-Length up front, and by counting the bytes
  actually received for chunked uploads. Routes that legitimately take more have their own limit (UPLOAD_LIMITS).
* Security headers on every response: nosniff, frame-ancestors none / X-Frame-Options DENY, no-referrer,
  restrictive Permissions-Policy, a CSP that allows nothing for API responses (the interactive docs keep theirs),
  `Cache-Control: no-store` on auth/account responses, HSTS in production.
* Errors are always `{"detail": ...}` JSON: unhandled exceptions become 500 "Erro interno do servidor." with a
  request id (logged with the traceback server-side, never sent); validation errors never echo the input back
  (a password sent in a malformed body is not returned in the 422).
"""
from __future__ import annotations

import re
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ...config import settings

log = structlog.get_logger()

# (path regex, limit in bytes) — first match wins
UPLOAD_LIMITS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"^/api/layouts/[^/]+/image/?$"), 0),          # 0 = settings.layout_image_max_bytes + 1 MiB
    (re.compile(r"^/api/onboarding/(assets|tags)/import/?$"), 8 * 1024 * 1024),
]
NO_STORE_PREFIXES = ("/api/auth", "/api/account", "/api/users", "/api/api-tokens", "/api/gateways")
DOCS_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")
PRODUCTION_ENVS = {"production", "prod"}


def body_limit(path: str) -> int:
    for rx, limit in UPLOAD_LIMITS:
        if rx.match(path):
            return limit or settings.layout_image_max_bytes + 1024 * 1024
    return settings.max_body_bytes


async def _send_json(send, status: int, detail: str) -> None:
    import json
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        limit = body_limit(scope.get("path", "/"))
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    if int(v) > limit:
                        return await _send_json(send, 413, f"Requisição maior que o limite ({limit} bytes).")
                except ValueError:
                    return await _send_json(send, 400, "Content-Length inválido.")
        received = {"n": 0, "too_big": False}
        started = {"v": False}

        async def limited_receive():
            msg = await receive()
            if msg["type"] == "http.request":
                received["n"] += len(msg.get("body", b""))
                if received["n"] > limit:
                    received["too_big"] = True
                    raise _TooLarge()
            return msg

        async def tracking_send(message):
            if received["too_big"]:
                # the app turned the aborted read into its own error (FastAPI: 400): answer 413 instead, once
                if message["type"] == "http.response.start" and not started["v"]:
                    started["v"] = True
                    await _send_json(send, 413, f"Requisição maior que o limite ({limit} bytes).")
                return
            if message["type"] == "http.response.start":
                started["v"] = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started["v"]:
                await _send_json(send, 413, f"Requisição maior que o limite ({limit} bytes).")


class _TooLarge(Exception):
    pass


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "/")
        docs = path.startswith(DOCS_PATHS)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}

                def add(k: bytes, v: bytes):
                    if k not in have:
                        headers.append((k, v))
                add(b"x-content-type-options", b"nosniff")
                add(b"referrer-policy", b"no-referrer")
                add(b"x-frame-options", b"DENY")
                add(b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()")
                add(b"cross-origin-opener-policy", b"same-origin")
                if not docs:
                    add(b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                if path.startswith(NO_STORE_PREFIXES):
                    add(b"cache-control", b"no-store")
                    add(b"pragma", b"no-cache")
                if settings.app_env.lower() in PRODUCTION_ENVS:
                    add(b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, send_wrapper)


def _clean_errors(errors) -> list:
    out = []
    for e in errors:
        e = dict(e)
        e.pop("input", None)
        e.pop("url", None)
        ctx = e.get("ctx")
        if isinstance(ctx, dict):
            e["ctx"] = {k: str(v) for k, v in ctx.items()}
        out.append(e)
    return out


async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(_clean_errors(exc.errors()))})


async def unhandled_handler(request: Request, exc: Exception):
    rid = uuid.uuid4().hex[:12]
    log.error("http.unhandled_error", request_id=rid, path=request.url.path, method=request.method,
              error_type=type(exc).__name__, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor.", "request_id": rid})


def check_cors(origins: list[str]) -> None:
    bad = [o for o in origins if o == "*" or "*" in o or not re.match(r"^https?://[^/\s]+$", o)]
    if bad:
        raise RuntimeError(f"CORS_ORIGINS precisa de origens exatas (esquema://host[:porta]); inválidas: {bad}")


def install(app: FastAPI) -> None:
    """Called once from main.py (after the CORS middleware, so these wrap CORS responses too)."""
    check_cors(settings.cors_origins)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(Exception, unhandled_handler)
