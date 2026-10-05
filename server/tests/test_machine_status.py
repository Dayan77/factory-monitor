"""Machine status derived from data: last_seen_at, running, offline sweeper, WS broadcast."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from conftest import create_assets


class FakeWS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, msg):
        self.sent.append(msg)


@pytest.fixture
def ws_for():
    """Registers fake WebSocket clients on the real ConnectionManager."""
    from src.websocket.manager import manager
    registered = []

    async def _make(tenant_id):
        ws = FakeWS()
        await manager.connect(ws, tenant_id)
        registered.append((ws, tenant_id))
        return ws

    yield _make
    for ws, tid in registered:
        manager.disconnect(ws, tid)


async def _machine(db, machine_id):
    from src.models import Machine
    db.expire_all()
    return (await db.execute(select(Machine).where(Machine.id == machine_id))).scalar_one()


async def test_new_machine_has_null_last_seen(client, tenants):
    assets = await create_assets(client, tenants.a)
    assert assets.machine["status"] == "unknown"
    assert "last_seen_at" in assets.machine and assets.machine["last_seen_at"] is None


async def test_mark_seen_sets_running_and_last_seen(client, tenants, db):
    from src.services.machine_status import mark_seen

    assets = await create_assets(client, tenants.a)
    now = datetime.now(timezone.utc)
    change = await mark_seen(db, tenants.a.id, assets.tag["id"], now=now)
    assert change is not None
    assert change.machine_id == assets.machine["id"]
    assert (change.previous_status, change.status) == ("unknown", "running")
    m = await _machine(db, assets.machine["id"])
    assert m.status.value == "running" and m.last_seen_at == now

    # Within the write interval: no change, no write
    assert await mark_seen(db, tenants.a.id, assets.tag["id"], now=now + timedelta(seconds=2)) is None
    assert (await _machine(db, assets.machine["id"])).last_seen_at == now
    # After it: last_seen_at refreshed, still no status change
    later = now + timedelta(seconds=10)
    assert await mark_seen(db, tenants.a.id, assets.tag["id"], now=later) is None
    assert (await _machine(db, assets.machine["id"])).last_seen_at == later

    r = await client.get(f"/api/machines/{assets.machine['id']}", headers=tenants.a.headers("operator"))
    body = r.json()
    assert body["status"] == "running"
    assert datetime.fromisoformat(body["last_seen_at"]) == later


async def test_stopped_becomes_running_but_maintenance_and_error_are_kept(client, tenants, db):
    from src.services.machine_status import mark_seen

    h = tenants.a.headers("manager")
    for manual, expected in (("stopped", "running"), ("maintenance", "maintenance"), ("error", "error")):
        assets = await create_assets(client, tenants.a)
        await client.patch(f"/api/machines/{assets.machine['id']}", json={"status": manual}, headers=h)
        await mark_seen(db, tenants.a.id, assets.tag["id"])
        m = await _machine(db, assets.machine["id"])
        assert m.status.value == expected, manual
        assert m.last_seen_at is not None


async def test_mark_seen_ignores_foreign_unknown_and_unbound_tags(client, tenants, db):
    from src.services.machine_status import mark_seen

    assets = await create_assets(client, tenants.a)
    # tag of tenant A reported under tenant B's id → ignored
    assert await mark_seen(db, tenants.b.id, assets.tag["id"]) is None
    m = await _machine(db, assets.machine["id"])
    assert m.status.value == "unknown" and m.last_seen_at is None
    # unknown tag / malformed ids
    assert await mark_seen(db, tenants.a.id, str(uuid.uuid4())) is None
    assert await mark_seen(db, "nope", assets.tag["id"]) is None
    # tag without machine
    tag = (await client.post("/api/tags/", json={"name": "solta", "protocol": "mqtt", "address": "x"},
                             headers=tenants.a.headers("admin"))).json()
    assert await mark_seen(db, tenants.a.id, tag["id"]) is None


async def test_sweeper_marks_stale_machines_unknown(client, tenants, db):
    from src.models import Machine
    from src.services.machine_status import mark_seen, sweep_offline

    now = datetime.now(timezone.utc)
    stale = await create_assets(client, tenants.a)
    fresh = await create_assets(client, tenants.b)
    maint = await create_assets(client, tenants.a)
    await mark_seen(db, tenants.a.id, stale.tag["id"], now=now - timedelta(seconds=61))
    await mark_seen(db, tenants.b.id, fresh.tag["id"], now=now - timedelta(seconds=30))
    await mark_seen(db, tenants.a.id, maint.tag["id"], now=now - timedelta(seconds=300))
    await db.execute(update(Machine).where(Machine.id == maint.machine["id"]).values(status="maintenance"))
    await db.commit()

    changes = {c.machine_id: c for c in await sweep_offline(db, now=now, timeout_s=60)}
    assert stale.machine["id"] in changes
    c = changes[stale.machine["id"]]
    assert (c.tenant_id, c.previous_status, c.status) == (tenants.a.id, "running", "unknown")
    assert fresh.machine["id"] not in changes
    assert maint.machine["id"] not in changes
    assert (await _machine(db, stale.machine["id"])).status.value == "unknown"
    assert (await _machine(db, fresh.machine["id"])).status.value == "running"
    assert (await _machine(db, maint.machine["id"])).status.value == "maintenance"
    # Idempotent: a second sweep changes nothing for these machines
    again = {c.machine_id for c in await sweep_offline(db, now=now, timeout_s=60)}
    assert stale.machine["id"] not in again


async def test_running_without_any_data_is_swept(client, tenants, db):
    from src.services.machine_status import sweep_offline

    assets = await create_assets(client, tenants.a)
    await client.patch(f"/api/machines/{assets.machine['id']}", json={"status": "running"},
                       headers=tenants.a.headers("manager"))
    changes = {c.machine_id for c in await sweep_offline(db)}
    assert assets.machine["id"] in changes


async def test_mqtt_message_updates_status_and_broadcasts(client, tenants, db, ws_for, monkeypatch):
    """Full ingestion path: MQTT message → Influx + last_seen_at + WS machine_status."""
    from src.services import mqtt_subscriber
    from src.services.influx import influx

    written = []

    async def fake_write(**kw):
        written.append(kw)

    monkeypatch.setattr(influx, "write", fake_write)

    assets = await create_assets(client, tenants.a)
    ws_a = await ws_for(tenants.a.id)
    ws_b = await ws_for(tenants.b.id)
    topic = f"factory/{tenants.a.id}/sensors/{assets.tag['id']}"
    payload = json.dumps({"value": 42.5, "unit": "°C", "ts": "2026-10-05T10:00:00Z"}).encode()

    await mqtt_subscriber._handle_message(topic, payload)

    assert written and written[0]["tag_id"] == assets.tag["id"]
    types = [m.get("type") for m in ws_a.sent]
    assert types == ["sensor_reading", "machine_status"]
    reading, status_msg = ws_a.sent
    assert reading["tag_id"] == assets.tag["id"] and reading["value"] == 42.5
    assert status_msg["machine_id"] == assets.machine["id"]
    assert status_msg["machine_name"] == assets.machine["name"]
    assert status_msg["status"] == "running" and status_msg["previous_status"] == "unknown"
    assert status_msg["last_seen_at"] is not None and status_msg["timestamp"]
    assert ws_b.sent == []   # other tenant gets nothing

    # second reading: reading broadcast only, no new machine_status
    await mqtt_subscriber._handle_message(topic, payload)
    assert [m.get("type") for m in ws_a.sent] == ["sensor_reading", "machine_status", "sensor_reading"]

    r = await client.get(f"/api/machines/{assets.machine['id']}", headers=tenants.a.headers("operator"))
    assert r.json()["status"] == "running" and r.json()["last_seen_at"]


async def test_mqtt_rejects_non_uuid_topic(ws_for, tenants, monkeypatch):
    from src.services import mqtt_subscriber
    from src.services.influx import influx

    written = []

    async def fake_write(**kw):
        written.append(kw)

    monkeypatch.setattr(influx, "write", fake_write)
    ws = await ws_for(tenants.a.id)
    await mqtt_subscriber._handle_message(f'factory/{tenants.a.id}/sensors/x") or true', b'{"value": 1}')
    await mqtt_subscriber._handle_message('factory/not-a-uuid/sensors/also-not', b'{"value": 1}')
    assert written == [] and ws.sent == []


async def test_offline_sweeper_task_broadcasts(client, tenants, db, ws_for, monkeypatch):
    from src.config import settings
    from src.services import machine_status

    monkeypatch.setattr(settings, "machine_status_sweep_interval_s", 0.05)
    monkeypatch.setattr(settings, "machine_offline_timeout_s", 60)
    assets = await create_assets(client, tenants.b)
    await machine_status.mark_seen(db, tenants.b.id, assets.tag["id"],
                                   now=datetime.now(timezone.utc) - timedelta(seconds=120))
    ws = await ws_for(tenants.b.id)

    task = asyncio.create_task(machine_status.run_offline_sweeper())
    try:
        for _ in range(100):
            if any(m.get("machine_id") == assets.machine["id"] for m in ws.sent):
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    msg = next(m for m in ws.sent if m.get("machine_id") == assets.machine["id"])
    assert msg["type"] == "machine_status"
    assert msg["status"] == "unknown" and msg["previous_status"] == "running"


# ── Source timestamp (payload ts) / edge backlog replay ────────────────────

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_parse_source_ts():
    from src.services.mqtt_subscriber import parse_source_ts

    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_source_ts("2026-10-05T10:00:00.123Z", now) == datetime(
        2026, 10, 5, 10, 0, 0, 123000, tzinfo=timezone.utc)
    assert parse_source_ts("2026-10-05T07:00:00-03:00", now) == datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    assert parse_source_ts("2026-10-05T10:00:00", now) == datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    for bad in (None, "", "ontem", 12345, "2026-13-40T99:00:00Z"):
        assert parse_source_ts(bad, now) == now, bad
    assert parse_source_ts("2026-10-05T12:00:03Z", now) == now        # small skew clamped
    assert parse_source_ts("2030-01-01T00:00:00Z", now) == now        # bogus future clamped


async def test_mark_seen_old_reading_never_moves_last_seen_back_nor_sets_running(client, tenants, db):
    from src.services.machine_status import mark_seen

    assets = await create_assets(client, tenants.a)
    now = datetime.now(timezone.utc)
    # stale reading (2 h old) on an unknown machine: last_seen_at recorded, status stays unknown
    assert await mark_seen(db, tenants.a.id, assets.tag["id"], now=now,
                           seen_at=now - timedelta(hours=2)) is None
    m = await _machine(db, assets.machine["id"])
    assert m.status.value == "unknown" and m.last_seen_at == now - timedelta(hours=2)
    # fresh reading → running
    change = await mark_seen(db, tenants.a.id, assets.tag["id"], now=now, seen_at=now - timedelta(seconds=1))
    assert change is not None and change.status == "running"
    # an older replayed reading arriving later never rewinds last_seen_at
    await mark_seen(db, tenants.a.id, assets.tag["id"], now=now + timedelta(seconds=30),
                    seen_at=now - timedelta(minutes=30))
    assert (await _machine(db, assets.machine["id"])).last_seen_at == now - timedelta(seconds=1)


async def test_mqtt_replayed_reading_uses_source_ts_and_skips_live_side_effects(
        client, tenants, db, ws_for, monkeypatch):
    """Backlog flush: ts 2 h old → Influx at source ts, no WS push, no 'running', no alert."""
    from src.services import mqtt_subscriber
    from src.services.influx import influx

    written, evaluated = [], []

    async def fake_write(**kw):
        written.append(kw)

    async def fake_evaluate(**kw):
        evaluated.append(kw)

    monkeypatch.setattr(influx, "write", fake_write)
    monkeypatch.setattr(mqtt_subscriber, "evaluate", fake_evaluate)

    assets = await create_assets(client, tenants.a)
    ws = await ws_for(tenants.a.id)
    topic = f"factory/{tenants.a.id}/sensors/{assets.tag['id']}"
    received = datetime.now(timezone.utc)
    old = (received - timedelta(hours=2)).replace(microsecond=123000)

    await mqtt_subscriber._handle_message(
        topic, json.dumps({"value": 99.0, "ts": _iso(old)}).encode(), received_at=received)

    assert len(written) == 1 and written[0]["ts"] == old
    assert ws.sent == []
    assert evaluated == []
    m = await _machine(db, assets.machine["id"])
    assert m.status.value == "unknown" and m.last_seen_at == old

    # a live reading right after: source ts kept, WS push, running, rules evaluated
    live = received - timedelta(milliseconds=250)
    await mqtt_subscriber._handle_message(
        topic, json.dumps({"value": 98.0, "ts": _iso(live)}).encode(), received_at=received)
    assert written[-1]["ts"] == live
    assert [x["type"] for x in ws.sent] == ["sensor_reading", "machine_status"]
    assert datetime.fromisoformat(ws.sent[0]["timestamp"]) == live
    assert len(evaluated) == 1 and evaluated[0]["value"] == 98.0
    m = await _machine(db, assets.machine["id"])
    assert m.status.value == "running" and m.last_seen_at == live


async def test_mqtt_missing_or_future_ts_falls_back_to_receive_time(client, tenants, ws_for, monkeypatch):
    from src.services import mqtt_subscriber
    from src.services.influx import influx

    written = []

    async def fake_write(**kw):
        written.append(kw)

    monkeypatch.setattr(influx, "write", fake_write)
    assets = await create_assets(client, tenants.a)
    topic = f"factory/{tenants.a.id}/sensors/{assets.tag['id']}"
    received = datetime.now(timezone.utc)
    await mqtt_subscriber._handle_message(topic, b'{"value": 1}', received_at=received)
    await mqtt_subscriber._handle_message(
        topic, json.dumps({"value": 2, "ts": "2099-01-01T00:00:00Z"}).encode(), received_at=received)
    assert [w["ts"] for w in written] == [received, received]
