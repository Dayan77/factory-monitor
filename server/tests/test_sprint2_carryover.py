"""Carry-over from the Sprint 2 review: line production window alignment + position run_state,
unique position counter tag, lead rate-limit race and blank names, bounded clean shutdown."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from conftest import _make_tenant
from usage_helpers import add_position, add_tag, api, build_plant, publish


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


async def test_line_production_aligns_window_and_uses_position_run_state(client):
    """QA: start=10:30 summed the whole 10:00 bucket but divided by 30 min; the Linha de Montagem
    output conveyor (run_state on its motor position) had cadence_running_per_hour null."""
    t = await _tenant("LP")
    p = await build_plant(client, t, "LP")
    h = t.headers("admin")
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": p.machine["id"]})
    motor = await add_position(client, t, p, "Motor", component_type="electric_motor")
    run = await add_tag(client, t, p, "motor.run", "run_state", motor, data_type="bool")
    good = await add_tag(client, t, p, "good", "part_counter_good")
    T = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    for sec in range(0, 2 * 3600 + 1, 30):                      # running 2 h, heartbeat 30 s
        await publish(t.id, run["id"], 1, T + timedelta(seconds=sec))
    for k, v in enumerate([0, 60, 120, 180, 240]):              # +60 parts every 30 min
        await publish(t.id, good["id"], v, T + timedelta(minutes=30 * k, seconds=5))
    r = await api(client, "GET", f"/api/lines/{p.line['id']}/production", t.headers("operator"), params={
        "start": (T + timedelta(minutes=30)).isoformat(), "stop": (T + timedelta(hours=2)).isoformat()})
    assert datetime.fromisoformat(r["start"].replace("Z", "+00:00")) == T          # aligned to the bucket
    assert r["total"] == 180                                   # buckets T (60) and T+1h (120)
    assert abs(r["cadence_per_hour"] - 180 / 2.0) < 1e-9        # over the aligned 2 h, not 1.5 h
    assert r["cadence_running_per_hour"] is not None
    assert abs(r["cadence_running_per_hour"] - 180 / 2.0) < 1e-6   # 2 h of position run_state = 1
    assert sum(pt["good"] for pt in r["points"]) == r["total"]


async def test_counter_tag_cannot_count_two_positions(client):
    t = await _tenant("CT")
    h = t.headers("admin")
    p = await build_plant(client, t, "CT")
    a = await add_position(client, t, p, "A1")
    b = await add_position(client, t, p, "A2")
    cnt = await add_tag(client, t, p, "ciclos", "cycle_counter")
    linked = await add_tag(client, t, p, "a1.pulsos", "cycle_pulse", a)
    await api(client, "PATCH", f"/api/positions/{a['id']}", h, json={"counter_tag_id": cnt["id"]})
    await api(client, "PATCH", f"/api/positions/{a['id']}", h, json={"counter_tag_id": cnt["id"]})   # same: ok
    r = await client.patch(f"/api/positions/{b['id']}", headers=h, json={"counter_tag_id": cnt["id"]})
    assert r.status_code == 409 and "outra posição" in r.json()["detail"]
    r = await client.patch(f"/api/positions/{b['id']}", headers=h, json={"counter_tag_id": linked["id"]})
    assert r.status_code == 409
    r = await client.post("/api/positions/", headers=h, json={
        "machine_id": p.machine["id"], "name": "A3", "component_type": "pneumatic_cylinder",
        "counter_tag_id": cnt["id"]})
    assert r.status_code == 409
    # unlink → free again
    await api(client, "PATCH", f"/api/positions/{a['id']}", h, json={"counter_tag_id": None})
    await api(client, "PATCH", f"/api/positions/{b['id']}", h, json={"counter_tag_id": cnt["id"]})


def _lead(**kw):
    body = {"name": "Maria", "email": f"m.{uuid.uuid4().hex[:6]}@example.com", "consent": True, "website": ""}
    body.update(kw)
    return body


async def test_lead_blank_name_rejected(app):
    transport = httpx.ASGITransport(app=app, client=("203.0.113.90", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/public/leads", json=_lead(name="   "))
    assert r.status_code == 422


async def test_lead_rate_limit_holds_under_concurrency(app, monkeypatch):
    from src.config import settings
    from src.services import mailer

    async def fake_send(*a, **kw):
        return "smtp"

    monkeypatch.setattr(mailer, "send_email", fake_send)
    monkeypatch.setattr(settings, "leads_rate_limit_per_hour", 5)
    # widen the count → insert window so the race is deterministic without the lock
    from sqlalchemy.ext.asyncio import AsyncSession
    orig_commit = AsyncSession.commit

    async def slow_commit(self):
        await asyncio.sleep(0.2)
        return await orig_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", slow_commit)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.91", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        rs = await asyncio.gather(*[c.post("/api/public/leads", json=_lead()) for _ in range(12)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(202) == 5 and codes.count(429) == 7, codes


async def test_shutdown_is_bounded_even_with_stubborn_tasks():
    """Lifespan shutdown must finish even when a background task / MQTT reading ignores cancellation."""
    from src import main
    from src.services import mqtt_subscriber as ms

    async def stubborn():
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue                       # swallows cancellation (e.g. a hung client close)

    async def reading():
        await asyncio.sleep(3600)

    bg = asyncio.create_task(stubborn())
    rd = asyncio.create_task(reading())
    ms._inflight.add(rd)
    t0 = asyncio.get_running_loop().time()
    await main.shutdown_tasks([bg], timeout_s=0.5)
    assert asyncio.get_running_loop().time() - t0 < 3
    assert rd.cancelled()
    bg.cancel()
    ms._inflight.discard(rd)


async def test_uvicorn_exits_on_sigterm_with_pending_readings(tmp_path):
    """Real process: uvicorn with the app's lifespan (MQTT subscriber, detector) exits promptly on SIGTERM."""
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path
    server = Path(__file__).resolve().parent.parent
    env = {**os.environ, "MQTT_BROKER_HOST": "127.0.0.1", "SHUTDOWN_TIMEOUT_S": "2"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.main:app", "--port", "8187", "--log-level", "info",
                             "--no-proxy-headers"], cwd=server, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ok = False
        for _ in range(100):
            await asyncio.sleep(0.2)
            try:
                async with httpx.AsyncClient() as c:
                    if (await c.get("http://127.0.0.1:8187/api/health")).status_code == 200:
                        ok = True
                        break
            except httpx.HTTPError:
                pass
        assert ok, proc.stdout.read(4000) if proc.poll() is not None else "server did not start"
        proc.send_signal(signal.SIGTERM)
        for _ in range(75):
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.2)
        assert proc.poll() is not None, "uvicorn did not exit within 15 s of SIGTERM"
        out = proc.stdout.read().decode(errors="replace")
        assert "mqtt.subscribed" in out                      # the MQTT subscriber was really running
        assert "shutdown_abandoned_tasks" not in out, out[-2000:]   # …and stopped on cancel (no reconnect loop)
    finally:
        if proc.poll() is None:
            proc.kill()


