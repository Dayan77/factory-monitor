"""Sprint 4 maintenance API: work-order state machine and audit trail (S4-5), roles (maintenance_workflows §1),
component replacement semantics (§5.4 / S4-2), plan engine single draft (S4-4), ISO 14224 failures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import api, make_maint_tenant, seed_usage, ts
from src.services.maintenance import core

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


async def _cylinder(ctx, name="Cilindro X", total=1_000_000, recent=140_000, nominal=5_000_000,
                    machine="Estação de Fixação", serial=None) -> dict:
    """A cylinder position with an installed component, usage total and 14-day usage (rate 10 000/day)."""
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType, Position
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=ctx.machines[machine], name=name,
                     component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        c = Component(tenant_id=ctx.id, component_type=ComponentType.pneumatic_cylinder, manufacturer="Festo",
                      model="DSBC", serial_number=serial or f"S-{p.id[:6]}", nominal_life={"cycles": nominal},
                      installed_at=NOW() - timedelta(days=200), position_id=p.id, last_position_id=p.id)
        s.add(c)
        await s.flush()
        b = (NOW() - timedelta(days=2)).replace(minute=0, second=0, microsecond=0).isoformat()
        # the position has had data for more than 14 days (audit A-3: the plan pace divides by the days with data)
        first = (NOW() - timedelta(days=30)).replace(minute=0, second=0, microsecond=0).isoformat()
        await seed_usage(s, ctx.id, "position", p.id, {"cycles": total}, [(first, {"cycles": 0}), (b, {"cycles": recent})])
        await seed_usage(s, ctx.id, "component", c.id, {"cycles": total - 100_000}, [(b, {"cycles": recent})])
        await s.commit()
        return {"position_id": p.id, "component_id": c.id, "machine_id": ctx.machines[machine]}


@pytest.fixture
async def m(client):
    return await make_maint_tenant("API")


# ── state machine (pure) ────────────────────────────────────────────────────

def test_state_machine_table():
    assert core.allowed_targets("draft", "preventive", None) == ["open", "cancelled"]
    assert core.allowed_targets("open", "preventive", None) == ["approved", "cancelled"]
    assert core.allowed_targets("open", "corrective", "emergency") == ["approved", "in_progress", "cancelled"]
    assert core.allowed_targets("open", "corrective", "deferred") == ["approved", "cancelled"]
    assert core.allowed_targets("approved", "improvement", None) == ["scheduled", "cancelled"]
    assert core.allowed_targets("scheduled", "preventive", None) == ["in_progress", "cancelled"]
    assert core.allowed_targets("in_progress", "preventive", None) == ["on_hold", "completed", "cancelled"]
    assert core.allowed_targets("on_hold", "preventive", None) == ["scheduled", "in_progress", "cancelled"]
    assert core.allowed_targets("completed", "preventive", None) == ["closed"]
    assert core.allowed_targets("closed", "preventive", None) == []
    assert core.allowed_targets("cancelled", "preventive", None) == []
    assert core.transition_error("draft", "completed", "preventive", None, None)
    assert core.transition_error("completed", "in_progress", "predictive", None, None)
    assert core.transition_error("closed", "cancelled", "preventive", None, "other")
    assert core.transition_error("open", "in_progress", "preventive", None, None)
    assert core.transition_error("approved", "cancelled", "corrective", "deferred", None)
    assert core.transition_error("approved", "cancelled", "corrective", "deferred", "because")
    assert core.transition_error("approved", "cancelled", "corrective", "deferred", "superseded") is None
    assert core.transition_error("open", "in_progress", "corrective", "emergency", None) is None


def test_plan_status_whichever_first_kpis_u4():
    """kpis.md U4: 2 000 000 cycles or 180 days; 1 700 000 cycles and 60 days; 20 000/day → 15 days, cycles."""
    from datetime import date
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Manaus")
    st = core.compute_plan_status({"cycles": 2_000_000, "days": 180}, datetime(2026, 8, 6, 15, tzinfo=timezone.utc),
                                  {"cycles": 1_000_000}, {"cycles": 2_700_000}, {"cycles": 280_000},
                                  date(2026, 10, 5), tz)
    assert st.state == "due_soon" and abs(st.consumed - 0.85) < 1e-9 and st.consumed_trigger == "cycles"
    assert st.days_to_due == 15 and st.next_due_date.isoformat() == "2026-10-20" and st.due_trigger == "cycles"
    # no recent usage: the calendar still projects; nothing projects → null + reason
    st = core.compute_plan_status({"cycles": 2_000_000}, datetime(2026, 8, 6, 15, tzinfo=timezone.utc),
                                  {"cycles": 0}, {"cycles": 100}, {}, date(2026, 10, 5), tz)
    assert st.next_due_date is None and st.reason == "no_recent_usage"


# ── lifecycle, roles and audit trail ────────────────────────────────────────

async def test_work_order_lifecycle_roles_and_audit(client, m):
    asset = await _cylinder(m)
    pl, tech, sup = m.h("maintenance_planner"), m.h("technician"), m.h("production_supervisor")
    tech_id = m.users["technician"]["id"]
    wo = await api(client, "POST", "/api/work-orders/", pl, 201, json={
        "type": "preventive", "state": "open", "title": "Lubrificar", "position_id": asset["position_id"],
        "estimated_hours": 3, "task_list": [{"text": "limpar"}, {"text": "lubrificar"}]})
    url = f"/api/work-orders/{wo['id']}"
    assert wo["number"].startswith("OS-") and wo["allowed_transitions"] == ["approved", "cancelled"]
    assert wo["machine_id"] == asset["machine_id"] and len(wo["task_list"]) == 2

    # technician cannot approve; supervisor cannot schedule
    r = await client.post(url + "/transition", headers=tech, json={"to": "approved"})
    assert r.status_code == 403
    await api(client, "POST", url + "/transition", pl, json={"to": "approved"})
    r = await client.post(url + "/transition", headers=sup, json={"to": "scheduled", "scheduled_for": "2030-01-10"})
    assert r.status_code == 403
    r = await client.post(url + "/transition", headers=pl, json={"to": "scheduled"})
    assert r.status_code == 422                                             # scheduled_for required
    d = await api(client, "POST", url + "/transition", pl,
                  json={"to": "scheduled", "scheduled_for": "2030-01-10", "assignee_id": tech_id})
    scheduled_at = d["scheduled_at"]
    assert d["assignee_id"] == tech_id and d["scheduled_for"] == "2030-01-10"
    # reprogramming keeps scheduled_at (freeze base, S4-5) and notifies the technician
    d = await api(client, "PATCH", url, pl, json={"scheduled_for": "2030-01-11"})
    assert d["scheduled_for"] == "2030-01-11" and d["scheduled_at"] == scheduled_at
    n = await api(client, "GET", "/api/notifications/", tech)
    assert {"wo_assigned", "wo_rescheduled"} <= {i["kind"] for i in n["items"]}

    # technician executes; planner cannot complete nor log hours
    await api(client, "POST", url + "/transition", tech, json={"to": "in_progress"})
    r = await client.post(url + "/transition", headers=tech, json={"to": "on_hold"})
    assert r.status_code == 422                                             # hold_reason required
    d = await api(client, "POST", url + "/transition", tech,
                  json={"to": "on_hold", "hold_reason": "aguardando peça", "labor_hours": 1.0, "remaining_hours": 1.5})
    assert d["labor_hours"] == 1.0 and d["remaining_hours"] == 1.5 and d["hold_reason"] == "aguardando peça"
    await api(client, "POST", url + "/transition", pl, json={"to": "scheduled"})          # peça chegou
    await api(client, "POST", url + "/transition", tech, json={"to": "in_progress"})
    r = await client.post(url + "/labor", headers=pl, json={"hours": 1})
    assert r.status_code == 403
    d = await api(client, "POST", url + "/labor", tech, 201, json={"hours": 0.5, "note": "lubrificação"})
    assert d["labor_hours"] == 1.5 and d["labor"][-1]["user_id"] == tech_id
    task = d["task_list"][0]["id"]
    d = await api(client, "PATCH", url + "/tasks", tech, json={"task_id": task, "done": True, "result": "ok"})
    assert d["task_list"][0]["done"] is True
    r = await client.post(url + "/transition", headers=pl, json={"to": "completed"})
    assert r.status_code == 403
    d = await api(client, "POST", url + "/transition", tech, json={"to": "completed", "labor_hours": 0.5})
    assert d["labor_hours"] == 2.0 and d["completed_at"]
    r = await client.post(url + "/transition", headers=tech, json={"to": "closed"})
    assert r.status_code == 403
    d = await api(client, "POST", url + "/transition", sup, json={"to": "closed", "note": "verificado"})
    assert d["closed_at"] and d["allowed_transitions"] == []
    # closed never reopens nor cancels (422 with the allowed list)
    for to, extra in (("in_progress", {}), ("cancelled", {"cancel_reason": "other"})):
        r = await client.post(url + "/transition", headers=m.h("admin"), json={"to": to, **extra})
        assert r.status_code == 422 and r.json()["detail"]["allowed"] == []
    # audit trail: every transition with who/when/from/to
    hist = d["history"]
    assert [(h["from_state"], h["to_state"]) for h in hist] == [
        (None, "open"), ("open", "approved"), ("approved", "scheduled"), ("scheduled", "in_progress"),
        ("in_progress", "on_hold"), ("on_hold", "scheduled"), ("scheduled", "in_progress"),
        ("in_progress", "completed"), ("completed", "closed")]
    assert all(h["by"] and h["at"] for h in hist) and hist[-1]["note"] == "verificado"
    assert hist[3]["by"] == tech_id and hist[1]["by"] == m.users["maintenance_planner"]["id"]


async def test_invalid_transitions_answer_422_with_allowed(client, m):
    asset = await _cylinder(m)
    pl = m.h("maintenance_planner")
    wo = await api(client, "POST", "/api/work-orders/", pl, 201, json={
        "type": "preventive", "state": "draft", "title": "x", "position_id": asset["position_id"]})
    url = f"/api/work-orders/{wo['id']}/transition"
    r = await client.post(url, headers=pl, json={"to": "completed"})
    assert r.status_code == 422 and r.json()["detail"]["allowed"] == ["open", "cancelled"]
    r = await client.post(url, headers=pl, json={"to": "cancelled"})
    assert r.status_code == 422                                        # cancel_reason required
    r = await client.post(url, headers=pl, json={"to": "cancelled", "cancel_reason": "porque sim"})
    assert r.status_code == 422
    await api(client, "POST", url, pl, json={"to": "open"})
    r = await client.post(url, headers=m.h("admin"), json={"to": "in_progress"})
    assert r.status_code == 422 and "emergencial" in r.json()["detail"]["message"]
    r = await client.post(url, headers=pl, json={"to": "bogus"})
    assert r.status_code == 422
    # `at` in the future or before the last change → 422
    r = await client.post(url, headers=pl, json={"to": "approved", "at": (NOW() + timedelta(hours=1)).isoformat()})
    assert r.status_code == 422
    r = await client.post(url, headers=pl, json={"to": "approved", "at": "2000-01-01T00:00:00Z"})
    assert r.status_code == 422


async def test_approve_draft_and_emergency_shortcut(client, m):
    asset = await _cylinder(m)
    pl, tech = m.h("maintenance_planner"), m.h("technician")
    wo = await api(client, "POST", "/api/work-orders/", pl, 201, json={
        "type": "predictive", "state": "draft", "title": "Vibração", "position_id": asset["position_id"]})
    d = await api(client, "POST", f"/api/work-orders/{wo['id']}/approve-draft", pl)
    assert d["state"] == "approved" and [h["to_state"] for h in d["history"]] == ["draft", "open", "approved"]
    # technician opens an emergency corrective and goes straight to in_progress
    e = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "Vazamento",
        "position_id": asset["position_id"]})
    d = await api(client, "POST", f"/api/work-orders/{e['id']}/transition", tech, json={"to": "in_progress"})
    assert d["state"] == "in_progress" and d["assignee_id"] == m.users["technician"]["id"]
    # technician may not open a draft nor a preventive
    r = await client.post("/api/work-orders/", headers=tech, json={"type": "preventive", "state": "open", "title": "x",
                                                                   "position_id": asset["position_id"]})
    assert r.status_code == 403


async def test_operator_request_visibility_and_hours_hidden(client, m):
    asset = await _cylinder(m)
    op = m.h("operator")
    r = await client.post("/api/work-orders/", headers=op, json={"type": "corrective", "state": "draft", "title": "x",
                                                                 "machine_id": asset["machine_id"]})
    assert r.status_code == 403
    mine = await api(client, "POST", "/api/work-orders/", op, 201, json={
        "type": "corrective", "state": "open", "title": "Defeito encontrado: ruído", "machine_id": asset["machine_id"],
        "estimated_hours": 2})
    assert mine["urgency"] == "deferred" and mine["estimated_hours"] is None and mine["labor_hours"] is None
    other = await api(client, "POST", "/api/work-orders/", m.h("maintenance_planner"), 201, json={
        "type": "improvement", "state": "open", "title": "outra", "machine_id": asset["machine_id"]})
    ids = {w["id"] for w in await api(client, "GET", "/api/work-orders/", op)}
    assert mine["id"] in ids and other["id"] not in ids
    r = await client.get(f"/api/work-orders/{other['id']}", headers=op)
    assert r.status_code == 404


# ── §5.4 / S4-2: component replacement ──────────────────────────────────────

async def test_component_replacement_semantics(client, m):
    asset = await _cylinder(m, total=3_000_000)
    pl, tech = m.h("maintenance_planner"), m.h("technician")
    pos, old_id = asset["position_id"], asset["component_id"]
    base = {"wo_type": "preventive", "estimated_hours": 1, "last_done_usage": {"cycles": 2_500_000},
            "last_done_at": (NOW() - timedelta(days=30)).isoformat()}
    reset_plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        **base, "name": "vedações", "position_id": pos, "interval_cycles": 2_000_000})
    keep_plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        **base, "name": "inspeção visual", "position_id": pos, "interval_cycles": 2_000_000, "reset_on_replacement": False})
    machine_plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "NR-12", "machine_id": asset["machine_id"], "interval_days": 30,
        "last_done_at": (NOW() - timedelta(days=3)).isoformat()})
    wo = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "troca", "position_id": pos})
    url = f"/api/work-orders/{wo['id']}"
    await api(client, "POST", url + "/transition", tech, json={"to": "in_progress"})
    d = await api(client, "POST", url + "/replacements", tech, 201, json={
        "position_id": pos, "removal_reason": "failure",
        "new_component": {"manufacturer": "SMC", "model": "CP96", "serial_number": "SMC-NEW-1",
                          "nominal_life": {"cycles": 6_000_000}}})
    rep = d["replacements"][0]
    assert rep["removed_component_id"] == old_id and rep["applied_at"] is None
    # not applied before completion
    old = await api(client, "GET", f"/api/components/{old_id}", pl)
    assert old["position_id"] == pos and old["removed_at"] is None
    r = await client.post(url + "/replacements", headers=tech, json={"position_id": pos, "removal_reason": "other",
                                                                      "new_component": {"serial_number": "dup"}})
    assert r.status_code == 409                                                 # one swap per position per WO
    done = await api(client, "POST", url + "/transition", tech, json={"to": "completed", "labor_hours": 1})
    at = ts(done["completed_at"])
    assert done["plans_reset"] == [reset_plan["id"]]
    old = await api(client, "GET", f"/api/components/{old_id}", pl)
    assert ts(old["removed_at"]) == at and old["position_id"] is None and old["removal_reason"] == "failure"
    assert old["usage_since_install"]["cycles"] == 2_900_000                     # keeps its age at removal
    new = await api(client, "GET", f"/api/components/{rep['installed_component_id']}", pl)
    assert ts(new["installed_at"]) == at and new["position_id"] == pos and new["usage_since_install"]["cycles"] == 0
    p = await api(client, "GET", f"/api/positions/{pos}", pl)
    assert p["usage"]["cycles_total"] == 3_000_000                                 # position total keeps counting
    p1 = await api(client, "GET", f"/api/maintenance-plans/{reset_plan['id']}", pl)
    assert ts(p1["last_reset_at"]) == at and p1["last_reset_usage"]["cycles"] == 3_000_000 and p1["state"] == "ok"
    p2 = await api(client, "GET", f"/api/maintenance-plans/{keep_plan['id']}", pl)
    assert p2["last_reset_usage"]["cycles"] == 2_500_000
    p3 = await api(client, "GET", f"/api/maintenance-plans/{machine_plan['id']}", pl)
    assert p3["last_reset_at"] == machine_plan["last_reset_at"]
    # history stays: the position lists both components; nothing deleted
    comps = await api(client, "GET", "/api/components/", pl, params={"position_id": pos})
    assert {c["id"] for c in comps} >= {old_id, new["id"]}
    # no swap after completion; a stock component installed elsewhere is refused
    r = await client.post(url + "/replacements", headers=tech, json={"position_id": pos, "removal_reason": "other",
                                                                      "new_component": {"serial_number": "late"}})
    assert r.status_code == 422
    wo2 = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "troca 2", "position_id": pos})
    r = await client.post(f"/api/work-orders/{wo2['id']}/replacements", headers=tech,
                          json={"position_id": pos, "removal_reason": "upgrade", "new_component_id": old_id})
    assert r.status_code == 409


# ── S4-4: one draft per plan at ≥ 80 % ───────────────────────────────────────

async def test_plan_engine_single_draft_and_recreation(client, m):
    asset = await _cylinder(m, total=1_000_000)
    pl = m.h("maintenance_planner")
    # 850 000 / 1 000 000 = 0.85 → due_soon → draft created by the engine on creation
    plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "vedações", "position_id": asset["position_id"], "interval_cycles": 1_000_000, "estimated_hours": 1.5,
        "task_list": [{"text": "trocar vedações"}], "last_done_usage": {"cycles": 150_000},
        "last_done_at": (NOW() - timedelta(days=40)).isoformat()})
    low = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "baixo", "position_id": asset["position_id"], "interval_cycles": 10_000_000,
        "last_done_usage": {"cycles": 0}})
    assert plan["state"] == "due_soon" and low["state"] == "ok"
    for _ in range(2):
        await api(client, "POST", "/api/maintenance-plans/recompute", pl)
    wos = await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": plan["id"]})
    assert len(wos) == 1 and wos[0]["state"] == "draft" and wos[0]["estimated_hours"] == 1.5
    assert wos[0]["task_list"][0]["text"] == "trocar vedações" and wos[0]["type"] == "preventive"
    assert await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": low["id"]}) == []
    rows = {r["plan_id"]: r for r in await api(client, "GET", "/api/maintenance-plans/status", pl)}
    assert rows[plan["id"]]["open_work_order_id"] == wos[0]["id"]
    assert rows[plan["id"]]["projected_date"] is not None and rows[plan["id"]]["days_to_due"] == 15
    # a second WO for the plan is refused
    r = await client.post("/api/work-orders/", headers=pl, json={"type": "preventive", "state": "open", "title": "dup",
                                                                 "plan_id": plan["id"]})
    assert r.status_code == 409
    # cancelling the draft re-creates one at once (never zero, never two)
    d = await api(client, "POST", f"/api/work-orders/{wos[0]['id']}/transition", pl,
                  json={"to": "cancelled", "cancel_reason": "production_conflict"})
    assert d["new_draft_id"]
    open_wos = [w for w in await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": plan["id"]})
                if w["state"] in core.OPEN_STATES]
    assert [w["id"] for w in open_wos] == [d["new_draft_id"]]
    # completing the plan's WO resets the plan (§5.4) → ok, no new draft
    url = f"/api/work-orders/{d['new_draft_id']}"
    await api(client, "POST", url + "/approve-draft", pl)
    await api(client, "POST", url + "/transition", pl, json={"to": "scheduled", "scheduled_for": "2030-01-01",
                                                             "assignee_id": m.users["technician"]["id"]})
    await api(client, "POST", url + "/transition", m.h("technician"), json={"to": "in_progress"})
    await api(client, "POST", url + "/transition", m.h("technician"), json={"to": "completed", "labor_hours": 1})
    p = await api(client, "GET", f"/api/maintenance-plans/{plan['id']}", pl)
    assert p["state"] == "ok" and p["consumed"] == 0 and p["last_reset_usage"]["cycles"] == 1_000_000
    await api(client, "POST", "/api/maintenance-plans/recompute", pl)
    open_wos = [w for w in await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": plan["id"]})
                if w["state"] in core.OPEN_STATES]
    assert open_wos == []
    # the planner was notified (grouped digest kind)
    n = await api(client, "GET", "/api/notifications/", pl)
    due_soon = [i for i in n["items"] if i["kind"] == "plan_due_soon"]
    assert len(due_soon) == 1 and due_soon[0]["count"] == 2                     # grouped: draft + re-created draft?


async def test_plan_validation(client, m):
    asset = await _cylinder(m)
    pl = m.h("maintenance_planner")
    r = await client.post("/api/maintenance-plans/", headers=pl, json={"name": "x", "position_id": asset["position_id"]})
    assert r.status_code == 422                                                 # no trigger
    r = await client.post("/api/maintenance-plans/", headers=pl, json={"name": "x", "interval_days": 7})
    assert r.status_code == 422                                                 # no anchor
    r = await client.post("/api/maintenance-plans/", headers=pl, json={
        "name": "x", "interval_days": 7, "position_id": asset["position_id"], "machine_id": m.machines["Robô de Solda"]})
    assert r.status_code == 422                                                 # position of another machine
    p = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "x", "interval_days": 7, "machine_id": asset["machine_id"]})
    p = await api(client, "PATCH", f"/api/maintenance-plans/{p['id']}", pl, json={"interval_hours": 100, "name": "y"})
    assert p["interval_hours"] == 100 and p["name"] == "y"
    r = await client.patch(f"/api/maintenance-plans/{p['id']}", headers=pl, json={"interval_days": None, "interval_hours": None})
    assert r.status_code == 422
    await api(client, "DELETE", f"/api/maintenance-plans/{p['id']}", pl, 204)
    assert (await api(client, "GET", f"/api/maintenance-plans/{p['id']}", pl))["is_active"] is False


# ── ISO 14224 failures ──────────────────────────────────────────────────────

async def test_failures_iso14224(client, m):
    asset = await _cylinder(m)
    tech, pl = m.h("technician"), m.h("maintenance_planner")
    codes = await api(client, "GET", "/api/failure-codes/", m.h("operator"))
    assert {"ELP", "INL", "PDE", "FTO", "FTC", "UNK"} <= {c["code"] for c in codes["failure_modes"]}
    assert {"6.4", "2.4", "4.5"} <= {c["code"] for c in codes["mechanisms"]}
    assert {"5.5", "3.4"} <= {c["code"] for c in codes["causes"]}
    assert "continuous_condition_monitoring" in {c["code"] for c in codes["detection_methods"]}
    assert "ELP" in codes["modes_by_component_type"]["pneumatic_cylinder"]
    base = {"machine_id": asset["machine_id"], "position_id": asset["position_id"],
            "failed_at": (NOW() - timedelta(days=1)).isoformat(), "severity": "degraded"}
    r = await client.post("/api/failures/", headers=tech, json={**base, "failure_mode": "XYZ"})
    assert r.status_code == 422
    r = await client.post("/api/failures/", headers=tech, json={**base, "position_id": asset["position_id"],
                                                                "machine_id": m.machines["Robô de Solda"]})
    assert r.status_code == 422
    r = await client.post("/api/failures/", headers=tech, json={**base, "restored_at": (NOW() - timedelta(days=2)).isoformat()})
    assert r.status_code == 422
    # found in a preventive, no stop: counts in MTBF, not in MTTR (S4-10); component attributed (S4-9)
    f = await api(client, "POST", "/api/failures/", tech, 201, json={
        **base, "failure_mode": "INL", "detection_method": "periodic_maintenance"})
    assert f["component_id"] == asset["component_id"] and f["status"] == "draft" and f["restored_at"] is None
    assert f["usage_at_failure"]["position_total"]["cycles"] <= 1_000_000
    f = await api(client, "PATCH", f"/api/failures/{f['id']}", pl, json={"failure_mechanism": "2.4", "failure_cause": "3.4"})
    assert f["status"] == "complete" and f["mechanism"] == "2.4" and f["cause"] == "3.4"
    lst = await api(client, "GET", "/api/failures/", pl, params={"position_id": asset["position_id"]})
    assert [x["id"] for x in lst] == [f["id"]]
    rel = await api(client, "GET", "/api/maintenance/reliability", pl, params={
        "group_by": "position", "start": (NOW() - timedelta(days=30)).isoformat(), "stop": NOW().isoformat()})
    row = next(r for r in rel["rows"] if r["position_id"] == asset["position_id"])
    assert row["n"] == 1 and row["restored"] == 0 and row["mttr_min"] is None and row["mttr_reason"] == "no_restored_failures"
    assert row["mtbf_h"] == 0.0 or row["mtbf_h"] is not None


async def test_offline_replay_is_idempotent(client, m):
    """The technician app queues actions offline and replays them with X-Client-Action-Id: applied once."""
    asset = await _cylinder(m)
    tech = m.h("technician")
    wo = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "offline",
        "position_id": asset["position_id"]})
    url = f"/api/work-orders/{wo['id']}"
    h = {**tech, "X-Client-Action-Id": "act-1"}
    d = await api(client, "POST", url + "/transition", h, json={"to": "in_progress"})
    again = await api(client, "POST", url + "/transition", h, json={"to": "in_progress"})
    assert again["replayed"] is True and len(again["history"]) == len(d["history"])
    h2 = {**tech, "X-Client-Action-Id": "act-2"}
    for _ in range(2):
        d = await api(client, "POST", url + "/labor", h2, 201, json={"hours": 1.5})
    assert d["labor_hours"] == 1.5 and len(d["labor"]) == 1
    d = await api(client, "POST", url + "/labor", {**tech, "X-Client-Action-Id": "act-3"}, 201, json={"hours": 0.5})
    assert d["labor_hours"] == 2.0


async def test_plan_pace_uses_the_days_with_data_audit_A3(client, m):
    """audit A-3: a position connected 5 days ago (100 000 cycles, 20 000/day). The pace was always ÷ 14
    (290 000 left / 7 143 per day → 41 days); it must divide by the ~5 days with data (→ 15) and flag low_confidence."""
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=m.id, machine_id=m.machines["Estação de Fixação"], name="Cilindro A3",
                     component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        h0 = NOW().replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
        hours = [h0 + timedelta(hours=h) for h in range(0, 120, 24)]
        await seed_usage(s, m.id, "position", p.id, {"cycles": 1_710_000},
                         [(b.isoformat(), {"cycles": 20_000}) for b in hours])
        await s.commit()
        pid = p.id
    pl = m.h("maintenance_planner")
    plan = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "U4", "position_id": pid, "interval_cycles": 2_000_000, "last_done_usage": {"cycles": 0}})
    rows = {r["plan_id"]: r for r in await api(client, "GET", "/api/maintenance-plans/status", pl)}
    row = rows[plan["id"]]
    assert row["days_to_due"] == 15 and row["low_confidence"] is True
