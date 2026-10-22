"""Sprint 4 RBAC (maintenance_workflows.md §1) for every new endpoint, and tenant isolation: tenant B never
sees, changes or references tenant A's plans, work orders, failures, KPIs, users, notifications, tokens."""
from __future__ import annotations

import pytest

from maint_helpers import api, make_maint_tenant
from test_maintenance_api import _cylinder

ROLES = ("admin", "manager", "maintenance_planner", "technician", "production_supervisor", "operator")
ALL = set(ROLES)
MAINT_READ = {"admin", "manager", "maintenance_planner", "technician", "production_supervisor"}
PLANNER = {"admin", "manager", "maintenance_planner"}
KPI = {"admin", "manager", "maintenance_planner", "production_supervisor"}
LISTERS = {"admin", "manager", "maintenance_planner", "production_supervisor"}
ADMIN = {"admin"}


@pytest.fixture(scope="module")
def world():
    return {}


async def _world(client, world):
    if "m" not in world:
        m = await make_maint_tenant("R")
        asset = await _cylinder(m)
        pl = m.h("maintenance_planner")
        plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
            "name": "p", "position_id": asset["position_id"], "interval_days": 30})
        wo = await api(client, "POST", "/api/work-orders/", pl, 201, json={
            "type": "improvement", "state": "open", "title": "w", "machine_id": asset["machine_id"]})
        fl = await api(client, "POST", "/api/failures/", pl, 201, json={
            "machine_id": asset["machine_id"], "failed_at": "2026-01-01T00:00:00Z"})
        world.update(m=m, asset=asset, plan=plan, wo=wo, failure=fl)
    return world


def _cases(w):
    m, a = w["m"], w["asset"]
    return [
        ("GET", "/api/maintenance-plans/", None, MAINT_READ),
        ("GET", "/api/maintenance-plans/status", None, MAINT_READ),
        ("GET", f"/api/maintenance-plans/{w['plan']['id']}", None, MAINT_READ),
        ("POST", "/api/maintenance-plans/", {"name": "x", "machine_id": a["machine_id"], "interval_days": 9}, PLANNER),
        ("PATCH", f"/api/maintenance-plans/{w['plan']['id']}", {"name": "p2"}, PLANNER),
        ("POST", "/api/maintenance-plans/recompute", None, PLANNER),
        ("GET", "/api/work-orders/", None, ALL),
        ("PATCH", f"/api/work-orders/{w['wo']['id']}", {"title": "w2"}, PLANNER),
        ("POST", "/api/work-orders/", {"type": "improvement", "state": "draft", "title": "t", "machine_id": a["machine_id"]}, PLANNER),
        ("POST", "/api/work-orders/", {"type": "corrective", "state": "open", "title": "req", "machine_id": a["machine_id"]},
         PLANNER | {"technician", "production_supervisor", "operator"}),
        ("GET", "/api/failures/", None, MAINT_READ),
        ("GET", f"/api/failures/{w['failure']['id']}", None, MAINT_READ),
        ("POST", "/api/failures/", {"machine_id": a["machine_id"], "failed_at": "2026-01-02T00:00:00Z"},
         MAINT_READ),
        ("PATCH", f"/api/failures/{w['failure']['id']}", {"note": "n"}, MAINT_READ),
        ("GET", "/api/failure-codes/", None, ALL),
        ("GET", "/api/maintenance/kpis", None, KPI),
        ("GET", "/api/maintenance/reliability", None, KPI),
        ("GET", "/api/maintenance/components", None, MAINT_READ),
        ("GET", "/api/maintenance/settings", None, MAINT_READ),
        ("PUT", "/api/maintenance/settings", {"schedule_tolerance_days": 0}, {"admin", "manager"}),
        ("GET", "/api/maintenance/absences", None, PLANNER),
        ("POST", "/api/maintenance/absences", {"user_id": m.users["technician"]["id"], "date": "2026-01-05", "hours": 1}, PLANNER),
        ("GET", "/api/users/", None, LISTERS),
        ("GET", f"/api/users/{m.users['technician']['id']}", None, LISTERS),
        ("PATCH", f"/api/users/{m.users['technician']['id']}", {"on_call": True}, {"admin", "manager", "maintenance_planner"}),
        ("GET", "/api/notifications/", None, ALL),
        ("GET", "/api/notifications/preferences", None, ALL),
        ("POST", "/api/notifications/read-all", None, ALL),
        ("GET", "/api/push/vapid-public-key", None, ALL),
        ("GET", "/api/api-tokens/", None, ADMIN),
        ("GET", "/api/webhooks/", None, ADMIN),
        ("GET", "/api/webhooks/events", None, ADMIN),
    ]


@pytest.mark.parametrize("role", ROLES)
async def test_rbac_matrix(client, world, role):
    w = await _world(client, world)
    m = w["m"]
    for method, url, body, allowed in _cases(w):
        r = await client.request(method, url, headers=m.h(role), json=body)
        if role in allowed:
            assert r.status_code not in (401, 403), (role, method, url, r.status_code, r.text)
            assert r.status_code < 500, (role, method, url, r.text)
        else:
            assert r.status_code == 403, (role, method, url, r.status_code, r.text)


