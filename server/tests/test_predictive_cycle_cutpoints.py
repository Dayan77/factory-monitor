"""Sprint 5b (QA): stroke-time cycle blocks — incremental extraction == full recompute, whatever the cut points.

Property (P-1/P-12): cutting the same Influx data into blocks of N cycles gives identical blocks (index, start, end,
number of readings, median) whether the engine

* runs once at the end (full extraction),
* runs at random instants while the data is there (incremental, resuming from the saved cursor each time),
* runs while the data arrives, with a random part of the readings delivered late (gateway store-and-forward flush)
  through the real MQTT handler — the late-data marks rewind the blocks they touch,
* or is asked for a full recompute (`/recompute` with `reextract_all`).

The data has the awkward cases on purpose: stroke and counter readings on the same timestamp (ties), the counter
published before the stroke of the same cycle, sparse counter readings (one reading covers several cycles, even
several blocks), missing stroke readings (invalid blocks), a counter rollover. The pure oracle `fx.cycle_blocks`
over all the raw readings must agree too. Seeded random cases (no hypothesis dependency): FM_CUTPOINT_SEEDS.
"""
from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant

T = datetime(2027, 8, 2, 4, 0, tzinfo=timezone.utc)
N = 20
CMAX = 4095
SEEDS = [int(s) for s in os.environ.get("FM_CUTPOINT_SEEDS", "11,23,37").split(",")]


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T)
    monkeypatch.setattr(clock, "now", c)
    return c


def gen(seed: int, counter: bool = True):
    """Readings [(tag_key, ts, value)] of ~7.5 blocks of N cycles, deterministic for the seed."""
    rnd = random.Random(seed)
    out = []
    t = T + timedelta(seconds=30)
    start = rnd.randint(CMAX - 60, CMAX - 10)       # the counter rolls over inside the data
    if counter:
        out.append(("ct", T, float(start)))         # reference reading at the installation
    c = 0
    total = int(N * 7.5)
    while c < total:
        c += 1
        t += timedelta(seconds=rnd.choice([5, 7, 13, 30, 61]))
        if rnd.random() > 0.08:                     # ~8 % strokes missing
            out.append(("st", t, 200.0 + rnd.choice([0.0, 0.5, -0.5, 1.0, 3.0]) + (c // N)))
        if not counter:
            continue
        if c == 3 * N + 4:
            c += 2 * N + 3                         # one counter step that covers more than 2 blocks
        r = rnd.random()
        if r < 0.25:
            continue                               # sparse counter: this cycle counted by a later reading
        if r < 0.45:
            at = t                                 # same instant as the stroke (tie)
        elif r < 0.55:
            at = t - timedelta(milliseconds=1)     # counter before the stroke of the same cycle
        else:
            at = t + timedelta(milliseconds=1)
        out.append(("ct", at, float((start + c) % (CMAX + 1))))
    return out


async def _cylinder(ctx, label: str, counter: bool):
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    mach = ctx.machines["Estação de Fixação"]
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=mach, name=f"Cilindro {label}",
                     component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        st = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name=f"curso {label}", unit="ms",
                 protocol=ProtocolType.mqtt, address=f"{label}/st/{uuid.uuid4().hex[:6]}",
                 semantic_role=SemanticRole.stroke_time)
        s.add(st)
        ids = {"st": None, "ct": None}
        if counter:
            ct = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name=f"ciclos {label}", unit="",
                     protocol=ProtocolType.mqtt, address=f"{label}/ct/{uuid.uuid4().hex[:6]}",
                     semantic_role=SemanticRole.cycle_counter, counter_max=CMAX)
            s.add(ct)
            await s.flush()
            ids["ct"] = ct.id
        await s.commit()
        ids["st"] = st.id
        return mach, p.id, ids


async def _monitor(client, ctx, pos, st_id):
    return await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "stroke_time", "tag_id": st_id, "window_cycles": N, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0}})


async def _blocks(client, ctx, pos):
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", ctx.h("maintenance_planner"))
    pts = feats["series"][0]["points"] if feats["series"] else []
    return [(p["i"], p["start_at"], p["end_at"], p["n"], p["valid"], p["x"]) for p in pts]


async def _write(ctx, mach, ids, rows):
    from src.services.influx import influx
    for key, ts, v in rows:
        await influx.write(ctx.id, ids[key], v, machine_id=mach, unit="ms" if key == "st" else "", ts=ts)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _oracle(rows, counter: bool):
    from src.services.predictive import features as fx
    st = sorted((ts, v) for k, ts, v in rows if k == "st")
    cum = None
    if counter:
        ct = sorted((ts, v) for k, ts, v in rows if k == "ct")
        cum = fx.cumulative_cycles([p for p in ct if p[0] > T], CMAX, ct[0][1])
    blocks, _ = fx.cycle_blocks(st, N, T, 1, cum, 0.0)
    out = []
    for b in blocks:
        valid = b.valid if counter else True
        valid = valid and b.n >= fx.MIN_BLOCK_READINGS * N
        out.append((b.k, _iso(b.start), _iso(b.end), b.n, valid, b.x if valid else None))
    return out


