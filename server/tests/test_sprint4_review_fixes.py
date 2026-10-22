"""Sprint 4 review/QA fixes: plan projection horizon and engine isolation, automatic emergency WO, stock
component reserved by one replacement, Web Push SSRF guard, webhook DNS pinning, API-token audit trail."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from maint_helpers import api, make_maint_tenant, seed_usage
from src.services.maintenance import core

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
TZ = ZoneInfo("America/Manaus")


@pytest.fixture
async def m(client):
    return await make_maint_tenant("FIX")


# ── plan projection: bounded, never a date overflow ─────────────────────────

def test_projection_beyond_horizon_is_undated_with_reason():
    st = core.compute_plan_status({"hours": 5000}, datetime(2026, 10, 1, tzinfo=timezone.utc), {},
                                  {"hours": 0.01}, {"hours": 0.001}, date(2026, 10, 5), TZ)
    assert st.days_to_due is None and st.next_due_date is None and st.reason == "beyond_horizon"
    assert st.triggers[0].days is None and st.triggers[0].projected_date is None
    # a reachable pace still projects normally
    ok = core.compute_plan_status({"hours": 5000}, datetime(2026, 10, 1, tzinfo=timezone.utc), {},
                                  {"hours": 10}, {"hours": 140}, date(2026, 10, 5), TZ)
    assert ok.days_to_due == 499 and ok.reason is None
    # no pace at all keeps its own reason
    none = core.compute_plan_status({"hours": 5000}, datetime(2026, 10, 1, tzinfo=timezone.utc), {},
                                    {"hours": 10}, {}, date(2026, 10, 5), TZ)
    assert none.reason == "no_recent_usage"


def test_huge_calendar_interval_does_not_overflow():
    st = core.compute_plan_status({"days": 10**9}, datetime(2026, 10, 1, tzinfo=timezone.utc), {}, {}, {},
                                  date(2026, 10, 5), TZ)
    assert st.state == "ok" and st.days_to_due is None and st.reason == "beyond_horizon"
    # combined with a dated trigger: the dated one wins
    st2 = core.compute_plan_status({"days": 10**9, "cycles": 100}, datetime(2026, 10, 1, tzinfo=timezone.utc), {},
                                   {"cycles": 10}, {"cycles": 14}, date(2026, 10, 5), TZ)
    assert st2.days_to_due == 90 and st2.due_trigger == "cycles"


def test_rul_beyond_horizon_has_no_date():
    from src.services.usage import core as uc
    r = uc.rul(date(2026, 10, 5), {"hours": 5000}, {"hours": 1}, {"hours": 0.0001}, 14)
    assert r.date is None and r.days is None and r.reason == "beyond_horizon"


async def test_one_failing_plan_does_not_stop_the_others_nor_the_emergency_call(client, m, monkeypatch):
    """A plan whose evaluation raises is logged and skipped: the listing still answers, the other plans get their
    draft, and the engine's emergency stage still runs in the same tick."""
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, FailureRecord, WorkOrder
    from src.services.maintenance import engine, service
    pl = m.h("maintenance_planner")
    bad = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "ruim", "machine_id": m.machines["Robô de Solda"], "interval_days": 30,
        "last_done_at": (NOW() - timedelta(days=29)).isoformat()})
    good = await api(client, "POST", "/api/maintenance-plans/", pl, 201, json={
        "name": "bom", "machine_id": m.machines["Estação de Fixação"], "interval_days": 10,
        "last_done_at": (NOW() - timedelta(days=9)).isoformat()})
    # undo the drafts made on creation so the engine has work to do
    async with AsyncSessionLocal() as s:
        for w in (await s.execute(WorkOrder.__table__.select().where(WorkOrder.tenant_id == m.id))).all():
            await s.execute(WorkOrder.__table__.delete().where(WorkOrder.__table__.c.id == w.id))
        ev = DowntimeEvent(tenant_id=m.id, machine_id=m.machines["Robô de Solda"], start=NOW() - timedelta(minutes=5),
                           source="auto_fault", suggested_reason_code="EQP-01", stop_start=NOW() - timedelta(minutes=5))
        s.add(ev)
        await s.flush()
        s.add(FailureRecord(tenant_id=m.id, machine_id=ev.machine_id, downtime_event_id=ev.id, failed_at=ev.start,
                            severity="critical", status="draft"))
        await s.commit()

    real = core.compute_plan_status

    def boom(intervals, *a, **kw):
        if intervals.get("days") == 30:
            raise OverflowError("date value out of range")
        return real(intervals, *a, **kw)
    monkeypatch.setattr(service.core, "compute_plan_status", boom)

    r = await client.get("/api/maintenance-plans/status", headers=pl, params={"plant_id": m.plant_id})
    assert r.status_code == 200, r.text
    assert {p["plan_id"] for p in r.json()} >= {good["id"]}
    async with AsyncSessionLocal() as s:
        out = await engine.tick_tenant(s, m.id, force_plans=True)
    assert out["emergency"] == 1
    drafts = await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": good["id"]})
    assert len(drafts) == 1 and drafts[0]["state"] == "draft"
    assert await api(client, "GET", "/api/work-orders/", pl, params={"plan_id": bad["id"]}) == []

    # even if the whole plan stage blows up, emergency calls and escalations still run
    async def explode(*a, **kw):
        raise RuntimeError("plan engine down")
    monkeypatch.setattr(engine, "run_plan_engine", explode)
    async with AsyncSessionLocal() as s:
        ev2 = DowntimeEvent(tenant_id=m.id, machine_id=m.machines["Transportador de Saída"],
                            start=NOW() - timedelta(minutes=5), source="auto_fault", suggested_reason_code="EQP-01",
                            stop_start=NOW() - timedelta(minutes=5))
        s.add(ev2)
        await s.flush()
        s.add(FailureRecord(tenant_id=m.id, machine_id=ev2.machine_id, downtime_event_id=ev2.id, failed_at=ev2.start,
                            severity="critical", status="draft"))
        await s.commit()
    async with AsyncSessionLocal() as s:
        out = await engine.tick_tenant(s, m.id, force_plans=True)
    assert out["plans"] == 0 and out["emergency"] == 1


