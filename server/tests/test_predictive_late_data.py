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
    from src.services.ingest import watermarks
    for tag, v, at, unit in [(run_id, 1.0, T, ""), (cur_id, 10.0, T, "A"), (cur_id, 11.0, T + timedelta(hours=8), "A"),
                             (cur_id, 12.0, T + timedelta(hours=16), "A")]:
        await mqtt_subscriber._handle_message(f"factory/{ctx.id}/sensors/{tag}", _payload(v, at, unit),
                                              received_at=clk.at)
    # the ingestion noted the late range per tag (written to ingest_watermarks before the engine looks)
    assert min(e[2] for k, e in watermarks._pending.items() if k[0] == ctx.id) == T
    clk.at = T + timedelta(hours=25, minutes=5)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    pts = await _features(client, ctx, pos)
    assert [(p["i"], p["valid"], p["x"]) for p in pts] == [(1, True, 10.0), (2, True, 11.0), (3, True, 12.0)]
    assert [p["u"] for p in pts] == pytest.approx([8.0, 16.0, 24.0])
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import IngestWatermark, PredictiveDirty
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(PredictiveDirty).where(PredictiveDirty.monitor_id == m["id"]))).first() is None
        wms = (await s.execute(select(IngestWatermark).where(IngestWatermark.tenant_id == ctx.id))).scalars().all()
    assert {w.tag_id for w in wms} == {run_id, cur_id} and all(w.late_min_ts is None for w in wms)
    assert max(w.max_ts for w in wms) == T + timedelta(hours=16)
    await pe.run_once(clk.at)                     # nothing left to redo
    pts2 = await _features(client, ctx, pos)
    assert [(p["i"], p["valid"], p["x"]) for p in pts2] == [(1, True, 10.0), (2, True, 11.0), (3, True, 12.0)]


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
    # Sprint 5b: the closed blocks' count and median are computed inside Influx too — no raw rows at all
    assert r["windows_added"] == 2 and raw_reads == []
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


async def test_late_readings_rewrite_a_valid_window_and_stay_in_their_tenant(client, clk):
    """Sprint 5b: late data that changes an already VALID window re-extracts it (before: valid windows were never
    rewritten, the late readings were ignored). The late mark of tenant A never dirties tenant B's monitors."""
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import PredictiveDirty
    from src.services import mqtt_subscriber
    from src.services.influx import influx
    from src.services.predictive import engine as pe
    ctx = await make_maint_tenant("LT6")
    ctx_b = await make_maint_tenant("LT6B")
    mach, pos, run_id, cur_id = await _motor(ctx)
    mach_b, pos_b, run_b, cur_b = await _motor(ctx_b)
    m = await _current_monitor(client, ctx, pos, cur_id)
    m_b = await _current_monitor(client, ctx_b, pos_b, cur_b)
    for c, mid, r, t in ((ctx, mach, run_id, cur_id), (ctx_b, mach_b, run_b, cur_b)):
        await influx.write(c.id, r, 1.0, machine_id=mid, ts=T)
        for h in range(0, 24):
            await influx.write(c.id, t, 10.0, machine_id=mid, unit="A", ts=T + timedelta(hours=h))
    clk.at = T + timedelta(hours=25)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    await api(client, "POST", f"/api/predictive/monitors/{m_b['id']}/process", ctx_b.h("maintenance_planner"))
    assert [(p["valid"], p["x"]) for p in await _features(client, ctx, pos)] == [(True, 10.0)] * 3
    # the gateway flushes a buffered reading of 4 h ago in window 1 (value 22 A held for 30 min)
    for v, at in ((22.0, T + timedelta(hours=3, minutes=30)), (10.0, T + timedelta(hours=4))):
        await mqtt_subscriber._handle_message(f"factory/{ctx.id}/sensors/{cur_id}", _payload(v, at, "A"),
                                              received_at=clk.at)
    await pe._flush_ingest_marks()
    async with AsyncSessionLocal() as s:
        await pe.transfer_late(s)
        await s.commit()
        dirty = (await s.execute(select(PredictiveDirty.monitor_id))).scalars().all()
    assert m["id"] in dirty and m_b["id"] not in dirty
    clk.at += timedelta(minutes=5)
    r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert r["late_windows"] == 1
    pts = await _features(client, ctx, pos)
    assert pts[0]["valid"] and pts[0]["x"] == pytest.approx((7.5 * 10.0 + 0.5 * 22.0) / 8.0)
    assert [(p["valid"], p["x"]) for p in pts[1:]] == [(True, 10.0), (True, 10.0)]
    # tenant B untouched
    assert [(p["valid"], p["x"]) for p in await _features(client, ctx_b, pos_b)] == [(True, 10.0)] * 3
    # deterministic: a full re-extraction from Influx gives the same series
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"),
                   json={"monitor_id": m["id"], "reextract_all": True})
    assert rc["monitors"][0]["windows_reextracted"] == 0 and rc["same"] is True


