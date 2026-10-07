"""Sprint 6 §5 — public DEMO tenant: bootstrap from the virtual plant map, demo session (2 h JWT demo_viewer, per-IP
rate limit), read-only enforcement on EVERY write route of the app (generated from the route table), blocked
areas even for GET, no user e-mail anywhere, isolation of the demo token on every GET route of the app, AI quota,
demo status banner, hidden from superadmin listings, demo-bot scripted replacement WO (audited)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from sqlalchemy import delete, select, update

from conftest import _make_tenant
from usage_helpers import add_component, add_position, add_tag, api, build_plant

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "edge" / "config" / "plant.json"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAFE = {"GET", "HEAD", "OPTIONS"}


@pytest_asyncio.fixture(scope="module")
async def demo(app):
    """The demo tenant, bootstrapped once for this module (idempotent)."""
    if not MAP.exists():
        pytest.skip("edge/config/plant.json missing")
    from src.db import AsyncSessionLocal
    from src.services.demo.bootstrap import bootstrap, load_map
    async with AsyncSessionLocal() as db:
        out = await bootstrap(db, load_map(str(MAP)))
    return out


async def _session(client, ip_suffix: str | None = None) -> dict:
    from src.db import AsyncSessionLocal
    from src.services.demo.session import create_session
    async with AsyncSessionLocal() as db:
        return await create_session(db, f"test-{ip_suffix or uuid.uuid4().hex}")


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def test_bootstrap_creates_the_demo_tenant(demo, db):
    import json
    from src.models import Gateway, Line, Machine, Plant, PredictiveMonitor, Shift, Tag, Tenant, User
    from src.services.demo.bootstrap import bootstrap, load_map
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    t = await db.get(Tenant, demo["tenant_id"])
    assert t.is_demo and t.slug == "demo-publica"      # never the dev seed slug "demo" (scripts/seed_dev.py)
    plant = await db.get(Plant, demo["plant_id"])
    assert plant.timezone == "America/Sao_Paulo"
    shifts = (await db.execute(select(Shift).where(Shift.tenant_id == t.id))).scalars().all()
    assert len(shifts) == 3 and all(sorted(s.weekdays) == list(range(7)) for s in shifts)
    names = {m.name for m in (await db.execute(select(Machine).where(Machine.tenant_id == t.id))).scalars()}
    assert names == {m["name"] for ln in doc["assets"]["lines"] for m in ln["machines"]}
    lines = (await db.execute(select(Line).where(Line.tenant_id == t.id))).scalars().all()
    assert all(ln.output_machine_id for ln in lines)
    n_tags = (await db.execute(select(Tag).where(Tag.tenant_id == t.id))).scalars().all()
    assert len(n_tags) == len(doc["tags"]) == demo["tags"]
    gw = await db.get(Gateway, demo["gateway_id"])
    assert gw.token_hash and gw.mqtt_password_hash and all(tg.gateway_id == gw.id for tg in n_tags)
    creds = demo["gateway_credentials"]
    if creds is not None:                              # first run in this DB: shown once
        assert creds["token"].startswith("fcg_") and creds["mqtt"]["password"]
        assert gw.token_hash != creds["token"]          # only the hash is stored
    mons = (await db.execute(select(PredictiveMonitor).where(PredictiveMonitor.tenant_id == t.id))).scalars().all()
    assert len(mons) == sum(1 for x in doc["tags"] if x["semantic_role"] == "stroke_time")
    users = {u.email: u for u in (await db.execute(select(User).where(User.tenant_id == t.id))).scalars()}
    assert users["demo-bot@demo.factoryconnect.invalid"].name == "demo-bot"
    assert "demo_viewer" in users["visitante@demo.factoryconnect.invalid"].role_set
    # idempotent: a second run creates nothing and does not print credentials again
    async with __import__("src.db", fromlist=["AsyncSessionLocal"]).AsyncSessionLocal() as s:
        again = await bootstrap(s, load_map(str(MAP)))
    assert again["rows_created"] == 0 and again["gateway_credentials"] is None and again["monitors"] == 0
    # the gateway map of the demo is accepted by the edge parser
    import sys
    sys.path.insert(0, str(ROOT))
    from edge.src import plant_map
    from src.services.gateways import config_document
    body, _, warnings = await config_document(db, gw)
    assert warnings == []
    parsed = plant_map.parse_plant_map(body["plant_map"], env={})
    assert len(parsed.tags) == len(doc["tags"])
    # a non-demo tenant holding the slug is refused
    from src.services.demo.bootstrap import BootstrapError, ensure_tenant
    from src.config import settings
    old = settings.demo_tenant_slug
    other = await _make_tenant(db, "SLUG")
    await db.commit()
    settings.demo_tenant_slug = other.slug
    try:
        with pytest.raises(BootstrapError):
            await ensure_tenant(db)
    finally:
        settings.demo_tenant_slug = old
        await db.rollback()


async def test_demo_session_endpoint_and_rate_limit(client, demo, db):
    from src.api.routes.public import ip_hash
    from src.auth.security import decode_token
    from src.models import DemoSession
    r = await client.post("/api/public/demo-session")
    assert r.status_code == 201, r.text
    out = r.json()
    claims = decode_token(out["access_token"])
    assert claims["role"] == "demo_viewer" and claims["tenant_id"] == demo["tenant_id"] and claims["demo_session"]
    exp = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    assert timedelta(hours=1, minutes=59) < exp - datetime.now(timezone.utc) <= timedelta(hours=2)
    assert out["redirect_to"] == "/dashboard" and out["banner"].startswith("Demonstração")
    # the token opens the dashboard of the demo tenant
    ov = await api(client, "GET", "/api/overview", _h(out["access_token"]))
    assert ov["plant_id"] == demo["plant_id"]
    # 10 per hour per IP (counted by keyed hash) → 429
    h = ip_hash("127.0.0.1")
    used = (await db.execute(select(DemoSession).where(DemoSession.ip_hash == h))).scalars().all()
    for _ in range(10 - len(used)):
        db.add(DemoSession(ip_hash=h, expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
    await db.commit()
    r = await client.post("/api/public/demo-session")
    assert r.status_code == 429
    await db.execute(delete(DemoSession).where(DemoSession.ip_hash == h))
    await db.commit()


def _fill(path: str, ids: dict) -> str:
    return re.sub(r"\{(\w+)(?::path)?\}", lambda m: ids.get(m.group(1), str(uuid.uuid4())), path)


async def test_every_write_route_is_403_for_demo_viewer(client, app, demo):
    """Generated from the app's route table: every non-GET route under /api answers 403 with the demo message
    (except the AI questions, which are under the per-session quota)."""
    from src.services.demo.guard import AREA_MSG, WRITE_MSG, is_ai_question
    tok = (await _session(client))["access_token"]
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for method in sorted(set(route.methods) - SAFE):
            url = _fill(route.path, {})
            if is_ai_question(method, url):
                continue
            r = await client.request(method, url, headers=_h(tok), json={})
            assert r.status_code == 403, (method, route.path, r.status_code, r.text[:200])
            assert r.json()["detail"] in (WRITE_MSG, AREA_MSG), (method, route.path)
            checked += 1
    assert checked > 100
    # the same through ?token= (WebSocket-style) and for the demo viewer account with a non-demo token
    r = await client.post("/api/lines/", params={"token": tok}, json={"name": "x"})
    assert r.status_code == 403
    from src.auth.security import create_access_token
    from src.db import AsyncSessionLocal
    from src.services.demo.bootstrap import viewer_user
    async with AsyncSessionLocal() as s:
        v = await viewer_user(s, demo["tenant_id"])
    plain = create_access_token({"sub": v.id, "tenant_id": demo["tenant_id"], "role": "manager"})
    for method, url in (("POST", "/api/lines/"), ("PATCH", f"/api/machines/{uuid.uuid4()}"),
                        ("POST", f"/api/plants/{demo['plant_id']}/layouts"), ("PUT", "/api/onboarding")):
        r = await client.request(method, url, headers=_h(plain), json={"name": "x", "step": 1, "state": "done"})
        assert r.status_code == 403 and "Modo demonstração" in r.json()["detail"], (method, url, r.text)


async def test_blocked_areas_allowed_reads_and_no_email(client, app, demo):
    tok = (await _session(client))["access_token"]
    h = _h(tok)
    for url in ("/api/users/", "/api/admin/tenants", "/api/gateways/", f"/api/gateways/{demo['gateway_id']}/mqtt",
                "/api/cameras/", "/api/maintenance/settings", "/api/api-tokens/", "/api/webhooks/",
                "/api/onboarding", "/api/ai/audit", "/api/ai/conversations", "/api/notifications/preferences",
                "/api/report-schedules/", "/api/reports/settings", "/api/reports/external-recipients"):
        r = await client.get(url, headers=h)
        assert r.status_code == 403, (url, r.status_code)
    for url in ("/api/overview", "/api/lines/", "/api/machines/", "/api/plants/tree", "/api/work-orders/",
                "/api/predictive/alerts", f"/api/plants/{demo['plant_id']}/layout/auto", "/api/public/demo-status",
                "/api/demo/session"):
        r = await client.get(url, headers=h)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    me = await api(client, "GET", "/api/auth/me", h)
    assert me["email"] is None and "demo_viewer" in me["roles"]
    # no e-mail in any GET route without path parameters
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api") and "GET" in route.methods \
                and "{" not in route.path:
            r = await client.get(route.path, headers=h)
            assert r.status_code < 500, (route.path, r.status_code, r.text[:300])
            if r.headers.get("content-type", "").startswith("application/json"):
                assert not EMAIL_RE.search(r.text), (route.path, EMAIL_RE.search(r.text).group(0))
    from src.services.demo.guard import scrub
    assert scrub({"email": "a@b.com", "x": [{"owner_email": "c@d.org", "note": "fale com e@f.com"}]}) == \
        {"email": None, "x": [{"owner_email": None, "note": "fale com (oculto)"}]}


async def test_demo_token_never_reaches_another_tenant(client, app, demo):
    """Isolation on every GET route listed by the app: path ids of another tenant → never 200 with its data."""
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        other = await _make_tenant(s, "DISO")
        await s.commit()
    p = await build_plant(client, other, "DISO")
    pos = await add_position(client, other, p, "Cilindro X")
    comp = await add_component(client, other, p, "c1", position=pos)
    tag = await add_tag(client, other, p, "run", "run_state", data_type="bool")
    lay = await api(client, "POST", f"/api/plants/{p.plant['id']}/layouts", other.headers("admin"), 201)
    ids = {"plant_id": p.plant["id"], "line_id": p.line["id"], "machine_id": p.machine["id"], "tag_id": tag["id"],
           "position_id": pos["id"], "component_id": comp["id"], "layout_id": lay["id"],
           "user_id": other.users["admin"]["id"], "tenant_id": other.id}
    secrets_ = set(ids.values()) - {other.id}
    tok = (await _session(client))["access_token"]
    h = _h(tok)
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api") or "GET" not in route.methods:
            continue
        r = await client.get(_fill(route.path, ids), headers=h)
        assert r.status_code < 500, (route.path, r.status_code, r.text[:300])
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            leaked = [x for x in secrets_ if x in r.text]
            assert not leaked, (route.path, leaked)
    for url, params in (("/api/oee", {"scope": "plant", "id": p.plant["id"]}),
                        ("/api/losses/pareto", {"scope": "line", "id": p.line["id"]}),
                        ("/api/reliability", {"scope": "machine", "id": p.machine["id"]}),
                        ("/api/overview", {"plant_id": p.plant["id"]}),
                        ("/api/downtime/", {"machine_id": p.machine["id"]}),
                        ("/api/tags/", {"machine_id": p.machine["id"]})):
        r = await client.get(url, headers=h, params=params)
        assert r.status_code in (200, 404) and not any(x in r.text for x in secrets_ - {p.machine["id"]}
                                                       if r.status_code == 200), (url, r.status_code)
        if r.status_code == 200:
            assert r.json() in ([], {}) or p.machine["id"] not in r.text, url
    # the WebSocket of the demo tenant is allowed (live data); another tenant's is not
    from src.websocket.manager import WS_FORBIDDEN, authorize_ws_user
    code, _, _ = await authorize_ws_user(tok, demo["tenant_id"])
    assert code is None
    code, _, _ = await authorize_ws_user(tok, other.id)
    assert code == WS_FORBIDDEN


async def test_ai_quota_per_demo_session(client, demo, monkeypatch):
    from src.config import settings
    from src.services.demo.guard import QUOTA_MSG
    monkeypatch.setattr(settings, "demo_ai_questions_per_session", 2)
    s = await _session(client)
    h = _h(s["access_token"])
    for _ in range(2):
        r = await client.post("/api/ai/chat", headers=h, json={"message": "Qual o OEE de hoje?",
                                                               "conversation_id": str(uuid.uuid4())})
        assert r.status_code != 429 and r.status_code != 403, r.text
    r = await client.post("/api/ai/chat", headers=h, json={"message": "E ontem?"})
    assert r.status_code == 429 and r.json()["detail"] == QUOTA_MSG
    r = await client.post("/api/ai/chat/stream", headers=h, json={"message": "E ontem?"})
    assert r.status_code == 429
    info = await api(client, "GET", "/api/demo/session", h)
    assert info["ai_questions_used"] == 2 and info["ai_questions_left"] == 0
    # another session has its own quota
    r = await client.post("/api/ai/chat", headers=_h((await _session(client))["access_token"]),
                          json={"message": "Qual o OEE?"})
    assert r.status_code != 429


async def test_demo_status_banner_and_hidden_from_superadmin(client, demo, db, tenants):
    from src.models import Machine
    await db.execute(update(Machine).where(Machine.tenant_id == demo["tenant_id"]).values(
        last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    await db.commit()
    st = await api(client, "GET", "/api/public/demo-status", {})
    assert st["available"] and st["plant_running"] is False and "temporariamente parada" in st["banner"]
    await db.execute(update(Machine).where(Machine.tenant_id == demo["tenant_id"]).values(
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=30)))
    await db.commit()
    st = await api(client, "GET", "/api/public/demo-status", {})
    assert st["plant_running"] is True and st["banner"] == \
        "Demonstração — dados de uma planta simulada (planta virtual FactoryConnect)"
    listed = await api(client, "GET", "/api/admin/tenants", {"Authorization": f"Bearer {tenants.superadmin['token']}"})
    assert demo["tenant_id"] not in {x["id"] for x in listed} and tenants.a.id in {x["id"] for x in listed}


async def test_demo_bot_executes_the_scripted_replacement(client, demo, db):
    from src.models import (AuditEvent, Component, Position, PredictiveAlert, PredictiveMonitor, WorkOrder,
                            WorkOrderHistory)
    from src.services.demo.bot import run_once
    from src.services.demo.bootstrap import bot_user
    tid = demo["tenant_id"]
    pos = (await db.execute(select(Position).where(Position.tenant_id == tid, Position.name == "Cilindro B3"))).scalar_one()
    mon = (await db.execute(select(PredictiveMonitor).where(PredictiveMonitor.position_id == pos.id))).scalar_one()
    old = (await db.execute(select(Component).where(Component.position_id == pos.id,
                                                    Component.removed_at.is_(None)))).scalar_one()
    old_id, pos_id = old.id, pos.id
    now = datetime.now(timezone.utc)
    young = PredictiveAlert(tenant_id=tid, monitor_id=mon.id, position_id=pos.id, machine_id=pos.machine_id,
                            feature="stroke_time", state="open", severity="warning", opened_at=now)
    a = PredictiveAlert(tenant_id=tid, monitor_id=mon.id, position_id=pos.id, machine_id=pos.machine_id,
                        feature="stroke_time", state="open", severity="warning", opened_at=now - timedelta(hours=1),
                        explanation="tempo de curso +12 % sobre a linha de base")
    db.add(a)
    await db.commit()
    done = await run_once(db, now)
    assert [d["alert_id"] for d in done] == [a.id]
    assert done[0]["virtual_plant_ref"] == "montagem.fixacao.b3"
    db.expire_all()
    wo = await db.get(WorkOrder, done[0]["work_order_id"])
    bot = await bot_user(db, tid)
    assert wo.state == "closed" and wo.type == "predictive" and wo.finding == "defect_confirmed"
    hist = (await db.execute(select(WorkOrderHistory).where(WorkOrderHistory.work_order_id == wo.id))).scalars().all()
    assert {x.to_state for x in hist} >= {"scheduled", "in_progress", "completed", "closed"}
    assert all(x.by == bot.id for x in hist if x.to_state in ("in_progress", "completed", "closed"))
    new = (await db.execute(select(Component).where(Component.position_id == pos_id,
                                                    Component.removed_at.is_(None)))).scalar_one()
    assert new.id != old_id and (await db.get(Component, old_id)).removed_at is not None
    ev = (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tid,
                                                    AuditEvent.action == "demo_bot.replacement"))).scalars().all()
    assert ev and ev[-1].user_id == bot.id and ev[-1].entity_id == wo.id
    assert (await db.get(PredictiveAlert, done[0]["alert_id"])).state != "open"
    # a fresh alert (younger than DEMO_BOT_REPLACE_AFTER_S) is left for the visitors to see
    db.add(young)
    await db.commit()
    assert await run_once(db, now) == []


class _StubControl:
    """Stub of the virtual plant control API as seen from another container: /replace requires X-Control-Token."""

    def __init__(self, token: str):
        import http.server
        import threading
        self.calls: list[dict] = []
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                import json
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                given = self.headers.get("X-Control-Token")
                stub.calls.append({"path": self.path, "token": given, "body": body})
                code = 200 if given == token else 403
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


async def test_demo_bot_sends_control_token_and_backs_off_when_refused(client, demo, db, monkeypatch):
    from src.config import settings
    from src.models import AuditEvent, Position, PredictiveAlert, PredictiveMonitor
    from src.services.demo.bot import run_once
    tid = demo["tenant_id"]
    pos = (await db.execute(select(Position).where(Position.tenant_id == tid, Position.name == "Cilindro B3"))).scalar_one()
    mon = (await db.execute(select(PredictiveMonitor).where(PredictiveMonitor.position_id == pos.id))).scalar_one()
    # leave no open alert from earlier tests
    await db.execute(update(PredictiveAlert).where(PredictiveAlert.tenant_id == tid, PredictiveAlert.state.in_(
        ("open", "acknowledged"))).values(state="closed"))
    await db.commit()
    stub = _StubControl("s3cret")
    monkeypatch.setattr(settings, "demo_virtual_plant_control_url", stub.url)
    try:
        def alert(age_h: float) -> PredictiveAlert:
            return PredictiveAlert(tenant_id=tid, monitor_id=mon.id, position_id=pos.id, machine_id=pos.machine_id,
                                   feature="stroke_time", state="open", severity="warning",
                                   opened_at=datetime.now(timezone.utc) - timedelta(hours=age_h))
        # right token → 200: the wear of the virtual cylinder resets
        monkeypatch.setattr(settings, "demo_virtual_plant_control_token", "s3cret")
        db.add(alert(1))
        await db.commit()
        done = await run_once(db)
        assert len(done) == 1 and done[0]["virtual_plant_reset"] is True
        assert stub.calls[-1]["path"] == "/replace" and stub.calls[-1]["token"] == "s3cret"
        assert stub.calls[-1]["body"]["position"] == "montagem.fixacao.b3"

        # no/wrong token (the prod bug) → 403 is a failure: audited, and the position is left alone
        monkeypatch.setattr(settings, "demo_virtual_plant_control_token", "")
        db.add(alert(2))
        await db.commit()
        done = await run_once(db)
        assert len(done) == 1 and done[0]["virtual_plant_reset"] is False
        assert stub.calls[-1]["token"] is None
        fails = (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tid, AuditEvent.action == "demo_bot.virtual_plant_replace_failed"))).scalars().all()
        assert fails and fails[-1].entity_id == pos.id and fails[-1].data["detail"] == "HTTP 403"
        n_calls = len(stub.calls)
        db.add(alert(3))
        await db.commit()
        assert await run_once(db) == []            # backing off: no new replacement WO every 15 min
        assert len(stub.calls) == n_calls
    finally:
        stub.close()
        await db.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tid,
                                                  AuditEvent.action == "demo_bot.virtual_plant_replace_failed"))
        await db.execute(update(PredictiveAlert).where(PredictiveAlert.tenant_id == tid, PredictiveAlert.state.in_(
            ("open", "acknowledged"))).values(state="closed"))
        await db.commit()


async def test_enroll_code_cli_for_the_demo_gateway(demo, db, capsys):
    """operations.md §10.2 option A: the demo tenant has no admin who can log in, so the CLI issues the code."""
    from src.demo import main
    from src.models import Gateway
    from src.services import gateways as gs
    from src.services.demo.bootstrap import issue_enroll_code
    out = await issue_enroll_code(db)
    assert out["tenant_id"] == demo["tenant_id"] and out["gateway_id"] == demo["gateway_id"]
    db.expire_all()
    gw = await db.get(Gateway, out["gateway_id"])
    assert gw.enroll_code_hash == gs.sha256(gs.normalize_code(out["code"]))
    assert gw.enroll_expires_at > datetime.now(timezone.utc) + timedelta(hours=23)
    assert "enroll-code" in (Path(__file__).resolve().parents[1] / "src/demo.py").read_text()
    with pytest.raises(SystemExit):
        main(["enroll-code", "--help"])
    assert "DEMO_GATEWAY_ENROLL_CODE" not in capsys.readouterr().err