# ── automatic emergency call carries a WO the technician can take on the phone ─

async def test_engine_emergency_call_creates_the_emergency_wo(client, m):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, FailureRecord
    from src.services.maintenance import engine
    mid = m.machines["Robô de Solda"]
    async with AsyncSessionLocal() as s:
        ev = DowntimeEvent(tenant_id=m.id, machine_id=mid, start=NOW() - timedelta(minutes=3), source="auto_fault",
                           suggested_reason_code="EQP-01", stop_start=NOW() - timedelta(minutes=3))
        s.add(ev)
        await s.flush()
        f = FailureRecord(tenant_id=m.id, machine_id=mid, downtime_event_id=ev.id, failed_at=ev.start,
                          severity="critical", status="draft")
        s.add(f)
        await s.commit()
        fid, evid = f.id, ev.id
    async with AsyncSessionLocal() as s:
        assert (await engine.tick_tenant(s, m.id))["emergency"] == 1
    tech = m.h("technician")
    # the technician's emergency list (/m: corrective, open, urgency emergency) shows it
    open_em = [w for w in await api(client, "GET", "/api/work-orders/", tech, params={"type": "corrective", "state": "open"})
               if w["urgency"] == "emergency"]
    assert len(open_em) == 1
    wo = open_em[0]
    assert wo["downtime_event_id"] == evid and wo["failure_id"] == fid
    n = (await api(client, "GET", "/api/notifications/", tech))["items"][0]
    assert n["kind"] == "emergency_call" and n["link"] == f"/m/work-orders/{wo['id']}"
    assert n["data"]["work_order_id"] == wo["id"]
    f2 = await api(client, "GET", f"/api/failures/{fid}", tech)
    assert f2["work_order_id"] == wo["id"]
    # "Assumir" on the phone
    d = await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", tech,
                  json={"to": "in_progress", "assignee_id": m.users["technician"]["id"]})
    assert d["state"] == "in_progress"
    # the supervisor pressing "Chamar manutenção" afterwards gets the same WO (one call per stop)
    again = await api(client, "POST", "/api/work-orders/emergency-call", m.h("production_supervisor"), 201,
                      json={"downtime_event_id": evid})
    assert again["id"] == wo["id"]
    async with AsyncSessionLocal() as s:
        assert (await engine.tick_tenant(s, m.id))["emergency"] == 0


