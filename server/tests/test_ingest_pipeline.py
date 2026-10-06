"""Sprint 5b — MQTT ingestion as a bounded pipeline (QA: data loss + no backpressure under F1 at 500×).

* OrderedPipeline: per-key FIFO order with parallel keys, fixed worker pool, bounded readings in flight
  (put() blocks), acknowledgements in receive order and only once a reading is finished (its write included),
  a delivery of an old connection is never acknowledged on the new one;
* no silent drops: a valid reading that still fails after the retries is a dead letter (table + metric), and
  replaying it stores it; an Influx outage is retried as a batch (backpressure) and nothing is lost;
* persistent session (real broker): readings published while the server is down are delivered when it comes back
  under the same client id, and a reading taken but not finished before a crash is redelivered.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import create_assets


def _item(key, n, **kw):
    from src.services.ingest.pipeline import Item
    return Item(key=key, topic=f"factory/t/sensors/{key}", payload=json.dumps({"n": n}).encode(),
                received_at=datetime.now(timezone.utc), **kw)


async def test_pipeline_orders_per_key_bounds_inflight_and_acks_in_receive_order():
    from src.services.ingest.pipeline import OrderedPipeline
    seen: dict[str, list[int]] = {}
    active = {"n": 0, "max": 0, "keys": set(), "max_keys": 0}
    acks: list[tuple[str, int]] = []

    async def process(item):
        n = json.loads(item.payload)["n"]
        assert item.key not in active["keys"]          # one reading of a key at a time
        active["keys"].add(item.key)
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        await asyncio.sleep(0.001 * (hash((item.key, n)) % 3))
        seen.setdefault(item.key, []).append(n)
        active["n"] -= 1
        active["keys"].discard(item.key)

        async def write():                              # the Influx write finishes after the tag's turn
            await asyncio.sleep(0.002 * (n % 4))
        return write()

    pl = OrderedPipeline(process, capacity=16, workers=3, acker=lambda it: acks.append((it.key, it.mid)),
                         batch_per_turn=4)
    pl.start()
    gen = pl.new_connection()
    order = []
    max_inflight = 0
    for n in range(100):
        for key in ("a", "b", "c", "d", "e"):
            mid = len(order) + 1
            order.append((key, mid))
            await pl.put(_item(key, n, mid=mid, qos=1, gen=gen))
            max_inflight = max(max_inflight, pl.inflight)
    assert await pl.drain(timeout=30)
    await pl.stop()
    assert all(v == list(range(100)) for v in seen.values()) and len(seen) == 5
    assert active["max"] <= 3                           # fixed worker pool
    assert max_inflight <= 16                           # bounded: put() waited for room
    assert acks == order                                # every delivery acknowledged once, in receive order


async def test_pipeline_does_not_ack_before_the_write_finishes_nor_on_a_new_connection():
    from src.services.ingest.pipeline import OrderedPipeline
    release = asyncio.Event()
    acks = []

    async def process(item):
        async def write():
            await release.wait()
        return write() if item.key == "slow" else None

    pl = OrderedPipeline(process, capacity=8, workers=2, acker=lambda it: acks.append(it.mid))
    pl.start()
    g1 = pl.new_connection()
    await pl.put(_item("slow", 1, mid=1, qos=1, gen=g1))
    await pl.put(_item("fast", 1, mid=2, qos=1, gen=g1))
    await asyncio.sleep(0.05)
    assert acks == []                     # 2 is done but 1 (received first) is still writing: in-order PUBACK
    g2 = pl.new_connection()              # reconnect: 1 and 2 belong to the old session (broker redelivers)
    await pl.put(_item("fast", 2, mid=1, qos=1, gen=g2))
    release.set()
    assert await pl.drain(timeout=5)
    await pl.stop()
    assert acks == [1]                    # only the delivery of the current connection
    assert pl.processed == 3


async def test_reading_that_keeps_failing_becomes_a_dead_letter_and_replays(client, tenants, monkeypatch):
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import IngestDeadLetter
    from src.services import mqtt_subscriber as ms
    from src.services.ingest import dead_letter, transient
    from src.services.metrics import INGEST_DEAD_LETTERS
    from src.services.predictive import features as fx
    monkeypatch.setattr(ms, "RETRY_FIRST_S", 0.01)
    monkeypatch.setattr(ms, "RETRY_MAX_S", 0.02)
    monkeypatch.setattr(transient, "RETRY_FIRST_S", 0.01)
    monkeypatch.setattr(ms.settings, "mqtt_transient_retry_s", 0.2)
    a = await create_assets(client, tenants.a)
    topic = f"factory/{tenants.a.id}/sensors/{a.tag['id']}"
    at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
    payload = json.dumps({"value": 42.5, "unit": "", "ts": at.isoformat()}).encode()
    real_lookup = ms._lookup_tag

    async def db_down(tenant_id, tag_id):
        raise ConnectionRefusedError("postgres down")
    monkeypatch.setattr(ms, "_lookup_tag", db_down)
    before = INGEST_DEAD_LETTERS.labels("lookup")._value.get()
    await ms._handle_message(topic, payload)
    assert INGEST_DEAD_LETTERS.labels("lookup")._value.get() == before + 1
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(IngestDeadLetter).where(IngestDeadLetter.tag_id == a.tag["id"]))).scalars().all()
    assert len(rows) == 1 and rows[0].stage == "lookup" and rows[0].tenant_id == tenants.a.id
    assert bytes(rows[0].payload) == payload and "postgres down" in rows[0].error
    assert (await fx.influx_points(tenants.a.id, [a.tag["id"]], at, at + timedelta(seconds=1)))[a.tag["id"]] == []
    # the database is back: the operator replays the dead letters of the tenant
    monkeypatch.setattr(ms, "_lookup_tag", real_lookup)
    n = await dead_letter.replay(lambda t, p, r: ms._handle_message(t, p, received_at=r), tenants.a.id)
    assert n == 1
    pts = await fx.influx_points(tenants.a.id, [a.tag["id"]], at, at + timedelta(seconds=1))
    assert [v for _, v in pts[a.tag["id"]]] == [42.5]
    # tenant isolation of the replay: tenant B's replay does not touch A's letters (already replayed anyway)
    assert await dead_letter.replay(lambda t, p, r: ms._handle_message(t, p, received_at=r), tenants.b.id) == 0


async def test_usage_failure_is_dead_lettered_not_swallowed(client, tenants, monkeypatch):
    """Before: a usage processing error was logged as a warning and the counter reading was lost for the
    accumulators. Now: retried if transient, else dead-lettered (the Influx point is still written)."""
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import IngestDeadLetter
    from src.services import mqtt_subscriber as ms
    from src.services.metrics import INGEST_DEAD_LETTERS
    a = await create_assets(client, tenants.a)
    r = await client.post("/api/tags/", json={"machine_id": a.machine["id"], "name": "Ciclos DL", "protocol": "mqtt",
                                              "address": f"dl/{uuid.uuid4().hex[:6]}", "semantic_role": "cycle_counter",
                                              "counter_max": 65535}, headers=tenants.a.headers("admin"))
    assert r.status_code == 201, r.text
    ct = r.json()

    async def broken(*args, **kw):
        raise RuntimeError("bug in usage")
    monkeypatch.setattr(ms, "process_readings", broken)
    before = INGEST_DEAD_LETTERS.labels("usage")._value.get()
    at = datetime.now(timezone.utc).replace(microsecond=0)
    await ms._handle_message(f"factory/{tenants.a.id}/sensors/{ct['id']}",
                             json.dumps({"value": 5, "ts": at.isoformat()}).encode())
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(IngestDeadLetter).where(IngestDeadLetter.tag_id == ct["id"]))).scalars().all()
    assert [r_.stage for r_ in rows] == ["usage"] and "bug in usage" in rows[0].error
    assert INGEST_DEAD_LETTERS.labels("usage")._value.get() == before + 1
    from src.services.predictive import features as fx
    pts = await fx.influx_points(tenants.a.id, [ct["id"]], at, at + timedelta(seconds=1))
    assert [v for _, v in pts[ct["id"]]] == [5.0]          # the point itself was stored


async def test_influx_outage_is_retried_as_a_batch_and_nothing_is_lost(client, tenants, monkeypatch):
    from src.services import mqtt_subscriber as ms
    from src.services.influx import influx
    from src.services.ingest import transient
    from src.services.ingest.pipeline import Item, OrderedPipeline
    from src.services.metrics import INFLUX_WRITE_RETRIES
    from src.services.predictive import features as fx
    monkeypatch.setattr(transient, "RETRY_FIRST_S", 0.05)
    a = await create_assets(client, tenants.a)
    real = influx._send
    down = {"until": asyncio.get_running_loop().time() + 0.6, "calls": 0}

    async def flaky(records):
        down["calls"] += 1
        if asyncio.get_running_loop().time() < down["until"]:
            raise ConnectionRefusedError("influx restarting")
        await real(records)
    monkeypatch.setattr(influx, "_send", flaky)
    r0 = INFLUX_WRITE_RETRIES._value.get()
    pl = OrderedPipeline(ms._process_item, capacity=50, workers=4)
    pl.start()
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    topic = f"factory/{tenants.a.id}/sensors/{a.tag['id']}"
    for k in range(200):
        payload = json.dumps({"value": float(k), "ts": (t0 + timedelta(milliseconds=10 * k)).isoformat()}).encode()
        await pl.put(Item(key=ms._shard_key(topic), topic=topic, payload=payload,
                          received_at=datetime.now(timezone.utc)))
    assert await pl.drain(timeout=60)
    await pl.stop()
    assert INFLUX_WRITE_RETRIES._value.get() > r0
    assert down["calls"] < 60                    # batch retries, not one retry storm per reading
    pts = await fx.influx_points(tenants.a.id, [a.tag["id"]], t0, t0 + timedelta(seconds=3))
    assert [v for _, v in pts[a.tag["id"]]] == [float(k) for k in range(200)]


def _broker():
    return os.environ.get("MQTT_BROKER_HOST", "localhost"), int(os.environ.get("MQTT_BROKER_PORT", "1883"))


def _broker_up() -> bool:
    import socket
    try:
        with socket.create_connection(_broker(), timeout=1):
            return True
    except OSError:
        return False


async def test_persistent_session_keeps_readings_published_while_the_server_is_down(monkeypatch):
    """clean_session=False + stable client id: the broker queues QoS 1 readings for the server while it is down
    (or restarting) and redelivers the ones it had taken but not finished; nothing is lost."""
    if not _broker_up():
        pytest.fail("MQTT broker not reachable")
    import aiomqtt
    from src.services import mqtt_subscriber as ms
    tenant, tag = str(uuid.uuid4()), str(uuid.uuid4())
    topic = f"factory/{tenant}/sensors/{tag}"
    cid = f"fm-test-persist-{uuid.uuid4().hex[:8]}"
    got: list[int] = []
    stuck = {"on": True}

    async def process(t, raw, received_at):
        if t != topic:
            return None
        i = json.loads(raw)["i"]
        if stuck["on"] and i == 150:
            await asyncio.sleep(3600)             # the "crash": taken from the broker, never finished
        got.append(i)
        return None

    async def _items(items):
        return [await process(it.topic, it.payload, it.received_at) for it in items]
    monkeypatch.setattr(ms, "_process_items", _items)
    monkeypatch.setattr(ms.settings, "mqtt_client_id", cid)
    monkeypatch.setattr(ms.settings, "mqtt_persistent_session", True)
    host, port = _broker()
    try:
        sub = asyncio.create_task(ms.run_subscriber())
        await asyncio.sleep(1.0)
        async with aiomqtt.Client(hostname=host, port=port) as pub:
            for i in range(200):
                await pub.publish(topic, json.dumps({"i": i, "value": 1}).encode(), qos=1)
            for _ in range(100):
                if len(got) >= 150:
                    break
                await asyncio.sleep(0.05)
            sub.cancel()                                     # server goes down with 150 stuck
            await asyncio.gather(sub, return_exceptions=True)
            for i in range(200, 300):                        # published while it is down
                await pub.publish(topic, json.dumps({"i": i, "value": 1}).encode(), qos=1)
        stuck["on"] = False
        sub = asyncio.create_task(ms.run_subscriber())      # back, same client id
        for _ in range(200):
            if set(range(300)) <= set(got):
                break
            await asyncio.sleep(0.05)
        sub.cancel()
        await asyncio.gather(sub, return_exceptions=True)
    finally:
        # drop the persistent session from the shared broker
        async with aiomqtt.Client(hostname=host, port=port, identifier=cid, clean_session=True):
            pass
    assert set(got) == set(range(300)), sorted(set(range(300)) - set(got))[:20]
    # at-least-once: the unfinished deliveries are redelivered; order per tag restarts at the first one not acked
    assert got[:150] == list(range(150))