async def test_replayed_dead_letter_inside_a_stored_window_dirties_the_monitor(client, clk):
    """Review (Sprint 5b): the operator replay (`python -m src.services.ingest.dead_letter --replay`) runs the
    readings through the normal processor in its OWN process; their late marks lived only in that process's memory
    and were lost on exit, so no window was re-extracted. `replay_and_flush` writes them to ingest_watermarks before
    returning — the engine (another process) finds them, dirties the monitor and re-cuts the window."""
    from sqlalchemy import select, text
    from src.db import AsyncSessionLocal
    from src.models import PredictiveDirty
    from src.services.influx import influx
    from src.services.ingest import dead_letter, watermarks
    from src.services.predictive import engine as pe
    # real past times: the replay stamps the reading as received NOW (wall clock), so it must be old for real
    now = datetime.now(timezone.utc)
    t0 = (now - timedelta(days=3)).replace(hour=4, minute=0, second=0, microsecond=0)   # local midnight (Manaus)
    clk.at = t0
    ctx = await make_maint_tenant("LT7")
    mach, pos, run_id, cur_id = await _motor(ctx)
    m = await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "current", "tag_id": cur_id, "window_hours": 8, "warmup_windows": 1,
        "start_at": t0.isoformat(), "params": {"data_gap_s": 172800, "baseline_min_windows": 3,
                                               "baseline_min_span_h": 0}})
    await influx.write(ctx.id, run_id, 1.0, machine_id=mach, ts=t0)
    for h in range(0, 24):
        await influx.write(ctx.id, cur_id, 10.0, machine_id=mach, unit="A", ts=t0 + timedelta(hours=h))
    clk.at = t0 + timedelta(hours=25)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert [(p["valid"], p["x"]) for p in await _features(client, ctx, pos)] == [(True, 10.0)] * 3
    # Influx was down at the time: two readings of window 1 became dead letters
    topic = f"factory/{ctx.id}/sensors/{cur_id}"
    for v, at in ((22.0, t0 + timedelta(hours=3, minutes=30)), (10.0, t0 + timedelta(hours=4))):
        assert await dead_letter.record(topic, _payload(v, at, "A"), at, "influx", "influx down")
    assert watermarks.pending() == 0
    # the operator replays them (CLI entry point)
    assert await dead_letter.replay_and_flush(ctx.id) == 2
    assert watermarks.pending() == 0                       # nothing left only in this process's memory
    async with AsyncSessionLocal() as s:
        row = (await s.execute(text("SELECT late_min_ts, late_max_ts, late_count FROM ingest_watermarks "
                                    "WHERE tenant_id = :t AND tag_id = :g"), {"t": ctx.id, "g": cur_id})).one()
        assert row[0] == t0 + timedelta(hours=3, minutes=30) and row[1] == t0 + timedelta(hours=4) and row[2] == 2
        await pe.transfer_late(s)
        await s.commit()
        dirty = (await s.execute(select(PredictiveDirty.monitor_id))).scalars().all()
    assert m["id"] in dirty
    clk.at += timedelta(minutes=5)
    r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    assert r["late_windows"] == 1
    pts = await _features(client, ctx, pos)
    assert pts[0]["valid"] and pts[0]["x"] == pytest.approx((7.5 * 10.0 + 0.5 * 22.0) / 8.0)