# ── a stock component is reserved by one pending replacement ────────────────

async def _position(ctx, name, machine="Estação de Fixação"):
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType, Position
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=ctx.machines[machine], name=name,
                     component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        c = Component(tenant_id=ctx.id, component_type=ComponentType.pneumatic_cylinder, manufacturer="Festo",
                      model="DSBC", serial_number=f"S-{p.id[:6]}", nominal_life={"cycles": 5_000_000},
                      installed_at=NOW() - timedelta(days=100), position_id=p.id, last_position_id=p.id)
        s.add(c)
        await s.commit()
        return p.id, c.id


async def test_same_stock_component_cannot_go_into_two_positions(client, m):
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType, WorkOrderReplacement
    p1, c1 = await _position(m, "Cilindro P1")
    p2, c2 = await _position(m, "Cilindro P2")
    async with AsyncSessionLocal() as s:
        stock = Component(tenant_id=m.id, component_type=ComponentType.pneumatic_cylinder, manufacturer="Festo",
                          model="DSBC", serial_number="C-77", nominal_life={"cycles": 5_000_000})
        s.add(stock)
        await s.commit()
        c77 = stock.id
    tech = m.h("technician")

    async def emergency(pos):
        wo = await api(client, "POST", "/api/work-orders/", tech, 201, json={
            "type": "corrective", "urgency": "emergency", "state": "open", "title": "troca", "position_id": pos})
        await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", tech, json={"to": "in_progress"})
        return wo["id"]
    wo1, wo2 = await emergency(p1), await emergency(p2)
    await api(client, "POST", f"/api/work-orders/{wo1}/replacements", tech, 201,
              json={"position_id": p1, "removal_reason": "failure", "new_component_id": c77})
    r = await client.post(f"/api/work-orders/{wo2}/replacements", headers=tech,
                          json={"position_id": p2, "removal_reason": "failure", "new_component_id": c77})
    assert r.status_code == 409 and "reservado" in r.json()["detail"]

    # a claim that slipped through anyway (e.g. registered before this check) is refused at completion,
    # and nothing is removed from P2
    async with AsyncSessionLocal() as s:
        s.add(WorkOrderReplacement(tenant_id=m.id, work_order_id=wo2, position_id=p2, removed_component_id=c2,
                                   installed_component_id=c77, removal_reason="failure"))
        await s.commit()
    await api(client, "POST", f"/api/work-orders/{wo1}/transition", tech, json={"to": "completed", "labor_hours": 1})
    r = await client.post(f"/api/work-orders/{wo2}/transition", headers=tech, json={"to": "completed", "labor_hours": 1})
    assert r.status_code == 409, r.text
    old2 = await api(client, "GET", f"/api/components/{c2}", m.h("maintenance_planner"))
    assert old2["position_id"] == p2 and old2["removed_at"] is None
    assert (await api(client, "GET", f"/api/work-orders/{wo2}", tech))["state"] == "in_progress"
    new = await api(client, "GET", f"/api/components/{c77}", m.h("maintenance_planner"))
    assert new["position_id"] == p1


