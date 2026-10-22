"""Sprint 7 — MQTT ingestion at scale (docs/performance.md §3): the changes that took one ingestion process from
~250 to ~3 000 readings/s must not change a single number.

* many tags of a tenant in ONE batch (pipeline turn of up to MQTT_KEYS_PER_TURN tags → one usage transaction, one
  machine-status statement, one Influx request) give exactly the accumulators, tag states, machine status and Influx
  points of the same readings processed one by one;
* the multi-key turn keeps the per-tag order and acknowledgement order, and a stuck tenant does not hold another
  tenant's readings taken in the same turn;
* the receive loop takes queued messages without one event-loop round trip each and still stops on a disconnect;
* write_many: one future per batch, a rejected point fails its batch's future (dead letters), never silently;
* mark_seen_many = mark_seen per machine (status, last_seen_at, tenant isolation);
* the Influx line protocol built by hand is byte-for-byte the client library's.
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from usage_helpers import add_position, add_tag, build_plant

UTC = timezone.utc


def _topic(tenant_id, tag_id):
    return f"factory/{tenant_id}/sensors/{tag_id}"


def _payload(v, ts):
    return json.dumps({"value": v, "ts": ts.isoformat().replace("+00:00", "Z")}).encode()


async def _twin_machine(client, t, label):
    p = await build_plant(client, t, label)
    pos = await add_position(client, t, p, f"Cil {label}")
    run = await add_tag(client, t, p, f"run {label}", "run_state", data_type="bool")
    fault = await add_tag(client, t, p, f"fault {label}", "fault_state")
    parts = await add_tag(client, t, p, f"parts {label}", "part_counter_total", counter_max=65535)
    cyc = await add_tag(client, t, p, f"cyc {label}", "cycle_counter", position=pos, counter_max=65535)
    temp = await add_tag(client, t, p, f"temp {label}", "analog", data_type="float")
    return p, {"run": run["id"], "fault": fault["id"], "parts": parts["id"], "cyc": cyc["id"], "temp": temp["id"]}


def _stream(tags, t0, n):
    """n seconds of readings of one machine, in arrival order (all tags each second)."""
    out = []
    for k in range(n):
        ts = t0 + timedelta(seconds=k)
        running = not (40 <= k < 70)
        out += [(tags["run"], 1.0 if running else 0.0, ts), (tags["fault"], 3.0 if 45 <= k < 60 else 0.0, ts),
                (tags["parts"], float(k // 3), ts), (tags["cyc"], float(k // 2), ts), (tags["temp"], 20.0 + k % 7, ts)]
    return out


async def _snapshot(tenant_id, machine_id, tags):
    from src.db import AsyncSessionLocal
    from src.models import Machine, Position, TagState, UsageBucket, UsageTotal
    async with AsyncSessionLocal() as s:
        pos = [x for (x,) in (await s.execute(select(Position.id).where(Position.machine_id == machine_id))).all()]
        ents = {machine_id: "M", **{p: "P" for p in pos}}
        tot = sorted((sc, ents[e], m, round(v, 6)) for sc, e, m, v in (await s.execute(select(
            UsageTotal.scope, UsageTotal.entity_id, UsageTotal.measure, UsageTotal.value).where(
            UsageTotal.tenant_id == tenant_id, UsageTotal.entity_id.in_(list(ents))))).all())
        bk = sorted((sc, ents[e], m, b, round(v, 6)) for sc, e, m, b, v in (await s.execute(select(
            UsageBucket.scope, UsageBucket.entity_id, UsageBucket.measure, UsageBucket.bucket_start, UsageBucket.value)
            .where(UsageBucket.tenant_id == tenant_id, UsageBucket.entity_id.in_(list(ents))))).all())
        rev = {v: k for k, v in tags.items()}
        st = sorted((rev[tid], lv, lt, pv) for tid, lv, lt, pv in (await s.execute(select(
            TagState.tag_id, TagState.last_value, TagState.last_ts, TagState.pending_value).where(
            TagState.tag_id.in_(list(tags.values()))))).all())
        m = (await s.execute(select(Machine.status, Machine.last_seen_at).where(Machine.id == machine_id))).one()
    return tot, bk, st, (str(m[0]), m[1])


async def test_batched_turns_equal_reading_by_reading(client, tenants, monkeypatch):
    from influx_capture import capture_writes
    from src.services import mqtt_subscriber as ms
    from src.services.ingest.pipeline import Item, OrderedPipeline
    t = tenants.a
    pa, ta = await _twin_machine(client, t, "BAT")
    pb, tb = await _twin_machine(client, t, "ONE")
    writes = capture_writes(monkeypatch, ms.influx)
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=100)
    recv = datetime.now(UTC)
    # B: one reading at a time (the Sprint 5b path)
    for tag, v, ts in _stream(tb, t0, 90):
        await ms._handle_message(_topic(t.id, tag), _payload(v, ts), received_at=recv)
    # A: the same stream through the pipeline, many tags per turn (one usage transaction per turn)
    calls = {"many": 0}
    real_many = ms.process_many

    async def counting(*a, **kw):
        calls["many"] += 1
        return await real_many(*a, **kw)
    monkeypatch.setattr(ms, "process_many", counting)
    pl = OrderedPipeline(ms._process_item, capacity=5000, workers=2, process_batch=ms._process_items,
                         keys_per_turn=128, group_of=lambda k: k[0])
    for tag, v, ts in _stream(ta, t0, 90):
        topic = _topic(t.id, tag)
        await pl.put(Item(key=ms._shard_key(topic), topic=topic, payload=_payload(v, ts), received_at=recv))
    pl.start()                                   # everything queued first: turns of 5 tags × many readings
    assert await pl.drain(timeout=120)
    await pl.stop()
    assert calls["many"] >= 1                    # really went through the multi-tag transaction

    sa, sb = await _snapshot(t.id, pa.machine["id"], ta), await _snapshot(t.id, pb.machine["id"], tb)
    assert sa[0] == sb[0] and sa[0]              # usage totals (machine + position)
    assert sa[1] == sb[1]                        # hourly buckets
    assert sa[2] == sb[2]                        # tag states (counters, run state)
    assert sa[3][0] == sb[3][0]                  # derived machine status
    # last_seen_at: written at most every machine_last_seen_write_interval_s (both paths throttle it)
    from src.config import settings
    last = t0 + timedelta(seconds=89)
    for snap in (sa, sb):
        assert timedelta(0) <= last - snap[3][1] <= timedelta(seconds=settings.machine_last_seen_write_interval_s)
    # Influx: every reading once, same values per tag (order per tag kept)
    by_tag = {}
    for w in writes:
        by_tag.setdefault(w["tag_id"], []).append((w["ts"], w["value"]))
    for name in ta:
        assert by_tag[ta[name]] == by_tag[tb[name]] or [v for _, v in by_tag[ta[name]]] == \
            [v for _, v in by_tag[tb[name]]]
        assert [ts for ts, _ in by_tag[ta[name]]] == sorted(ts for ts, _ in by_tag[ta[name]])
        assert len(by_tag[ta[name]]) == 90


async def test_multi_key_turn_keeps_order_and_isolates_a_stuck_tenant():
    from src.services.ingest.pipeline import Item, OrderedPipeline
    order: dict = {}
    acked: list = []
    gate = asyncio.Event()

    async def batch(items):
        if items[0].key[0] == "stuck":
            await gate.wait()
        for it in items:
            order.setdefault(it.key, []).append(json.loads(it.payload)["n"])
        return [None] * len(items)

    pl = OrderedPipeline(lambda it: None, capacity=1000, workers=1, process_batch=batch, keys_per_turn=64,
                         group_of=lambda k: k[0], acker=lambda it: acked.append((it.key, json.loads(it.payload)["n"])))
    for n in range(5):
        for key in (("stuck", "x"), ("ok", "a"), ("ok", "b")):
            await pl.put(Item(key=key, topic="t", payload=json.dumps({"n": n}).encode(),
                              received_at=datetime.now(UTC), qos=1))
    pl.start()
    for _ in range(200):                         # tenant "ok" finishes although the same turn holds "stuck"
        if len(order.get(("ok", "b"), [])) == 5:
            break
        await asyncio.sleep(0.01)
    assert order[("ok", "a")] == list(range(5)) and order[("ok", "b")] == list(range(5))
    assert ("stuck", "x") not in order
    gate.set()
    assert await pl.drain(timeout=5)
    await pl.stop()
    assert order[("stuck", "x")] == list(range(5))
    for key in (("stuck", "x"), ("ok", "a"), ("ok", "b")):
        assert [n for k, n in acked if k == key] == list(range(5))


async def test_drain_messages_takes_queued_messages_and_stops_on_disconnect():
    from src.services import mqtt_subscriber as ms

    class FakeClient:
        def __init__(self):
            self._queue = asyncio.Queue()
            self._disconnected = asyncio.get_running_loop().create_future()
            self.anext_calls = 0
            outer = self

            class It:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    outer.anext_calls += 1
                    get = asyncio.ensure_future(outer._queue.get())
                    done, _ = await asyncio.wait({get, outer._disconnected}, return_when=asyncio.FIRST_COMPLETED)
                    if get in done:
                        return get.result()
                    get.cancel()
                    raise RuntimeError("Disconnected during message iteration")
            self.messages = It()

    c = FakeClient()
    for k in range(500):
        c._queue.put_nowait(k)
    got = []
    agen = ms._drain_messages(c)
    for _ in range(500):
        got.append(await agen.__anext__())
    assert got == list(range(500)) and c.anext_calls == 0      # no task + wait per queued message
    c._disconnected.set_exception(ConnectionError("broker gone"))
    with pytest.raises(RuntimeError):
        await agen.__anext__()


async def test_write_many_one_future_and_a_rejected_point_fails_its_batch(monkeypatch):
    from src.services.influx import influx, line_protocol
    from src.services.predictive import features as fx
    tenant, good, bad = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    t0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    lines = [line_protocol(tenant, good, "", "", float(k), t0 + timedelta(seconds=k)) for k in range(50)]
    await influx.write_many(lines)
    pts = await fx.influx_points(tenant, [good], t0, t0 + timedelta(minutes=2))
    assert [v for _, v in pts[good]] == [float(k) for k in range(50)]
    real = influx._send

    async def picky(records):
        if any(f"tag_id={bad}" in r for r in records):
            raise RuntimeError("422 field type conflict")
        await real(records)
    monkeypatch.setattr(influx, "_send", picky)
    ok = influx.write_many([line_protocol(tenant, good, "", "", 99.0, t0 + timedelta(seconds=60))])
    ko = influx.write_many([line_protocol(tenant, good, "", "", 98.0, t0 + timedelta(seconds=61)),
                            line_protocol(tenant, bad, "", "", 1.0, t0 + timedelta(seconds=61))])
    res = await asyncio.gather(ok, ko, return_exceptions=True)
    assert res[0] is None and isinstance(res[1], RuntimeError)


async def test_mark_seen_many_equals_mark_seen_and_isolates_tenants(client, tenants):
    from src.db import AsyncSessionLocal
    from src.models import Machine
    from src.services.machine_status import mark_seen_many
    a = await build_plant(client, tenants.a, "MSA")
    ta = await add_tag(client, tenants.a, a, "temp MSA", "analog", data_type="float")
    b = await build_plant(client, tenants.b, "MSB")
    tb = await add_tag(client, tenants.b, b, "temp MSB", "analog", data_type="float")
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        # tenant A's call carries tenant B's tag: ignored (fail closed), B's machine untouched
        changes = await mark_seen_many(s, tenants.a.id, [(ta["id"], now - timedelta(seconds=2), now),
                                                        (tb["id"], now, now)])
        await s.commit()
    assert [(c.machine_id, c.status, c.previous_status) for c in changes] == [(a.machine["id"], "running", "unknown")]
    async with AsyncSessionLocal() as s:
        ma = await s.get(Machine, a.machine["id"])
        mb = await s.get(Machine, b.machine["id"])
        assert ma.last_seen_at == now - timedelta(seconds=2) and str(ma.status.value) == "running"
        assert mb.last_seen_at is None
        # an older reading never moves last_seen_at back; a replay (old) never flips the status
        assert await mark_seen_many(s, tenants.a.id, [(ta["id"], now - timedelta(hours=1), now)]) == []
        await s.commit()
        await s.refresh(ma)
        assert ma.last_seen_at == now - timedelta(seconds=2)


def test_line_protocol_is_the_client_library_one():
    from influxdb_client import Point
    from src.services.influx import line_protocol
    t = datetime(2026, 10, 7, 13, 1, 2, 345678, tzinfo=UTC)
    for unit in ("", "°C", "a b,c=d", "x\\"):
        for v in (1.0, 2.5, 1e-7, 123456789.123, 3, -0.0):
            for machine in (str(uuid.uuid4()), ""):
                a, b = str(uuid.uuid4()), str(uuid.uuid4())
                rec = {"measurement": "sensors", "tags": {"tenant_id": a, "tag_id": b, "machine_id": machine,
                                                          "unit": unit},
                       "fields": {"value": float(v)}, "time": t.isoformat()}
                assert line_protocol(a, b, machine, unit, v, t) == Point.from_dict(rec).to_line_protocol()
    assert line_protocol("a", "b", "", "", math.nan, t) is None
