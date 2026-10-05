"""Sprint 4: notifications (grouping, area/tenant isolation, preferences, WebSocket, Web Push, digest),
background engine (emergency call, escalation, overdue WOs, component life), outbound webhooks (HMAC signature,
retries) and integration API tokens."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from maint_helpers import add_user, api, make_maint_tenant, seed_usage
from test_maintenance_api import _cylinder

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture
async def m(client):
    return await make_maint_tenant("N")


async def _users(ctx, *roles):
    from src.db import AsyncSessionLocal
    from src.models import User
    async with AsyncSessionLocal() as s:
        return [await s.get(User, ctx.users[r]["id"]) for r in roles]


# ── grouping, area and tenant isolation, preferences ────────────────────────

async def test_grouping_and_area_isolation(client, m):
    from src.db import AsyncSessionLocal
    from src.notifications import service as notif
    other = await make_maint_tenant("N2")
    async with AsyncSessionLocal() as s:
        tech_l2 = await add_user(s, m.id, "technician", "tech-l2", line_ids=[m.line2_id], on_call=True)
        tech_off = await add_user(s, m.id, "technician", "tech-off", line_ids=[m.line_id], on_call=False)
        await s.commit()
    async with AsyncSessionLocal() as s:
        planners = await notif.users_with_roles(s, m.id, ("maintenance_planner",))
        for i in range(3):
            await notif.notify(s, m.id, planners, "plan_due_soon", f"Plano {i} a 80 %", group_key="plan_due_soon",
                               grouped_title="{count} planos cruzaram 80 %")
        # emergency call of a line-1 machine: on-call technicians of line 1 + its supervisor only
        from src.services.maintenance.service import emergency_call
        n = await emergency_call(s, m.id, m.machines["Robô de Solda"], "Chamado emergencial: Robô", None, "/x", {})
        assert n == 2
        # recipients of another tenant are never notified, even if passed by mistake
        foreign = await notif.users_with_roles(s, other.id, ("maintenance_planner",))
        assert await notif.notify(s, m.id, foreign, "wo_on_hold", "x") == []
        await s.commit()
        await notif.deliver_pending(s)
    items = (await api(client, "GET", "/api/notifications/", m.h("maintenance_planner")))["items"]
    grouped = [i for i in items if i["kind"] == "plan_due_soon"]
    assert len(grouped) == 1 and grouped[0]["count"] == 3 and grouped[0]["title"] == "3 planos cruzaram 80 %"
    assert len(grouped[0]["data"]["items"]) == 3
    tech = (await api(client, "GET", "/api/notifications/", m.h("technician")))["items"]
    assert [i["kind"] for i in tech] == ["emergency_call"] and tech[0]["priority"] == "emergency"
    assert tech[0]["data"]["sound"] is True
    sup = (await api(client, "GET", "/api/notifications/", m.h("production_supervisor")))["items"]
    assert [i["kind"] for i in sup] == ["emergency_call"]
    for u in (tech_l2, tech_off):
        h = {"Authorization": f"Bearer {u['token']}"}
        assert (await api(client, "GET", "/api/notifications/", h))["items"] == []
    for role in ("maintenance_planner", "technician"):
        assert (await api(client, "GET", "/api/notifications/", other.h(role)))["items"] == []
    # another tenant cannot read/mark this tenant's notification
    nid = tech[0]["id"]
    r = await client.post(f"/api/notifications/{nid}/read", headers=other.h("technician"))
    assert r.status_code == 404
    r = await client.post(f"/api/notifications/{nid}/read", headers=m.h("maintenance_planner"))
    assert r.status_code == 404                                         # not his own either


async def test_read_unread_first_and_preferences(client, m):
    from src.db import AsyncSessionLocal
    from src.notifications import service as notif
    tech_h = m.h("technician")
    prefs = await api(client, "GET", "/api/notifications/preferences", tech_h)
    kinds = {k["kind"]: k for k in prefs["kinds"]}
    assert kinds["emergency_call"]["mutable"] is False and kinds["wo_assigned"]["channels"]["web_push"] is True
    r = await client.put("/api/notifications/preferences", headers=tech_h,
                         json={"channels": {"emergency_call": {"web_push": False}}})
    assert r.status_code == 422                                         # on-call emergency cannot be muted
    r = await client.put("/api/notifications/preferences", headers=tech_h, json={"channels": {"nope": {"in_app": False}}})
    assert r.status_code == 422
    p = await api(client, "PUT", "/api/notifications/preferences", tech_h,
                  json={"channels": {"wo_assigned": {"in_app": False, "web_push": False}}, "digest_enabled": False})
    assert {k["kind"]: k for k in p["kinds"]}["wo_assigned"]["channels"] == {"in_app": False, "email": False, "web_push": False}
    assert p["digest_enabled"] is False
    (tech,) = await _users(m, "technician")
    async with AsyncSessionLocal() as s:
        assert await notif.notify(s, m.id, [tech], "wo_assigned", "muted") == []
        a = (await notif.notify(s, m.id, [tech], "wo_on_hold", "primeira"))[0]
        await notif.notify(s, m.id, [tech], "wo_awaiting_close", "segunda")
        await s.commit()
    await api(client, "POST", f"/api/notifications/{a.id}/read", tech_h)
    lst = await api(client, "GET", "/api/notifications/", tech_h)
    assert lst["unread_count"] == 1 and [i["title"] for i in lst["items"]] == ["segunda", "primeira"]
    assert (await api(client, "POST", "/api/notifications/read-all", tech_h))["updated"] == 1
    assert (await api(client, "GET", "/api/notifications/", tech_h, params={"unread": "true"}))["items"] == []


async def test_websocket_notification_message(client, m):
    from src.db import AsyncSessionLocal
    from src.notifications import service as notif
    from src.websocket.manager import manager

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    mine, other_user, other_tenant = FakeWS(), FakeWS(), FakeWS()
    await manager.connect(mine, m.id, accept=False, user_id=m.users["technician"]["id"])
    await manager.connect(other_user, m.id, accept=False, user_id=m.users["maintenance_planner"]["id"])
    await manager.connect(other_tenant, "00000000-0000-0000-0000-000000000000", accept=False,
                          user_id=m.users["technician"]["id"])
    try:
        (tech,) = await _users(m, "technician")
        async with AsyncSessionLocal() as s:
            await notif.notify(s, m.id, [tech], "wo_on_hold", "OS em espera")
            await s.commit()
            await notif.deliver_pending(s)
        assert [x["type"] for x in mine.sent] == ["notification"]
        assert mine.sent[0]["notification"]["title"] == "OS em espera" and mine.sent[0]["notification"]["read"] is False
        assert other_user.sent == [] and other_tenant.sent == []
    finally:
        for ws, t in ((mine, m.id), (other_user, m.id), (other_tenant, "00000000-0000-0000-0000-000000000000")):
            manager.disconnect(ws, t)


# ── Web Push (RFC 8291 / VAPID) ─────────────────────────────────────────────

async def test_web_push_encrypted_and_vapid_signed(client, m, monkeypatch):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.notifications import service as notif, webpush

    vk = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings, "vapid_private_key", webpush.b64u(vk.private_numbers().private_value.to_bytes(32, "big")))
    monkeypatch.setattr(settings, "vapid_public_key", "")
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@example.com")
    key = await api(client, "GET", "/api/push/vapid-public-key", m.h("technician"))
    assert key["enabled"] and webpush.b64u_decode(key["public_key"]) == webpush._public_bytes(vk.public_key())

    ua = ec.generate_private_key(ec.SECP256R1())
    auth = webpush.b64u(b"0123456789abcdef")
    endpoint = "https://fcm.googleapis.com/fcm/send/abc"
    r = await client.post("/api/push/subscribe", headers=m.h("technician"),
                          json={"endpoint": "http://insecure", "keys": {"p256dh": "x", "auth": "y"}})
    assert r.status_code == 422
    await api(client, "POST", "/api/push/subscribe", m.h("technician"), 201,
              json={"endpoint": endpoint, "keys": {"p256dh": webpush.b64u(webpush._public_bytes(ua.public_key())),
                                                   "auth": auth}})
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(201)

    (tech,) = await _users(m, "technician")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as pc:
        async with AsyncSessionLocal() as s:
            await notif.notify(s, m.id, [tech], "wo_assigned", "OS-001 atribuída a você", link="/m/work-orders/1")
            await s.commit()
            await notif.deliver_pending(s, push_client=pc)
    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == endpoint and req.headers["content-encoding"] == "aes128gcm"
    payload = json.loads(webpush.decrypt(req.content, ua, auth))
    assert payload["title"] == "OS-001 atribuída a você" and payload["kind"] == "wo_assigned"
    # VAPID: ES256 JWT for the push service origin, signed by the server key
    auth_h = req.headers["authorization"]
    t = auth_h.split("t=")[1].split(",")[0]
    k = auth_h.split("k=")[1]
    assert k == key["public_key"]
    head, claims, sig = t.split(".")
    c = json.loads(webpush.b64u_decode(claims))
    assert c["aud"] == "https://fcm.googleapis.com" and c["sub"] == "mailto:ops@example.com"
    raw = webpush.b64u_decode(sig)
    vk.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                           f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    # a gone subscription (410) is removed
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(410))) as pc:
        async with AsyncSessionLocal() as s:
            await notif.notify(s, m.id, [tech], "window_request", "janela")
            await s.commit()
            await notif.deliver_pending(s, push_client=pc)
    from sqlalchemy import select
    from src.models import PushSubscription
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).first() is None


# ── daily digest ─────────────────────────────────────────────────────────────

async def test_daily_digest_once_a_day_at_07(client, m, monkeypatch):
    from datetime import date
    from src.db import AsyncSessionLocal
    from src.notifications import service as notif
    sent = []

    async def fake_email(to, subject, body):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(notif, "_email", fake_email)
    (planner,) = await _users(m, "maintenance_planner")
    async with AsyncSessionLocal() as s:
        await notif.notify(s, m.id, [planner], "plan_due_soon", "Plano A a 80 %", group_key="plan_due_soon")
        await notif.notify(s, m.id, [planner], "wo_overdue", "OS-007 vencida")
        await notif.notify(s, m.id, [planner], "plan_due", "Plano B vencido")          # immediate e-mail kind
        await s.commit()
        await notif.deliver_pending(s)
        assert [x[1] for x in sent] == ["[FactoryConnect] Plano B vencido"]
        sent.clear()
        assert await notif.send_digests(s, m.id, date(2026, 10, 5), 6) == 0          # before 07:00
        assert await notif.send_digests(s, m.id, date(2026, 10, 5), 7) == 1
        assert await notif.send_digests(s, m.id, date(2026, 10, 5), 9) == 0          # once a day
    assert len(sent) == 1 and sent[0][0] == m.users["maintenance_planner"]["email"]
    assert "Plano A a 80 %" in sent[0][2] and "OS-007 vencida" in sent[0][2] and "Plano B" not in sent[0][2]


# ── engine: emergency call, escalation, overdue WOs, component life ─────────

async def test_engine_emergency_call_escalation_and_overdue(client, m, monkeypatch):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, FailureRecord
    from src.services.maintenance import engine
    asset = await _cylinder(m, total=4_600_000, nominal=5_000_000)          # component at 0.9 of life
    mid = asset["machine_id"]
    async with AsyncSessionLocal() as s:
        ev = DowntimeEvent(tenant_id=m.id, machine_id=mid, start=NOW() - timedelta(minutes=3), source="auto_fault",
                           suggested_reason_code="EQP-01", stop_start=NOW() - timedelta(minutes=3))
        old_ev = DowntimeEvent(tenant_id=m.id, machine_id=mid, start=NOW() - timedelta(hours=3), source="auto_fault",
                               suggested_reason_code="EQP-03", stop_start=NOW() - timedelta(hours=3))
        s.add_all([ev, old_ev])
        await s.flush()
        f = FailureRecord(tenant_id=m.id, machine_id=mid, downtime_event_id=ev.id, failed_at=ev.start,
                          severity="critical", status="draft", detection_method="production_interference")
        f_old = FailureRecord(tenant_id=m.id, machine_id=mid, downtime_event_id=old_ev.id, failed_at=old_ev.start,
                              severity="critical", status="draft", emergency_notified_at=NOW() - timedelta(hours=3))
        f_restored = FailureRecord(tenant_id=m.id, machine_id=mid, downtime_event_id=None, failed_at=NOW() - timedelta(hours=5),
                                   restored_at=NOW() - timedelta(hours=4), severity="critical", status="draft")
        s.add_all([f, f_old, f_restored])
        await s.commit()
    wo = await api(client, "POST", "/api/work-orders/", m.h("maintenance_planner"), 201, json={
        "type": "improvement", "state": "open", "title": "atrasada", "machine_id": mid})
    url = f"/api/work-orders/{wo['id']}/transition"
    await api(client, "POST", url, m.h("maintenance_planner"), json={"to": "approved"})
    await api(client, "POST", url, m.h("maintenance_planner"), json={
        "to": "scheduled", "scheduled_for": (NOW() - timedelta(days=3)).date().isoformat(),
        "assignee_id": m.users["technician"]["id"]})
    async with AsyncSessionLocal() as s:
        out = await engine.tick_tenant(s, m.id, force_plans=True)
    # f: emergency call; f_old: already called, 3 h open > 2 × MTTR (60 min from f_restored) → escalated
    assert out["emergency"] == 1 and out["escalations"] == 1 and out["overdue"] == 1 and out["component_alerts"] == 1
    async with AsyncSessionLocal() as s:
        again = await engine.tick_tenant(s, m.id)
    assert again["emergency"] == again["escalations"] == again["overdue"] == again["component_alerts"] == 0
    tech = {i["kind"] for i in (await api(client, "GET", "/api/notifications/", m.h("technician")))["items"]}
    assert {"emergency_call", "wo_overdue", "wo_assigned"} <= tech
    pl = {i["kind"] for i in (await api(client, "GET", "/api/notifications/", m.h("maintenance_planner")))["items"]}
    assert {"failure_escalation", "wo_overdue", "component_life"} <= pl
    sup = {i["kind"] for i in (await api(client, "GET", "/api/notifications/", m.h("production_supervisor")))["items"]}
    assert {"emergency_call", "failure_escalation"} <= sup
    comp = await api(client, "GET", "/api/maintenance/components", m.h("maintenance_planner"))
    assert comp[0]["component_id"] == asset["component_id"] and comp[0]["alert"] == "attention"


async def test_call_maintenance_from_downtime(client, m):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, FailureRecord
    mid = m.machines["Robô de Solda"]
    async with AsyncSessionLocal() as s:
        ev = DowntimeEvent(tenant_id=m.id, machine_id=mid, start=NOW() - timedelta(minutes=5), source="auto_fault",
                           suggested_reason_code="EQP-01", stop_start=NOW() - timedelta(minutes=5))
        s.add(ev)
        await s.flush()
        f = FailureRecord(tenant_id=m.id, machine_id=mid, downtime_event_id=ev.id, failed_at=ev.start,
                          severity="critical", status="draft")
        s.add(f)
        await s.flush()
        ev.failure_id = f.id
        await s.commit()
    wo = await api(client, "POST", "/api/work-orders/emergency-call", m.h("operator"), 201,
                   json={"downtime_event_id": ev.id, "note": "robô parou"})
    assert wo["type"] == "corrective" and wo["urgency"] == "emergency" and wo["state"] == "open"
    assert wo["downtime_event_id"] == ev.id and wo["failure_id"] == f.id
    again = await api(client, "POST", "/api/work-orders/emergency-call", m.h("production_supervisor"), 201,
                      json={"downtime_event_id": ev.id})
    assert again["id"] == wo["id"]                                           # one call per stop
    tech = (await api(client, "GET", "/api/notifications/", m.h("technician")))["items"]
    assert tech[0]["kind"] == "emergency_call" and tech[0]["data"]["work_order_id"] == wo["id"]
    d = await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", m.h("technician"), json={"to": "in_progress"})
    assert d["state"] == "in_progress"
    f2 = await api(client, "GET", f"/api/failures/{f.id}", m.h("technician"))
    assert f2["work_order_id"] == wo["id"] and f2["repair_started_at"]


# ── webhooks ─────────────────────────────────────────────────────────────────

def test_signature_roundtrip():
    from src.services import webhooks
    sig = webhooks.sign("s3cr3t-s3cr3t-s3cr3t", "1700000000", b'{"a":1}')
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    assert webhooks.verify("s3cr3t-s3cr3t-s3cr3t", "1700000000", b'{"a":1}', sig)
    assert not webhooks.verify("other-secret-xxxxxx", "1700000000", b'{"a":1}', sig)
    assert not webhooks.verify("s3cr3t-s3cr3t-s3cr3t", "1700000001", b'{"a":1}', sig)
    assert webhooks.matches(["work_order.*"], "work_order.completed")
    assert not webhooks.matches(["failure.*"], "work_order.completed")


def _orig_url(r) -> str:
    """URL the webhook was meant for: delivery connects to the pinned IP with the original Host header."""
    return f"{r.url.scheme}://{r.headers['host']}{r.url.raw_path.decode()}"


async def test_webhook_delivery_signed_with_retries_and_isolation(client, m, monkeypatch):
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import WebhookDelivery
    from src.services import webhooks

    async def fake_resolve(host, port):
        return ["93.184.216.34"]                       # public address (example.com)
    monkeypatch.setattr(webhooks, "_resolve", fake_resolve)
    other = await make_maint_tenant("W2")
    secret = "whsec-0123456789abcdef"
    adm = m.h("admin")
    for bad in ({"url": "http://127.0.0.1:9/hook", "secret": secret, "events": ["work_order.*"]},
                {"url": "ftp://example.com/x", "secret": secret, "events": ["work_order.*"]},
                {"url": "https://hooks.example.com/x", "secret": secret, "events": ["nope.event"]},
                {"url": "https://hooks.example.com/x", "secret": "short", "events": ["*"]}):
        r = await client.post("/api/webhooks/", headers=adm, json=bad)
        assert r.status_code == 422, bad
    hook = await api(client, "POST", "/api/webhooks/", adm, 201, json={
        "url": "https://hooks.example.com/cmms", "secret": secret, "events": ["work_order.*", "failure.*"]})
    assert "secret" not in hook and hook["has_secret"]
    await api(client, "POST", "/api/webhooks/", other.h("admin"), 201, json={
        "url": "https://hooks.example.com/other", "secret": secret, "events": ["*"]})
    # tenant B cannot see / change tenant A's webhook
    assert (await client.get(f"/api/webhooks/{hook['id']}", headers=other.h("admin"))).status_code == 404
    assert (await client.delete(f"/api/webhooks/{hook['id']}", headers=other.h("admin"))).status_code == 404
    assert [h["id"] for h in await api(client, "GET", "/api/webhooks/", adm)] == [hook["id"]]

    wo = await api(client, "POST", "/api/work-orders/", m.h("maintenance_planner"), 201, json={
        "type": "improvement", "state": "open", "title": "integração", "machine_id": m.machines["Robô de Solda"]})
    await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", m.h("maintenance_planner"), json={"to": "approved"})
    seen = []

    def ok(request: httpx.Request):
        seen.append(request)
        return httpx.Response(204)

    async with AsyncSessionLocal() as s:
        async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as hc:
            n = await webhooks.deliver_due(s, client=hc, limit=500)
    assert all(r.url.host == "93.184.216.34" and r.extensions.get("sni_hostname") == "hooks.example.com" for r in seen)
    mine = [r for r in seen if _orig_url(r) == "https://hooks.example.com/cmms"]
    events = [r.headers["x-factoryconnect-event"] for r in mine]
    assert events == ["work_order.created", "work_order.state_changed", "work_order.approved"]
    assert all(_orig_url(r) != "https://hooks.example.com/other" or json.loads(r.content)["tenant_id"] == other.id for r in seen)
    for r in mine:
        assert webhooks.verify(secret, r.headers["x-factoryconnect-timestamp"], r.content,
                               r.headers["x-factoryconnect-signature"])
        body = json.loads(r.content)
        assert body["tenant_id"] == m.id and body["data"]["id"] == wo["id"] and body["id"] == r.headers["x-factoryconnect-delivery"]
    assert n >= 3

    # failure → retried with backoff, then 'failed' after MAX_ATTEMPTS
    await api(client, "POST", "/api/failures/", m.h("technician"), 201, json={
        "machine_id": m.machines["Robô de Solda"], "failed_at": NOW().isoformat(), "severity": "degraded"})
    async with AsyncSessionLocal() as s:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as hc:
            await webhooks.deliver_due(s, client=hc, limit=500)
        d = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook["id"],
                                                           WebhookDelivery.event == "failure.created"))).scalar_one()
        assert d.status == "pending" and d.attempts == 1 and d.last_status_code == 500
        assert d.next_attempt_at > NOW() + timedelta(seconds=20)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as hc:
            for _ in range(webhooks.MAX_ATTEMPTS - 1):
                await webhooks.attempt(s, d, client=hc)
        assert d.status == "failed" and d.attempts == webhooks.MAX_ATTEMPTS
        await s.commit()
    dl = await api(client, "GET", f"/api/webhooks/{hook['id']}/deliveries", adm)
    assert {x["status"] for x in dl} == {"delivered", "failed"}
    r = await client.get("/api/webhooks/", headers=m.h("manager"))
    assert r.status_code == 403


# ── API tokens ───────────────────────────────────────────────────────────────

async def test_api_tokens_scopes_hash_and_isolation(client, m):
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import ApiToken
    other = await make_maint_tenant("T2")
    r = await client.post("/api/api-tokens/", headers=m.h("manager"), json={"name": "x"})
    assert r.status_code == 403
    ro = await api(client, "POST", "/api/api-tokens/", m.h("admin"), 201, json={"name": "ERP leitura", "scopes": ["read"]})
    rw = await api(client, "POST", "/api/api-tokens/", m.h("admin"), 201, json={"name": "CMMS", "scopes": ["read", "write"]})
    assert ro["token"].startswith("fct_")
    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(ApiToken).where(ApiToken.id == ro["id"]))).scalar_one()
        assert row.token_hash != ro["token"] and ro["token"] not in row.token_hash and len(row.token_hash) == 64
    listed = await api(client, "GET", "/api/api-tokens/", m.h("admin"))
    assert all("token" not in t for t in listed) and {t["id"] for t in listed} == {ro["id"], rw["id"]}
    hr = {"Authorization": f"Bearer {ro['token']}"}
    hw = {"Authorization": f"Bearer {rw['token']}"}
    wos = await api(client, "GET", "/api/work-orders/", hr)
    assert wos == []
    body = {"type": "corrective", "state": "open", "title": "via ERP", "machine_id": m.machines["Robô de Solda"]}
    r = await client.post("/api/work-orders/", headers=hr, json=body)
    assert r.status_code == 403
    created = await api(client, "POST", "/api/work-orders/", hw, 201, json=body)
    assert created["title"] == "via ERP"
    # the token is bound to its tenant
    r = await client.get(f"/api/work-orders/{created['id']}", headers={"Authorization": f"Bearer {ro['token']}"})
    assert r.status_code == 200
    ot = await api(client, "POST", "/api/api-tokens/", other.h("admin"), 201, json={"name": "B", "scopes": ["read"]})
    r = await client.get(f"/api/work-orders/{created['id']}", headers={"Authorization": f"Bearer {ot['token']}"})
    assert r.status_code == 404
    # tokens cannot manage integrations; admin-only endpoints refuse them
    assert (await client.get("/api/api-tokens/", headers=hw)).status_code == 403
    assert (await client.delete(f"/api/api-tokens/{ro['id']}", headers=other.h("admin"))).status_code == 404
    await api(client, "DELETE", f"/api/api-tokens/{ro['id']}", m.h("admin"), 204)
    assert (await client.get("/api/work-orders/", headers=hr)).status_code == 401
    assert (await client.get("/api/work-orders/", headers={"Authorization": "Bearer fct_invalid"})).status_code == 401


# ── users: several roles, area, on call; /me ────────────────────────────────

async def test_users_roles_lines_on_call_and_me(client, m):
    me = await api(client, "GET", "/api/auth/me", m.h("technician"))
    assert me["role"] == "technician" and me["roles"] == ["technician"]
    assert me["on_call"] is True and me["line_ids"] == [m.line_id]
    adm = m.h("admin")
    u = await api(client, "POST", "/api/users/", adm, 201, json={
        "email": f"multi{m.id[:6]}@example.com", "password": "Senha@12345", "name": "Multi",
        "role": "technician", "roles": ["technician", "production_supervisor"], "line_ids": [m.line2_id]})
    assert u["role"] == "technician" and set(u["roles"]) == {"technician", "production_supervisor"}
    r = await client.patch(f"/api/users/{u['id']}", headers=adm, json={"line_ids": ["00000000-0000-0000-0000-000000000000"]})
    assert r.status_code == 404
    # planner manages the on-call roster but not roles
    pl = m.h("maintenance_planner")
    d = await api(client, "PATCH", f"/api/users/{u['id']}", pl, json={"on_call": True, "line_ids": [m.line_id]})
    assert d["on_call"] is True and d["line_ids"] == [m.line_id]
    assert (await client.patch(f"/api/users/{u['id']}", headers=pl, json={"roles": ["admin"]})).status_code == 403
    assert (await client.patch(f"/api/users/{m.users['admin']['id']}", headers=pl, json={"on_call": True})).status_code == 403
    on_call = await api(client, "GET", "/api/users/", pl, params={"role": "technician", "on_call": "true",
                                                                  "line_id": m.line_id})
    assert {x["id"] for x in on_call} == {u["id"], m.users["technician"]["id"]}
    d = await api(client, "PATCH", f"/api/users/{u['id']}", adm, json={"roles": ["maintenance_planner"]})
    assert d["role"] == "maintenance_planner" and d["roles"] == ["maintenance_planner"]
    other = await make_maint_tenant("U2")
    assert (await client.get(f"/api/users/{u['id']}", headers=other.h("admin"))).status_code == 404
    assert (await client.patch(f"/api/users/{u['id']}", headers=other.h("admin"), json={"on_call": False})).status_code == 404
    assert u["id"] not in {x["id"] for x in await api(client, "GET", "/api/users/", other.h("admin"))}
    # legacy hierarchy keeps working for multi-role users (technician + manager → manager-level access)
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        mm = await add_user(s, m.id, "technician", "tech-mgr", roles=["technician", "manager"])
        await s.commit()
    r = await client.patch(f"/api/machines/{m.machines['Robô de Solda']}", headers={"Authorization": f"Bearer {mm['token']}"},
                           json={"status": "maintenance"})
    assert r.status_code == 200