async def test_inline_new_component_of_same_model_inherits_nominal_life(client, m):
    p1, c1 = await _position(m, "Cilindro A1")
    tech = m.h("technician")
    wo = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "troca", "position_id": p1})
    await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", tech, json={"to": "in_progress"})
    d = await api(client, "POST", f"/api/work-orders/{wo['id']}/replacements", tech, 201, json={
        "position_id": p1, "removal_reason": "failure",
        "new_component": {"manufacturer": "Festo", "model": "DSBC", "serial_number": "NEW-1"}})
    new_id = d["replacements"][0]["installed_component_id"]
    new = await api(client, "GET", f"/api/components/{new_id}", m.h("maintenance_planner"))
    assert new["nominal_life"]["cycles"] == 5_000_000
    # a different model does not inherit
    p2, _ = await _position(m, "Cilindro A2")
    wo2 = await api(client, "POST", "/api/work-orders/", tech, 201, json={
        "type": "corrective", "urgency": "emergency", "state": "open", "title": "troca", "position_id": p2})
    await api(client, "POST", f"/api/work-orders/{wo2['id']}/transition", tech, json={"to": "in_progress"})
    d2 = await api(client, "POST", f"/api/work-orders/{wo2['id']}/replacements", tech, 201, json={
        "position_id": p2, "removal_reason": "failure",
        "new_component": {"manufacturer": "SMC", "model": "CP96", "serial_number": "NEW-2"}})
    other = await api(client, "GET", f"/api/components/{d2['replacements'][0]['installed_component_id']}",
                      m.h("maintenance_planner"))
    assert not other["nominal_life"].get("cycles")


# ── Web Push: only known push services (blind SSRF) ─────────────────────────

async def test_push_endpoint_must_be_a_known_push_service(client, m):
    from src.notifications import webpush
    keys = {"p256dh": "x" * 20, "auth": "y" * 10}
    tech = m.h("technician")
    for bad in ("https://localhost:9443/push/x", "https://10.0.0.5/admin", "https://fcm.googleapis.com:8443/x",
                "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.tld/x",
                "https://evilpush.apple.com/x", "http://fcm.googleapis.com/x"):
        r = await client.post("/api/push/subscribe", headers=tech, json={"endpoint": bad, "keys": keys})
        assert r.status_code == 422, bad
    for good in ("https://fcm.googleapis.com/fcm/send/fix-1", "https://web.push.apple.com/QGx-fix",
                 "https://updates.push.services.mozilla.com/wpush/v2/fix", "https://wns2-by3p.notify.windows.com/w/?t=fix"):
        await api(client, "POST", "/api/push/subscribe", tech, 201, json={"endpoint": good, "keys": keys})
    # a subscription is never moved to another tenant
    other = await make_maint_tenant("PUSH2")
    r = await client.post("/api/push/subscribe", headers=other.h("technician"),
                          json={"endpoint": "https://fcm.googleapis.com/fcm/send/fix-1", "keys": keys})
    assert r.status_code == 409
    # and send() refuses a non-allowed endpoint already stored (defence in depth)
    with pytest.raises(ValueError):
        await webpush.send("https://127.0.0.1/x", "p", "a", {}, vapid=None)  # type: ignore[arg-type]


# ── webhooks: resolve once, validate every address, connect to that address ─

async def test_webhook_dns_rebinding_and_cgnat_are_blocked(client, m, monkeypatch):
    from src.services import webhooks
    answers = {"rebind.attacker.tld": [["93.184.216.34"], ["172.18.0.5"]], "cgnat.example": [["100.64.1.2"]],
               "mixed.example": [["93.184.216.34", "127.0.0.1"]], "v6.example": [["::ffff:10.0.0.1"]]}

    async def fake_resolve(host, port):
        seq = answers[host]
        return seq.pop(0) if len(seq) > 1 else seq[0]
    monkeypatch.setattr(webhooks, "_resolve", fake_resolve)
    adm = m.h("admin")
    secret = "whsec-0123456789abcdef"
    for host in ("cgnat.example", "mixed.example", "v6.example"):
        r = await client.post("/api/webhooks/", headers=adm, json={"url": f"https://{host}/h", "secret": secret,
                                                                   "events": ["*"]})
        assert r.status_code == 422, host
    # the first lookup (save) is public; delivery looks up again and gets a private address → refused, no request
    rebind = await api(client, "POST", "/api/webhooks/", adm, 201, json={
        "url": "https://rebind.attacker.tld/h", "secret": secret, "events": ["work_order.*"]})
    await api(client, "POST", "/api/work-orders/", m.h("maintenance_planner"), 201, json={
        "type": "improvement", "state": "open", "title": "x", "machine_id": m.machines["Robô de Solda"]})
    seen = []
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204))) as hc:
            assert await webhooks.deliver_due(s, client=hc, limit=500) == 0
    assert seen == []
    await api(client, "DELETE", f"/api/webhooks/{rebind['id']}", adm, 204)
    # a public answer is connected to by IP (no second lookup by httpx), with the original Host/SNI
    url, headers, ext = webhooks.pinned_request("https://hooks.example.com:8443/cmms?a=1", "93.184.216.34")
    assert url == "https://93.184.216.34:8443/cmms?a=1" and headers == {"Host": "hooks.example.com:8443"}
    assert ext == {"sni_hostname": "hooks.example.com"}
    assert webhooks.pinned_request("http://h.example/x", "2606:2800::1")[0] == "http://[2606:2800::1]/x"


