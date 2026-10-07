"""Sprint 7 — authorization fuzzing over the WHOLE FastAPI route table (docs/security.md §2).

Every route × every principal: no token, each role of tenant A, the public-demo viewer, a gateway device token,
API tokens with scope read / write, and tenant B (admin, operator) using tenant A's ids. The expected allow/deny of
each (method, path) is declared as data in src/services/security/policy.py (POLICY); this test proves the app
matches it:

* not allowed → never 2xx/3xx (401 without token, else 401/403/404/405/409/422/429), never 5xx;
* allowed → never 401/403 and never 500 (404/409/422 are fine: the probe uses generated bodies);
* tenant B → no 2xx response may contain any tenant-A id or name marker, and no 204 on a route addressed by a
  tenant-A id (a silent cross-tenant write);
* a route without a policy entry (new endpoint) fails the test until it gets one.

Each principal runs against its own fresh world (tenant A rebuilt), DELETEs last, so a destructive allowed call can
not hide a missing check in a later one. FM_AUTHZ_DUMP=<file> writes the observed matrix (to review a change).
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from fastapi.routing import APIRoute

from authz_world import body_for, build_world, fill_path, fresh_token

A_PRINCIPALS = ("anon", "gateway", "demo_viewer", "api_read", "operator", "technician", "production_supervisor",
                "maintenance_planner", "api_write", "manager", "admin", "superadmin")
B_PRINCIPALS = ("b_operator", "b_admin")
SELF_DESTRUCTIVE = {("DELETE", "/api/account"), ("POST", "/api/auth/logout-all"), ("POST", "/api/auth/logout")}
HUMAN = {"operator", "technician", "production_supervisor", "maintenance_planner", "manager", "admin", "superadmin"}
DENY_OK = {401, 403, 404, 405, 409, 410, 413, 422, 429}
# routes that call external services absent in tests may answer 502/503/504 to an allowed caller
UPSTREAM_OK = {502, 503, 504}


def _routes(app) -> list[tuple[str, str]]:
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    # DELETEs last (they may remove the world's objects)
    return sorted(set(out), key=lambda x: (x[0] == "DELETE", x[1], x[0]))


async def _call(client, spec, w, principal, method, path):
    url = fill_path(w, path)
    headers = {"X-Forwarded-For": w.ip}
    if principal != "anon":
        if (method, path) in SELF_DESTRUCTIVE and principal in HUMAN:
            headers["Authorization"] = await fresh_token(principal, w.a_id)
        else:
            headers.update(w.header(principal))
    kind, body = body_for(spec, method, path, w)
    kw = {}
    if kind == "json":
        kw["json"] = body
    elif kind == "files":
        kw["files"] = body
    r = await client.request(method, url, headers=headers, **kw)
    return r


async def _world(ip_seed: str):
    w = await build_world()
    w.ip = f"198.19.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}"
    return w


@pytest.fixture(scope="module")
def observed():
    return {}


async def test_authz_matrix_every_route_every_principal(app, client, observed):
    from src.services.security.policy import POLICY, accepts_401, allowed
    spec = app.openapi()
    routes = _routes(app)
    discover = bool(os.environ.get("FM_AUTHZ_DISCOVER"))      # dump the observed matrix, assert nothing
    missing = [f"{m} {p}" for m, p in routes if f"{m} {p}" not in POLICY]
    stale = sorted(set(POLICY) - {f"{m} {p}" for m, p in routes})
    if not discover:
        assert missing == [], "routes without an authorization policy (add them to services/security/policy.py)"
        assert stale == [], "policy entries for routes that no longer exist"

    problems = []
    for principal in A_PRINCIPALS:
        w = await _world(principal)
        for method, path in routes:
            key = f"{method} {path}"
            r = await _call(client, spec, w, principal, method, path)
            observed.setdefault(key, {})[principal] = r.status_code
            if discover:
                if r.status_code >= 500:
                    observed[key][principal + "_err"] = r.text[:300]
                continue
            ok = allowed(POLICY[key], principal, method, path)
            sc = r.status_code
            if sc >= 500 and not (ok and sc in UPSTREAM_OK):
                problems.append(f"{key} as {principal}: {sc} {r.text[:200]}")
            elif ok and sc in (401, 403) and not (sc == 401 and accepts_401(POLICY[key])):
                problems.append(f"{key} as {principal}: expected allowed, got {sc} {r.text[:160]}")
            elif not ok and principal == "anon" and sc != 401:
                problems.append(f"{key} anonymous: expected 401, got {sc} {r.text[:160]}")
            elif not ok and sc not in DENY_OK:
                problems.append(f"{key} as {principal}: expected denied, got {sc} {r.text[:160]}")
    _dump(observed)
    assert discover or problems == [], "\n".join(problems)


def _contains_marker(text: str, markers) -> list[str]:
    return [m for m in markers if m and m in text]


async def test_tenant_b_never_sees_or_changes_tenant_a(app, client, observed):
    from src.services.security.policy import CROSS_TENANT_204_OK
    spec = app.openapi()
    routes = _routes(app)
    problems = []
    for principal in B_PRINCIPALS:
        w = await _world(principal)
        for method, path in routes:
            key = f"{method} {path}"
            r = await _call(client, spec, w, principal, method, path)
            observed.setdefault(key, {})[principal] = r.status_code
            sc = r.status_code
            if 200 <= sc < 300 and _contains_marker(r.text, w.markers):
                observed[key][principal + "_leak"] = r.text[:300]
            if sc >= 500 and sc not in UPSTREAM_OK:
                problems.append(f"{key} as {principal}: {sc} {r.text[:200]}")
                continue
            if 200 <= sc < 300:
                leaked = _contains_marker(r.text, w.markers)
                if leaked:
                    problems.append(f"{key} as {principal}: tenant A data leaked ({leaked[:3]}) {r.text[:160]}")
                if sc == 204 and "{" in path and key not in CROSS_TENANT_204_OK:
                    problems.append(f"{key} as {principal}: 204 on a tenant-A resource")
        # the world of tenant A is intact after tenant B's whole run
        from src.db import AsyncSessionLocal
        from src.models import Line, Machine, User, WorkOrder
        async with AsyncSessionLocal() as s:
            assert await s.get(Line, w.ids["line"]) is not None
            assert await s.get(Machine, w.ids["machine"]) is not None
            assert await s.get(WorkOrder, w.ids["wo"]) is not None
            victim = await s.get(User, w.ids["user"])
            assert victim.is_active and victim.anonymized_at is None
    _dump(observed)
    assert os.environ.get("FM_AUTHZ_DISCOVER") or problems == [], "\n".join(problems)


def _dump(observed):
    path = os.environ.get("FM_AUTHZ_DUMP")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(observed, f, indent=1, sort_keys=True)


def test_policy_semantics():
    from src.services.security.policy import allowed
    assert allowed("public", "anon", "POST", "/api/public/leads")
    assert not allowed("user", "anon", "GET", "/api/lines/")
    assert allowed("user", "demo_viewer", "GET", "/api/lines/")
    assert not allowed("user", "demo_viewer", "POST", "/api/lines/")
    assert not allowed("user", "demo_viewer", "GET", "/api/users/")             # blocked area
    assert allowed("user", "api_read", "GET", "/api/lines/") and not allowed("user", "api_read", "POST", "/x")
    assert allowed("user", "api_write", "POST", "/x")
    assert not allowed("manager", "api_write", "GET", "/x") and allowed("manager", "admin", "GET", "/x")
    assert allowed("any(maintenance_planner)", "api_read", "GET", "/x")
    assert not allowed("any(technician)", "api_read", "GET", "/x")
    assert allowed("any(technician)", "superadmin", "POST", "/x")
    assert not allowed("user+noapi", "api_write", "POST", "/x")
    assert allowed("only(operator|admin)", "operator", "GET", "/x") and not allowed("only(admin)", "superadmin", "GET", "/x")


async def test_leak_detector_is_effective(app, client):
    """Control: tenant A's own admin DOES see the markers on the same requests (so their absence for tenant B is
    meaningful)."""
    spec = app.openapi()
    w = await _world("control")
    hits = 0
    for method, path in (("GET", "/api/lines/"), ("GET", "/api/machines/{machine_id}"), ("GET", "/api/work-orders/"),
                         ("GET", "/api/users/{user_id}")):
        r = await _call(client, spec, w, "admin", method, path)
        assert r.status_code == 200, (path, r.text)
        hits += bool(_contains_marker(r.text, w.markers))
    assert hits == 4
