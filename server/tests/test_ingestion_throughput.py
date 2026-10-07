"""MQTT ingestion capacity (Sprint 5 review): a gateway flushing per-cycle stroke_time readings of several
cylinders plus their cycle counters, delivered the way the subscriber does it (Sprint 5b: the bounded pipeline —
per-tag ordered queues, fixed worker pool, batched Influx writes).

Checks that every reading reaches Influx (counters in order) and prints the measured rate and the number of Influx
write requests (recorded in docs/predictive.md, "Capacidade de ingestão"). Timing-based floors are left out on
purpose (shared runners); the coalescing itself is checked deterministically in the second test.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from maint_helpers import make_maint_tenant

CYLINDERS = 8
CYCLES = 100


async def test_stroke_and_counter_burst_is_ingested_with_batched_influx_writes():
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    from src.services import mqtt_subscriber
    from src.services.influx import influx
    from src.services.predictive import features as fx

    ctx = await make_maint_tenant("ING")
    mach = ctx.machines["Estação de Fixação"]
    strokes, counters = [], []
    async with AsyncSessionLocal() as s:
        for k in range(CYLINDERS):
            p = Position(tenant_id=ctx.id, machine_id=mach, name=f"Cilindro ING{k}",
                         component_type=ComponentType.pneumatic_cylinder)
            s.add(p)
            await s.flush()
            st = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name=f"curso ING{k}", unit="ms",
                     protocol=ProtocolType.mqtt, address=f"ing/st/{uuid.uuid4().hex[:6]}",
                     semantic_role=SemanticRole.stroke_time)
            s.add(st)
            if k < 2:
                ct = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name=f"ciclos ING{k}", unit="",
                         protocol=ProtocolType.mqtt, address=f"ing/ct/{uuid.uuid4().hex[:6]}",
                         semantic_role=SemanticRole.cycle_counter, counter_max=2**32 - 1)
                s.add(ct)
                await s.flush()
                counters.append(ct.id)
            await s.flush()
            strokes.append(st.id)
        await s.commit()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    t0 = now - timedelta(minutes=30)            # a 30-min gateway buffer flushed at once
    msgs = []
    for c in range(1, CYCLES + 1):
        at = t0 + timedelta(seconds=10 * c)
        msgs += [(tag, 200.0 + c % 7, at) for tag in strokes]
        msgs += [(tag, float(c), at + timedelta(milliseconds=1)) for tag in counters]
    req0 = influx.write_requests
    from src.services.ingest.pipeline import Item, OrderedPipeline
    pl = OrderedPipeline(mqtt_subscriber._process_item, capacity=2000, workers=12,
                         process_batch=mqtt_subscriber._process_items)
    pl.start()
    started = time.perf_counter()
    for tag, v, at in msgs:
        payload = json.dumps({"value": v, "unit": "", "ts": at.isoformat().replace("+00:00", "Z")}).encode()
        topic = f"factory/{ctx.id}/sensors/{tag}"
        await pl.put(Item(key=mqtt_subscriber._shard_key(topic), topic=topic, payload=payload, received_at=now))
    assert await pl.drain(timeout=300)
    await pl.stop()
    elapsed = time.perf_counter() - started
    rate = len(msgs) / elapsed
    requests = influx.write_requests - req0
    print(f"\ningestion: {len(msgs)} readings ({CYLINDERS} stroke tags, {len(counters)} counters) in {elapsed:.2f} s "
          f"= {rate:.0f} readings/s, {requests} Influx write requests")
    pts = await fx.influx_points(ctx.id, strokes + counters, t0, now + timedelta(seconds=1))
    assert all(len(pts[t]) == CYCLES for t in strokes + counters)
    assert all([v for _, v in pts[t]] == [float(c) for c in range(1, CYCLES + 1)] for t in counters)
    assert requests < len(msgs), f"{requests} write requests for {len(msgs)} readings: no coalescing at all"


async def test_concurrent_influx_writes_are_coalesced_and_each_caller_learns_its_own_failure(monkeypatch):
    from src.services.influx import influx
    from src.services.predictive import features as fx
    tenant, tag, bad = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    req0 = influx.write_requests
    await asyncio.gather(*[influx.write(tenant, tag, float(k), ts=t0 + timedelta(seconds=k)) for k in range(300)])
    assert influx.write_requests - req0 <= 3            # 1st alone, the other 299 while it is in flight
    pts = await fx.influx_points(tenant, [tag], t0, t0 + timedelta(minutes=6))
    assert [v for _, v in pts[tag]] == [float(k) for k in range(300)]
    # one rejected point fails only its own caller (the batch is retried point by point)
    real = influx._send

    async def picky(records):
        # records are line-protocol strings (Sprint 7 fast path) or dicts (non-finite values)
        if any((f"tag_id={bad}" in r) if isinstance(r, str) else r["tags"]["tag_id"] == bad for r in records):
            raise RuntimeError("422 field type conflict")
        await real(records)

    monkeypatch.setattr(influx, "_send", picky)
    res = await asyncio.gather(*[influx.write(tenant, bad if k == 3 else tag, 1000.0 + k, ts=t0 + timedelta(seconds=400 + k))
                                 for k in range(6)], return_exceptions=True)
    assert [isinstance(r, Exception) for r in res] == [False, False, False, True, False, False]