def _norm(blocks):
    return [(i, s.replace("+00:00", "Z"), e.replace("+00:00", "Z"), n, v, x) for i, s, e, n, v, x in blocks]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("counter", [True, False], ids=["counter", "no_counter"])
async def test_incremental_equals_full_for_random_cut_points(client, clk, seed, counter):
    ctx = await make_maint_tenant(f"CP{seed}{int(counter)}")
    rows = gen(seed, counter)
    end = max(ts for _, ts, _ in rows) + timedelta(minutes=10)
    rnd = random.Random(seed * 7 + 1)

    # A: full extraction (one run after everything is in Influx)
    mach, pos_a, ids_a = await _cylinder(ctx, f"a{seed}", counter)
    await _write(ctx, mach, ids_a, rows)
    m_a = await _monitor(client, ctx, pos_a, ids_a["st"])
    clk.at = end
    await api(client, "POST", f"/api/predictive/monitors/{m_a['id']}/process", ctx.h("maintenance_planner"))
    full = await _blocks(client, ctx, pos_a)
    assert len(full) >= 6
    # B: the same data, engine runs at random instants (resume from the cursor every time)
    _, pos_b, ids_b = await _cylinder(ctx, f"b{seed}", counter)
    await _write(ctx, mach, ids_b, rows)
    m_b = await _monitor(client, ctx, pos_b, ids_b["st"])
    span = (end - T).total_seconds()
    cuts = sorted({T + timedelta(seconds=rnd.uniform(0, span)) for _ in range(12)}) + [end]
    # also cut exactly at reading instants (+ GRACE: the horizon then sits on the reading)
    from src.services.predictive.engine import GRACE
    for _ in range(6):
        cuts.append(rnd.choice(rows)[1] + GRACE)
    for c in sorted(cuts):
        clk.at = c
        await api(client, "POST", f"/api/predictive/monitors/{m_b['id']}/process", ctx.h("maintenance_planner"))
    clk.at = end
    await api(client, "POST", f"/api/predictive/monitors/{m_b['id']}/process", ctx.h("maintenance_planner"))
    inc = await _blocks(client, ctx, pos_b)
    assert _norm([b[:] for b in inc]) == _norm(full)
    # the pure oracle over all raw readings agrees
    assert _norm(full) == _oracle(rows, counter)
    # full recompute from Influx reproduces the stored blocks (and the alerts)
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"),
                   json={"monitor_id": m_b["id"], "reextract_all": True})
    assert rc["monitors"][0]["windows_reextracted"] == 0 and rc["same"] is True
    assert _norm(await _blocks(client, ctx, pos_b)) == _norm(full)


@pytest.mark.parametrize("seed", SEEDS[:2])
async def test_late_readings_through_mqtt_rewind_the_blocks_they_touch(client, clk, seed):
    """Data arrives over time through the MQTT handler; ~15 % of the readings are held back by the "gateway" and
    flushed later (after the engine already cut their blocks). The end result equals the full extraction."""
    from src.services import mqtt_subscriber
    ctx = await make_maint_tenant(f"CL{seed}")
    rows = gen(seed, True)
    end = max(ts for _, ts, _ in rows) + timedelta(minutes=10)
    mach, pos_a, ids_a = await _cylinder(ctx, f"la{seed}", True)
    await _write(ctx, mach, ids_a, rows)
    m_a = await _monitor(client, ctx, pos_a, ids_a["st"])
    clk.at = end
    await api(client, "POST", f"/api/predictive/monitors/{m_a['id']}/process", ctx.h("maintenance_planner"))
    full = await _blocks(client, ctx, pos_a)

    _, pos_b, ids_b = await _cylinder(ctx, f"lb{seed}", True)
    m_b = await _monitor(client, ctx, pos_b, ids_b["st"])
    rnd = random.Random(seed * 13 + 5)
    held: list = []
    step = timedelta(minutes=20)
    t = T
    pending = sorted(rows, key=lambda r: r[1])
    k = 0
    rewinds = 0
    while t < end + step:
        t += step
        clk.at = t
        batch = []
        while k < len(pending) and pending[k][1] <= t:
            (held if rnd.random() < 0.15 and pending[k][1] > T else batch).append(pending[k])
            k += 1
        if held and rnd.random() < 0.3:             # the gateway flushes its buffer
            batch += held
            held = []
        for key, ts, v in batch:
            payload = json.dumps({"value": v, "unit": "", "ts": _iso(ts)}).encode()
            await mqtt_subscriber._handle_message(f"factory/{ctx.id}/sensors/{ids_b[key]}", payload,
                                                  received_at=clk.at)
        r = await api(client, "POST", f"/api/predictive/monitors/{m_b['id']}/process", ctx.h("maintenance_planner"))
        rewinds += r.get("late_windows", 0)
    for key, ts, v in held:
        payload = json.dumps({"value": v, "unit": "", "ts": _iso(ts)}).encode()
        await mqtt_subscriber._handle_message(f"factory/{ctx.id}/sensors/{ids_b[key]}", payload, received_at=clk.at)
    clk.at = t + step
    await api(client, "POST", f"/api/predictive/monitors/{m_b['id']}/process", ctx.h("maintenance_planner"))
    assert rewinds > 0                              # the case really happened
    assert _norm(await _blocks(client, ctx, pos_b)) == _norm(full)