# ── API tokens: die with their creator; audit trail names the integration ───

async def test_api_token_audit_and_inactive_creator(client, m):
    from sqlalchemy import update
    from src.db import AsyncSessionLocal
    from src.models import User
    rw = await api(client, "POST", "/api/api-tokens/", m.h("admin"), 201, json={"name": "ERP", "scopes": ["read", "write"]})
    hw = {"Authorization": f"Bearer {rw['token']}"}
    wo = await api(client, "POST", "/api/work-orders/", hw, 201, json={
        "type": "improvement", "state": "open", "title": "via ERP", "machine_id": m.machines["Robô de Solda"]})
    await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", m.h("maintenance_planner"), json={"to": "approved"})
    d = await api(client, "GET", f"/api/work-orders/{wo['id']}", m.h("maintenance_planner"))
    created, approved = d["history"][0], d["history"][1]
    assert created["by_name"] == "API: ERP" and created["api_token_id"] == rw["id"]
    assert approved["by_name"] != "API: ERP" and approved["api_token_id"] is None
    async with AsyncSessionLocal() as s:
        await s.execute(update(User).where(User.id == m.users["admin"]["id"]).values(is_active=False))
        await s.commit()
    r = await client.get("/api/work-orders/", headers=hw)
    assert r.status_code == 401


# ── restoration notifies integrations (failure.updated) ─────────────────────

async def test_restoration_enqueues_failure_updated_webhook(client, m, monkeypatch):
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, WebhookDelivery
    from src.services import webhooks
    from src.services.oee.detector import ensure_failure

    async def fake_resolve(host, port):
        return ["93.184.216.34"]
    monkeypatch.setattr(webhooks, "_resolve", fake_resolve)
    hook = await api(client, "POST", "/api/webhooks/", m.h("admin"), 201, json={
        "url": "https://hooks.example.com/f", "secret": "whsec-0123456789abcdef", "events": ["failure.*"]})
    async with AsyncSessionLocal() as s:
        start = NOW() - timedelta(minutes=30)
        ev = DowntimeEvent(tenant_id=m.id, machine_id=m.machines["Robô de Solda"], start=start, source="auto_fault",
                           suggested_reason_code="EQP-01", stop_start=start)
        s.add(ev)
        await s.flush()
        fr = await ensure_failure(s, [ev], failed_at=start, restored_at=None)
        await ensure_failure(s, [ev], failed_at=start, restored_at=None)          # still open: nothing
        await ensure_failure(s, [ev], failed_at=start, restored_at=NOW())          # restored → failure.updated
        await ensure_failure(s, [ev], failed_at=start, restored_at=fr.restored_at)  # unchanged: nothing more
        await s.commit()
        rows = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook["id"]))).scalars().all()
    assert [r.event for r in rows] == ["failure.updated"]
    assert rows[0].payload["data"]["id"] == fr.id and rows[0].payload["data"]["restored_at"]
    await api(client, "DELETE", f"/api/webhooks/{hook['id']}", m.h("admin"), 204)   # no pending delivery left behind