async def test_transitions_by_role(client, world):
    """Who may move a WO (state validity aside): planner approves/schedules/cancels; technician starts,
    pauses, completes; supervisor closes; operator nothing."""
    w = await _world(client, world)
    m, a = w["m"], w["asset"]
    pl = m.h("maintenance_planner")
    wo = await api(client, "POST", "/api/work-orders/", pl, 201, json={
        "type": "improvement", "state": "open", "title": "roles", "machine_id": a["machine_id"]})
    url = f"/api/work-orders/{wo['id']}/transition"
    steps = [("approved", {}, "maintenance_planner"),
             ("scheduled", {"scheduled_for": "2030-01-01", "assignee_id": m.users["technician"]["id"]}, "maintenance_planner"),
             ("in_progress", {}, "technician"), ("on_hold", {"hold_reason": "peça"}, "technician"),
             ("in_progress", {}, "maintenance_planner"), ("completed", {}, "technician"),
             ("closed", {}, "production_supervisor")]
    for to, extra, who in steps:
        for role in ("operator", "technician", "production_supervisor", "maintenance_planner"):
            if role == who:
                continue
            if to == "in_progress" and role in ("technician", "maintenance_planner"):
                continue          # both may (start / give back to the technician)
            r = await client.post(url, headers=m.h(role), json={"to": to, **extra})
            # the operator does not even see a WO he did not request (404)
            assert r.status_code == (404 if role == "operator" else 403), (to, role, r.text)
        d = await api(client, "POST", url, m.h(who), json={"to": to, **extra})
        assert d["state"] == to
    assert d["user_allowed_transitions"] == []


async def test_unauthenticated(client):
    for url in ("/api/maintenance-plans/", "/api/maintenance-plans/status", "/api/work-orders/", "/api/failures/",
                "/api/failure-codes/", "/api/maintenance/kpis", "/api/maintenance/reliability", "/api/notifications/",
                "/api/users/", "/api/webhooks/", "/api/api-tokens/"):
        assert (await client.get(url)).status_code == 401, url


async def test_tenant_isolation(client, world):
    w = await _world(client, world)
    a_ctx, asset = w["m"], w["asset"]
    b = await make_maint_tenant("RB")
    for role in ("admin", "maintenance_planner"):
        h = b.h(role)
        assert w["plan"]["id"] not in {p["id"] for p in await api(client, "GET", "/api/maintenance-plans/", h)}
        assert all(r["plan_id"] != w["plan"]["id"] for r in await api(client, "GET", "/api/maintenance-plans/status", h))
        assert await api(client, "GET", "/api/work-orders/", h) == []
        assert await api(client, "GET", "/api/failures/", h) == []
        for url in (f"/api/maintenance-plans/{w['plan']['id']}", f"/api/work-orders/{w['wo']['id']}",
                    f"/api/failures/{w['failure']['id']}", f"/api/users/{a_ctx.users['technician']['id']}"):
            assert (await client.get(url, headers=h)).status_code == 404, url
        r = await client.post(f"/api/work-orders/{w['wo']['id']}/transition", headers=h, json={"to": "approved"})
        assert r.status_code == 404
        for method, url, body in (
                ("PATCH", f"/api/maintenance-plans/{w['plan']['id']}", {"name": "hack"}),
                ("PATCH", f"/api/work-orders/{w['wo']['id']}", {"title": "hack"}),
                ("POST", f"/api/work-orders/{w['wo']['id']}/labor", {"hours": 1}),
                ("PATCH", f"/api/work-orders/{w['wo']['id']}/tasks", {"task_id": "x", "done": True}),
                ("POST", f"/api/work-orders/{w['wo']['id']}/replacements",
                 {"position_id": asset["position_id"], "removal_reason": "other", "new_component": {}}),
                ("PATCH", f"/api/failures/{w['failure']['id']}", {"note": "hack"}),
                ("DELETE", f"/api/maintenance-plans/{w['plan']['id']}", None)):
            r = await client.request(method, url, headers=h, json=body)
            assert r.status_code in (403, 404), (method, url, r.status_code)
        # references to A's assets from B are refused
        for body in ({"type": "corrective", "state": "open", "title": "x", "machine_id": asset["machine_id"]},
                     {"type": "corrective", "state": "open", "title": "x", "position_id": asset["position_id"]},
                     {"type": "preventive", "state": "draft", "title": "x", "plan_id": w["plan"]["id"]},
                     {"type": "corrective", "state": "open", "title": "x", "machine_id": b.machines["Robô de Solda"],
                      "failure_id": w["failure"]["id"]}):
            assert (await client.post("/api/work-orders/", headers=h, json=body)).status_code == 404, body
        r = await client.post("/api/maintenance-plans/", headers=h, json={"name": "x", "position_id": asset["position_id"],
                                                                          "interval_days": 3})
        assert r.status_code == 404
        r = await client.post("/api/failures/", headers=h, json={"machine_id": asset["machine_id"],
                                                                 "failed_at": "2026-01-01T00:00:00Z"})
        assert r.status_code == 404
        r = await client.post("/api/maintenance/absences", headers=h, json={
            "user_id": a_ctx.users["technician"]["id"], "date": "2026-01-01", "hours": 1})
        assert r.status_code == 404
    # KPIs/reliability/components of B contain nothing of A
    h = b.h("manager")
    k = await api(client, "GET", "/api/maintenance/kpis", h)
    assert k["backlog_count"] == 0 and k["pmp"] is None and k["requests_pending"] == []
    rel = await api(client, "GET", "/api/maintenance/reliability", h, params={"group_by": "position"})
    assert asset["position_id"] not in {r.get("position_id") for r in rel["rows"]}
    for g in ("manufacturer", "manufacturer_model", "component_type", "machine"):
        rel = await api(client, "GET", "/api/maintenance/reliability", h, params={"group_by": g})
        assert all(r["n"] == 0 for r in rel["rows"]) and all((r.get("components") or 0) == 0 for r in rel["rows"])
    assert await api(client, "GET", "/api/maintenance/components", h) == []
    r = await client.get("/api/maintenance/kpis", headers=h, params={"plant_id": a_ctx.plant_id})
    assert r.status_code == 404
