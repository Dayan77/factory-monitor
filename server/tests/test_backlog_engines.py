"""Sprint 7 — no detector may stall on a backlog (docs/performance.md §2, kpis.md S7-16).

* predictive engine: after a long outage the windows to extract are many and each one is an Influx query; a run
  stops after PREDICTIVE_EXTRACT_BUDGET_S, commits what it has and the next run resumes from the cursor — the series
  ends up identical to an unbounded run (before: a run longer than predictive_monitor_timeout_s was rolled back and
  restarted every tick, forever);
* maintenance engine: the urgent stages (emergency calls, escalations) run and commit before the plan recompute, so a
  plan stage that fails or hangs never takes them down; tenants run with bounded concurrency, one slow tenant never
  delays the others.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant
from test_predictive_late_data import _motor

T = datetime(2027, 6, 7, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T)
    monkeypatch.setattr(clock, "now", c)
    return c


async def _monitor(client, ctx, pos, cur_id):
    return await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "current", "tag_id": cur_id, "window_hours": 1, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"data_gap_s": 7200, "baseline_min_windows": 3,
                                              "baseline_min_span_h": 0}})


async def _points(client, ctx, pos):
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", ctx.h("maintenance_planner"))
    return [(p["i"], p["valid"], p["x"], round(p["u"] or 0.0, 6)) for p in feats["series"][0]["points"]]


async def test_predictive_backlog_is_extracted_in_bounded_runs_equal_to_one_run(client, clk, monkeypatch):
    from src.services.influx import influx
    from src.services.predictive import engine as pe
    hours = 72
    series = []
    for label in ("BK1", "BK2"):
        ctx = await make_maint_tenant(label)
        mach, pos, run_id, cur_id = await _motor(ctx)
        for h in range(hours):
            at = T + timedelta(hours=h, minutes=10)
            await influx.write(ctx.id, run_id, 1.0, machine_id=mach, ts=at)
            await influx.write(ctx.id, cur_id, 10.0 + (h % 5), machine_id=mach, unit="A", ts=at)
        m = await _monitor(client, ctx, pos, cur_id)
        series.append((ctx, pos, m))
    clk.at = T + timedelta(hours=hours, minutes=10)       # the gateway buffer of 72 h has just been flushed

    # reference: one unbounded run
    (ctx1, pos1, m1), (ctx2, pos2, m2) = series
    r = await api(client, "POST", f"/api/predictive/monitors/{m1['id']}/process", ctx1.h("maintenance_planner"))
    assert r["windows_added"] == hours
    ref = await _points(client, ctx1, pos1)

    # bounded: every extraction is slow, the budget lets a few windows through per run
    real = pe._time_window

    async def slow(*a, **kw):
        await asyncio.sleep(0.02)
        return await real(*a, **kw)
    monkeypatch.setattr(pe, "_time_window", slow)
    monkeypatch.setattr(pe, "EXTRACT_BUDGET_S", 0.15)
    added, runs = [], 0
    while sum(added) < hours and runs < 60:
        r = await api(client, "POST", f"/api/predictive/monitors/{m2['id']}/process", ctx2.h("maintenance_planner"))
        added.append(r["windows_added"])
        runs += 1
    assert sum(added) == hours and len(added) > 3 and all(a > 0 for a in added)     # always progressing
    assert await _points(client, ctx2, pos2) == ref


async def test_maintenance_urgent_stages_survive_a_hanging_plan_stage(client, monkeypatch):
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    from src.services.maintenance import engine as me
    ctx = await make_maint_tenant("URG")
    mach = ctx.machines["Estação de Fixação"]
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        f = FailureRecord(tenant_id=ctx.id, machine_id=mach, failed_at=now - timedelta(minutes=30),
                          severity="critical", detection_method="production_interference")
        s.add(f)
        await s.commit()
        fid = f.id

    async def hangs(*a, **kw):                  # a plan recompute stuck on a huge usage backlog
        await asyncio.sleep(30)
    monkeypatch.setattr(me, "run_plan_engine", hangs)
    monkeypatch.setattr(me, "MAINTENANCE_TENANT_TIMEOUT_S", 1.0)
    out = await me.run_maintenance_tick(now=now, tenant_ids={ctx.id})
    assert out[ctx.id] == {"error": "timeout"}  # the tick of the tenant timed out in the plan stage …
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(FailureRecord.emergency_notified_at).where(FailureRecord.id == fid))
                ).scalar_one() is not None             # … but the emergency call had already gone out


async def test_maintenance_slow_tenant_does_not_delay_the_others(client, monkeypatch):
    from src.services.maintenance import engine as me
    slow_ctx = await make_maint_tenant("SLW")
    fast_ctx = await make_maint_tenant("FST")
    done_at: dict = {}
    real = me.tick_tenant
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def tick(db, tid, now=None, **kw):
        if tid == slow_ctx.id:
            await asyncio.sleep(1.0)
        res = await real(db, tid, now, **kw)
        done_at[tid] = loop.time() - t0
        return res
    monkeypatch.setattr(me, "tick_tenant", tick)
    await me.run_maintenance_tick(tenant_ids={slow_ctx.id, fast_ctx.id})
    assert done_at[fast_ctx.id] < done_at[slow_ctx.id] and done_at[fast_ctx.id] < 0.9
