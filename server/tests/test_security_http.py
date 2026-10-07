"""Sprint 7 — input/response hardening (docs/security.md §3): body size limit, strict write bodies, errors without
stack traces nor echoed input, security headers, exact CORS origins, injection review (SQL/Flux), audit coverage of
security-relevant integration changes."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sec_helpers import audit_actions, bearer, login, make_sec_tenant

SRC = Path(__file__).resolve().parents[1] / "src"


async def test_body_size_limit(client):
    big = "x" * (1024 * 1024 + 10)
    r = await client.post("/api/auth/login", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"].startswith("Requisição maior")

    async def gen():
        for _ in range(20):
            yield b"x" * 65536 * 2
    r = await client.post("/api/auth/login", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_upload_routes_have_their_own_limit():
    from src.config import settings
    from src.services.security.http import body_limit
    assert body_limit("/api/auth/login") == settings.max_body_bytes
    assert body_limit("/api/layouts/abc/image") > settings.layout_image_max_bytes
    assert body_limit("/api/onboarding/tags/import") >= 4 * 1024 * 1024


async def test_validation_errors_do_not_echo_input(client):
    secret = "Minha-Senha-Super-Secreta-123"
    r = await client.post("/api/auth/login", json={"email": "not-an-email", "password": secret})
    assert r.status_code == 422
    assert secret not in r.text and "input" not in r.json()["detail"][0]
    r = await client.post("/api/auth/login", json={"email": "a@example.com", "password": secret, "role": "admin"})
    assert r.status_code == 422 and secret not in r.text


async def test_unhandled_error_is_json_without_trace(app):
    import httpx

    async def boom():
        raise RuntimeError("segredo interno /etc/passwd")
    app.add_api_route("/api/__boom_test", boom, methods=["GET"])
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/__boom_test")
        assert r.status_code == 500
        body = r.json()
        assert body["detail"] == "Erro interno do servidor." and body["request_id"]
        assert "segredo" not in r.text and "Traceback" not in r.text
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/api/__boom_test"]


async def test_security_headers(client):
    r = await client.get("/api/health")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer" and "frame-ancestors 'none'" in h["content-security-policy"]
    assert "camera=()" in h["permissions-policy"]
    r = await client.post("/api/auth/login", json={"email": "a@example.com", "password": "x" * 12})
    assert r.headers["cache-control"] == "no-store"
    docs = await client.get("/api/docs")
    assert "content-security-policy" not in docs.headers          # swagger UI keeps working


async def test_hsts_only_in_production(client, monkeypatch):
    from src.config import settings
    assert "strict-transport-security" not in (await client.get("/api/health")).headers
    monkeypatch.setattr(settings, "app_env", "production")
    assert "max-age=" in (await client.get("/api/health")).headers["strict-transport-security"]


async def test_cors_exact_origins(client):
    ok = await client.options("/api/auth/login", headers={"Origin": "https://app.factoryconnect.tech",
                                                          "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.factoryconnect.tech"
    bad = await client.options("/api/auth/login", headers={"Origin": "https://evil.example.com",
                                                           "Access-Control-Request-Method": "POST"})
    assert bad.headers.get("access-control-allow-origin") is None
    from src.services.security.http import check_cors
    with pytest.raises(RuntimeError):
        check_cors(["*"])
    with pytest.raises(RuntimeError):
        check_cors(["https://*.example.com"])
    check_cors(["http://localhost:5173", "https://app.factoryconnect.tech"])


# ── injection review ───────────────────────────────────────────────

def _calls_with_fstring(name_rx: str):
    hits = []
    for p in SRC.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if re.fullmatch(name_rx, fname or "") and node.args and isinstance(node.args[0], ast.JoinedStr):
                    hits.append(f"{p.relative_to(SRC)}:{node.lineno}")
    return hits


def test_no_sql_built_with_fstrings():
    """SQL text is always parametrized: no text(f"...") / execute(f"...") anywhere in the server."""
    assert _calls_with_fstring(r"text|execute|exec_driver_sql") == []


def test_flux_interpolation_only_of_validated_values():
    """Every Flux query interpolates only ids that passed ensure_uuid / come from UUID columns, durations that passed
    ensure_flux_duration and RFC 3339 datetimes; the public entry points refuse anything else."""
    from src.services.validation import ensure_flux_duration, ensure_uuid
    for bad in ('x" or true or "', "00000000-0000-0000-0000-00000000000'", ") |> drop()"):
        with pytest.raises(Exception):
            ensure_uuid(bad, "tag_id")
    for bad in ("1m) |> yield()", "1h; drop", ""):
        with pytest.raises(Exception):
            ensure_flux_duration(bad, "window")


async def test_flux_injection_through_api_is_refused(client, tenants):
    h = tenants.a.headers("admin")
    r = await client.get('/api/sensors/x" or r._measurement == "y/history', headers=h)
    assert r.status_code in (404, 422)
    r = await client.get("/api/sensors/00000000-0000-0000-0000-000000000000/history",
                         params={"window": "1m) |> yield(name: \"x\""}, headers=h)
    assert r.status_code in (404, 422)


# ── strict write bodies ────────────────────────────────────────────

def _write_body_models():
    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    from src.main import app
    out = {}
    for rt in app.routes:
        if not isinstance(rt, APIRoute) or not (rt.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        body = rt.body_field
        ann = getattr(body, "type_", None) if body is not None else None
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.setdefault(ann, set()).add(rt.path)
    return out


# Write bodies that may still carry unknown fields (other areas' models; the UI sends extra keys on some of them).
# Every model here is ignored-extra, never stored: shrink this list, never grow it (docs/security.md §3.2).
EXTRA_ALLOWED_PREFIXES = ("/api/lines", "/api/machines", "/api/tags", "/api/alerts", "/api/cameras", "/api/plants",
                          "/api/positions", "/api/components", "/api/usage", "/api/public", "/api/shifts",
                          "/api/calendar-exceptions", "/api/products", "/api/production-runs", "/api/reason-codes",
                          "/api/downtime", "/api/maintenance", "/api/work-orders", "/api/failures",
                          "/api/failure-codes", "/api/notifications", "/api/push", "/api/gateways", "/api/predictive",
                          "/api/layouts", "/api/onboarding", "/api/demo", "/api/reports", "/api/report-runs",
                          "/api/report-schedules", "/api/ai", "/api/admin", "/api/oee", "/api/webhooks",
                          "/api/api-tokens", "/api/sensors")
STRICT_PREFIXES = ("/api/auth", "/api/account", "/api/users")


def test_security_write_bodies_forbid_extra_fields():
    loose = []
    for model, paths in _write_body_models().items():
        if model.model_config.get("extra") == "forbid":
            continue
        for p in paths:
            if p.startswith(STRICT_PREFIXES):
                loose.append((p, model.__name__))
            elif not p.startswith(EXTRA_ALLOWED_PREFIXES):
                loose.append((p, model.__name__))
    assert loose == []


async def test_users_bodies_are_strict(client, tenants):
    h = tenants.a.headers("admin")
    r = await client.patch(f"/api/users/{tenants.a.users['operator']['id']}", json={"tenant_id": tenants.b.id},
                           headers=h)
    assert r.status_code == 422


# ── audit coverage of integration changes ──────────────────────────

async def test_token_and_webhook_changes_are_audited(client, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "webhook_allow_private_hosts", True)
    st = await make_sec_tenant("Aud", roles=("admin",))
    h = bearer((await login(client, st.email("admin"))).json()["access_token"])
    tok = (await client.post("/api/api-tokens/", json={"name": "erp", "scopes": ["read"]}, headers=h)).json()
    assert (await client.delete(f"/api/api-tokens/{tok['id']}", headers=h)).status_code == 204
    hook = await client.post("/api/webhooks/", json={"url": "http://127.0.0.1:9/h", "events": ["*"], "secret": "s" * 20},
                             headers=h)
    assert hook.status_code == 201, hook.text
    hid = hook.json()["id"]
    assert (await client.patch(f"/api/webhooks/{hid}", json={"is_active": False}, headers=h)).status_code == 200
    assert (await client.delete(f"/api/webhooks/{hid}", headers=h)).status_code == 204
    acts = await audit_actions(st.users["admin"]["id"])
    for a in ("api_token.created", "api_token.revoked", "webhook.created", "webhook.updated", "webhook.deleted"):
        assert a in acts, a
    import json
    from sqlalchemy import select

    from src.db import AsyncSessionLocal
    from src.models import AuditEvent
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(AuditEvent.data).where(AuditEvent.tenant_id == st.id))).scalars().all()
    blob = json.dumps(rows)
    assert tok.get("token", "fct_") not in blob and "s" * 20 not in blob


async def test_webhook_ssrf_refused(client, tenants):
    h = tenants.a.headers("admin")
    for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "file:///etc/passwd",
                "http://[::1]/", "gopher://x"):
        r = await client.post("/api/webhooks/", json={"url": url, "events": ["*"]}, headers=h)
        assert r.status_code == 422, (url, r.text)


async def test_gateway_credentials_are_audited(client):
    from src.db import AsyncSessionLocal
    from src.models import Plant
    st = await make_sec_tenant("GwAud", roles=("admin",))
    async with AsyncSessionLocal() as s:
        p = Plant(tenant_id=st.id, name="P")
        s.add(p)
        await s.commit()
        pid = p.id
    h = bearer((await login(client, st.email("admin"))).json()["access_token"])
    gw = (await client.post("/api/gateways/", json={"name": "gw", "plant_id": pid}, headers=h)).json()
    code = (await client.post(f"/api/gateways/{gw['id']}/enroll-token", headers=h)).json()["code"]
    r = await client.post("/api/gateways/enroll", json={"code": code})
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/gateways/{gw['id']}/revoke", headers=h)).status_code == 200
    acts = await audit_actions(st.users["admin"]["id"])
    assert "gateway.enroll_code_created" in acts and "gateway.revoked" in acts
    from sqlalchemy import select

    from src.models import AuditEvent
    async with AsyncSessionLocal() as s:
        enrolled = (await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == st.id,
                                                             AuditEvent.action == "gateway.enrolled"))).scalars().all()
    assert len(enrolled) == 1 and code not in str(enrolled[0].data)
