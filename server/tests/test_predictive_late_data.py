"""Sprint 5 fix loop — predictive extraction (P-1/P-12) under real-path conditions:

* late data: readings that a gateway flushes from its store-and-forward buffer after the engine already stored the
  time windows as invalid are re-extracted (MQTT handler note → next run), and /recompute re-reads invalid windows;
* cost: while a stroke block is open the engine asks Influx for aggregates only (no raw rows are read and parsed
  on every tick — that starved MQTT ingestion in the same process);
* counter reset/rollover inside an open block and stroke monitors without a counter keep exact blocks on resume.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant

T = datetime(2027, 6, 7, 4, 0, tzinfo=timezone.utc)   # local midnight (America/Manaus): window anchor
N = 20


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T)
    monkeypatch.setattr(clock, "now", c)
    return c


async def _motor(ctx):
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    mach = ctx.machines["Transportador de Saída"]
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=mach, name="Motor LD", component_type=ComponentType.electric_motor)
        s.add(p)
        await s.flush()
        run = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="run LD", protocol=ProtocolType.mqtt,
                  address=f"ld/run/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.run_state)
        cur = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="corrente LD", unit="A",
                  protocol=ProtocolType.mqtt, address=f"ld/i/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.current)
        s.add_all([run, cur])
        await s.commit()
        return mach, p.id, run.id, cur.id


async def _current_monitor(client, ctx, pos, cur_id):
    return await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "current", "tag_id": cur_id, "window_hours": 8, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"data_gap_s": 172800, "baseline_min_windows": 3,
                                              "baseline_min_span_h": 0}})


async def _features(client, ctx, pos):
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", ctx.h("maintenance_planner"))
    return feats["series"][0]["points"]


def _payload(value: float, at: datetime, unit: str = "") -> bytes:
    return json.dumps({"value": value, "unit": unit, "ts": at.isoformat().replace("+00:00", "Z")}).encode()


async def test_time_windows_stored_invalid_are_reextracted_when_the_gateway_buffer_flushes(client, clk):
    from src.services import mqtt_subscriber
    from src.services.predictive import engine as pe
    ctx = await make_maint_tenant("LT1")
    mach, pos, run_id, cur_id = await _motor(ctx)
    m = await _current_monitor(client, ctx, pos, cur_id)
    # the uplink is down for a day: the engine runs and stores 3 windows without data (invalid)
    clk.at = T + timedelta(hours=25)
    r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert r["windows_added"] == 3
    assert [(p["i"], p["valid"]) for p in await _features(client, ctx, pos)] == [(1, False), (2, False), (3, False)]
    # the gateway reconnects and flushes its buffer through MQTT (source timestamps of yesterday)
    pe._late.clear()
    for tag, v, at, unit in [(run_id, 1.0, T, ""), (cur_id, 10.0, T, "A"), (cur_id, 11.0, T + timedelta(hours=8), "A"),
                             (cur_id, 12.0, T + timedelta(hours=16), "A")]:
        await mqtt_subscriber._handle_message(f"factory/{ctx.id}/sensors/{tag}", _payload(v, at, unit),
                                              received_at=clk.at)
    assert pe._late.get((ctx.id, mach)) == T
    clk.at = T + timedelta(hours=25, minutes=5)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    pts = await _features(client, ctx, pos)
    assert [(p["i"], p["valid"], p["x"]) for p in pts] == [(1, True, 10.0), (2, True, 11.0), (3, True, 12.0)]
    assert [p["u"] for p in pts] == pytest.approx([8.0, 16.0, 24.0])
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", ctx.h("maintenance_planner"))
    assert "late_from" not in (mon.get("status") or {})
    # the background run persists the notes of the other monitors too and then forgets them
    await pe.run_once(clk.at)
    assert (ctx.id, mach) not in pe._late


async def test_recompute_reextracts_invalid_windows_from_influx(client, clk):
    from src.services.influx import influx
    from src.services.predictive import engine as pe
    ctx = await make_maint_tenant("LT2")
    mach, pos, run_id, cur_id = await _motor(ctx)
    m = await _current_monitor(client, ctx, pos, cur_id)
    clk.at = T + timedelta(hours=17)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert [p["valid"] for p in await _features(client, ctx, pos)] == [False, False]
    # readings written late without passing through the MQTT handler (no note): only /recompute finds them
    await influx.write(ctx.id, run_id, 1.0, machine_id=mach, ts=T)
    await influx.write(ctx.id, cur_id, 9.5, machine_id=mach, unit="A", ts=T)
    pe._late.clear()
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["monitors"][0]["windows_reextracted"] == 2
    assert [(p["valid"], p["x"]) for p in await _features(client, ctx, pos)] == [(True, 9.5), (True, 9.5)]
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["monitors"][0]["windows_reextracted"] == 0 and rc["same"] is True


async def _cylinder(ctx, label: str, counter: bool = True, counter_max=2**32 - 1):
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
        ct_id = None
        if counter:
            ct = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name=f"ciclos {label}", unit="",
                     protocol=ProtocolType.mqtt, address=f"{label}/ct/{uuid.uuid4().hex[:6]}",
                     semantic_role=SemanticRole.cycle_counter, counter_max=counter_max)
            s.add(ct)
            await s.flush()
            ct_id = ct.id
        await s.commit()
        return mach, p.id, st.id, ct_id


async def _stroke_monitor(client, ctx, pos, st_id):
    return await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "stroke_time", "tag_id": st_id, "window_cycles": N, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0}})


async def test_open_block_reads_no_raw_rows_and_closes_exactly(client, clk, monkeypatch):
    from src.services.influx import influx
    from src.services.predictive import features as fx
    ctx = await make_maint_tenant("LT3")
    mach, pos, st_id, ct_id = await _cylinder(ctx, "lt3")
    m = await _stroke_monitor(client, ctx, pos, st_id)
    raw_reads = []
    real = fx.influx_points

    async def spy(tenant_id, tag_ids, start, stop):
        out = await real(tenant_id, tag_ids, start, stop)
        raw_reads.append(sum(len(v) for v in out.values()))
        return out

    monkeypatch.setattr(fx, "influx_points", spy)
    await influx.write(ctx.id, ct_id, 0.0, machine_id=mach, ts=T)

    async def cycles(a, b):
        for c in range(a, b + 1):
            at = T + timedelta(minutes=c)
            await influx.write(ctx.id, st_id, 300.0, machine_id=mach, unit="ms", ts=at)
            await influx.write(ctx.id, ct_id, float(c), machine_id=mach, ts=at + timedelta(milliseconds=1))

    await cycles(1, N - 1)                      # block 1 still open (19 of 20 cycles)
    for k in range(3):                          # several ticks while it stays open
        clk.at = T + timedelta(minutes=N + 3 + k)
        r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
        assert r["windows_added"] == 0
    assert raw_reads == []                      # only Influx aggregates while the block is open
    await cycles(N, 2 * N)
    clk.at = T + timedelta(minutes=2 * N + 5)
    r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert r["windows_added"] == 2 and len(raw_reads) == 1
    pts = await _features(client, ctx, pos)
    assert [(p["i"], p["n"]) for p in pts] == [(1, N), (2, N)]
    assert [datetime.fromisoformat(p["end_at"].replace("Z", "+00:00")) for p in pts] == \
        [T + timedelta(minutes=N * b, milliseconds=1) for b in (1, 2)]


async def test_counter_rollover_inside_an_open_block(client, clk):
    from src.services.influx import influx
    ctx = await make_maint_tenant("LT4")
    mach, pos, st_id, ct_id = await _cylinder(ctx, "lt4", counter_max=65535)
    m = await _stroke_monitor(client, ctx, pos, st_id)
    await influx.write(ctx.id, ct_id, 65530.0, machine_id=mach, ts=T)
    for c in range(1, 2 * N + 1):
        at = T + timedelta(minutes=c)
        await influx.write(ctx.id, st_id, 250.0, machine_id=mach, unit="ms", ts=at)
        await influx.write(ctx.id, ct_id, float((65530 + c) % 65536), machine_id=mach, ts=at + timedelta(milliseconds=1))
        if c in (N - 3, N, N + 7, 2 * N):    # engine runs inside and at the end of each block
            clk.at = at + timedelta(minutes=3)
            await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    pts = await _features(client, ctx, pos)
    assert [(p["i"], p["n"]) for p in pts] == [(1, N), (2, N)]


async def test_stroke_blocks_without_counter_resume_exactly(client, clk):
    from src.services.influx import influx
    ctx = await make_maint_tenant("LT5")
    mach, pos, st_id, _ = await _cylinder(ctx, "lt5", counter=False)
    m = await _stroke_monitor(client, ctx, pos, st_id)
    for b in range(3):
        for k in range(N):
            c = b * N + k + 1
            await influx.write(ctx.id, st_id, 200.0 + b, machine_id=mach, unit="ms", ts=T + timedelta(minutes=c))
        clk.at = T + timedelta(minutes=(b + 1) * N + 5)
        await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    pts = await _features(client, ctx, pos)
    assert [(p["i"], p["n"], p["x"]) for p in pts] == [(1, N, 200.0), (2, N, 201.0), (3, N, 202.0)]