async def test_recompute_in_background_returns_202_and_the_same_result_isolated_per_tenant(client, clk, monkeypatch):
    """Sprint 5b QA: reextract_all of a 70-block monitor took 94–352 s inside the request (nginx: 60 s → 504).
    background=true answers 202 with a job id; the job gives the same body as the synchronous call; the job is
    visible only to its tenant's admin; one predictive recompute per tenant at a time (409)."""
    import asyncio
    from src.services.influx import influx
    from src.services.predictive import engine as pe
    ctx = await make_maint_tenant("LT8")
    ctx_b = await make_maint_tenant("LT8B")
    mach, pos, run_id, cur_id = await _motor(ctx)
    m = await _current_monitor(client, ctx, pos, cur_id)
    await influx.write(ctx.id, run_id, 1.0, machine_id=mach, ts=T)
    for h in range(0, 24):
        await influx.write(ctx.id, cur_id, 10.0 + (h % 3), machine_id=mach, unit="A", ts=T + timedelta(hours=h))
    clk.at = T + timedelta(hours=25)
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", ctx.h("maintenance_planner"))
    body = {"monitor_id": m["id"], "reextract_all": True}
    sync = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json=body)
    assert sync["same"] is True

    async def poll(job_id, h):
        for _ in range(500):
            r = await client.get(f"/api/predictive/recompute/jobs/{job_id}", headers=h)
            assert r.status_code == 200, r.text
            if r.json()["status"] != "running":
                return r.json()
            await asyncio.sleep(0.02)
        raise AssertionError("job did not finish")

    r = await client.post("/api/predictive/recompute", headers=ctx.h("admin"), json={**body, "background": True})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "running" and job["monitor_id"] == m["id"] and job["reextract_all"] is True
    # another tenant's admin cannot see it; a non-admin of the same tenant cannot either
    assert (await client.get(f"/api/predictive/recompute/jobs/{job['job_id']}",
                             headers=ctx_b.h("admin"))).status_code == 404
    assert (await client.get(f"/api/predictive/recompute/jobs/{job['job_id']}",
                             headers=ctx.h("maintenance_planner"))).status_code == 403
    done = await poll(job["job_id"], ctx.h("admin"))
    assert done["status"] == "done" and done["error"] is None
    assert done["result"] == sync                                      # same body as the synchronous call
    # query-parameter form; a monitor of another tenant answers 404 before any job is created
    assert (await client.post("/api/predictive/recompute?background=true", headers=ctx_b.h("admin"),
                              json={"monitor_id": m["id"]})).status_code == 404
    # one at a time per tenant: a second request while a job runs → 409 with the running job id; tenant B is free
    gate = asyncio.Event()
    real = pe.recompute

    async def slow(*a, **k):
        await gate.wait()
        return await real(*a, **k)
    monkeypatch.setattr(pe, "recompute", slow)
    r = await client.post("/api/predictive/recompute?background=true", headers=ctx.h("admin"), json=body)
    assert r.status_code == 202
    running = r.json()["job_id"]
    r2 = await client.post("/api/predictive/recompute", headers=ctx.h("admin"), json=body)
    assert r2.status_code == 409 and r2.json()["detail"]["job_id"] == running
    assert (await client.post("/api/predictive/recompute", headers=ctx_b.h("admin"), json={})).status_code == 200
    gate.set()
    assert (await poll(running, ctx.h("admin")))["status"] == "done"