async def test_subscriber_stops_when_cancel_is_swallowed_while_connecting(monkeypatch):
    """Seen live: SIGTERM right after startup cancelled the subscriber inside aiomqtt's connect, which
    swallowed the CancelledError; the task then subscribed and served forever (shutdown hung)."""
    from src.services import mqtt_subscriber as ms

    class Msg:
        topic, payload = "factory/x/sensors/y", b"{}"

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            try:
                await asyncio.sleep(0.3)                 # connecting…
            except asyncio.CancelledError:
                pass                                     # …library swallows the cancellation
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic, qos=0):
            return None

        @property
        def messages(self):
            async def gen():
                while True:
                    await asyncio.sleep(0.05)
                    yield Msg()
            return gen()

    async def no_handle(*a, **kw):
        return None

    monkeypatch.setattr(ms.aiomqtt, "Client", FakeClient)
    monkeypatch.setattr(ms, "_handle_message", no_handle)
    task = asyncio.create_task(ms.run_subscriber())
    await asyncio.sleep(0.1)
    task.cancel()
    done, _ = await asyncio.wait([task], timeout=2)
    assert task in done and task.cancelled()


async def test_subscriber_stops_on_mqtt_error_raised_by_cancel(monkeypatch):
    """aiomqtt converts a cancel during iteration into MqttError: must not reconnect."""
    from src.services import mqtt_subscriber as ms
    connects = []

    class FakeClient:
        def __init__(self, **kw):
            connects.append(1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic, qos=0):
            return None

        @property
        def messages(self):
            async def gen():
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    raise ms.aiomqtt.MqttError("Disconnected during message iteration")
                yield None
            return gen()

    monkeypatch.setattr(ms.aiomqtt, "Client", FakeClient)
    task = asyncio.create_task(ms.run_subscriber())
    await asyncio.sleep(0.1)
    task.cancel()
    done, _ = await asyncio.wait([task], timeout=2)
    assert task in done and task.cancelled() and len(connects) == 1
