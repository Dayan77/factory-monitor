"""Sprint 7 — InfluxDB retention and downsampling (services/influx_downsampling.py, docs/performance.md §5).

Runs against the real InfluxDB on its OWN buckets (never the shared dev bucket): raw readings over 8 h, the 1-minute
and hourly aggregates computed by the same Flux the Influx tasks run, and the chart query (`query_history`) answered
from the aggregates for long ranges must give the same means as the raw readings. Also: `ensure()` is idempotent
(buckets, raw retention 90 d, tasks), the tasks really run in Influx, late readings are backfilled, and tenants stay
isolated in the aggregates.
"""
from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc


@pytest.fixture
async def ds_bucket(monkeypatch):
    from src.config import settings
    from src.services import influx_downsampling as ds
    raw = f"fm_test_ds_{uuid.uuid4().hex[:8]}"
    api = ds._Api()
    await api.ensure_bucket(raw, 0)
    monkeypatch.setattr(settings, "influx_bucket", raw)
    monkeypatch.setattr(settings, "influx_downsampling", True)
    monkeypatch.setattr(settings, "influx_manage_retention", True)
    ds._coverage_cache.clear()
    ds._late.clear()
    try:
        yield raw
    finally:
        n = ds.names(raw)
        org = await api.org_id()
        for name in (n["raw"], n["1m"], n["1h"]):
            b = await api.bucket(name)
            if b:
                await api.c.delete(f"/api/v2/buckets/{b['id']}")
        r = await api.c.get("/api/v2/tasks", params={"orgID": org, "limit": 500})
        for t in r.json().get("tasks") or []:
            if t["name"] in (n["task_1m"], n["task_1h"]):
                await api.c.delete(f"/api/v2/tasks/{t['id']}")
        await api.close()
        ds._coverage_cache.clear()


async def _write(tenant, tag, pts, machine=None):
    from src.services.influx import influx
    await asyncio.gather(*[influx.write(tenant, tag, v, machine_id=machine, ts=t) for t, v in pts])


def _close(a, b):
    assert len(a) == len(b), (len(a), len(b))
    for x, y in zip(a, b):
        assert x["timestamp"] == y["timestamp"]
        assert math.isclose(x["value"], y["value"], rel_tol=1e-9, abs_tol=1e-9), (x, y)


async def test_long_range_chart_from_aggregates_equals_raw(ds_bucket, monkeypatch):
    from src.config import settings
    from src.services import influx_downsampling as ds
    from src.services.influx import influx
    tenant, tag, other_tenant = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    t0 = ds.floor(now - timedelta(hours=10), timedelta(hours=1))
    pts = [(t0 + timedelta(seconds=10 * k), 20.0 + (k % 37) * 0.5 + (k // 360)) for k in range(8 * 360)]
    await _write(tenant, tag, pts, machine=str(uuid.uuid4()))
    # another tenant with the SAME tag id and other values: must never leak into tenant's aggregates
    await _write(other_tenant, tag, [(t, 1000.0) for t, _ in pts[::7]])

    out = await ds.ensure(ds_bucket)
    assert out["task_1m"] == "created" and out["task_1h"] == "created"
    await ds.backfill(t0, t0 + timedelta(hours=8), ds_bucket)
    await ds.write_coverage(t0, ds_bucket)
    assert await ds.read_coverage(ds_bucket) == t0

    stop = t0 + timedelta(hours=8)
    for window in ("5m", "1h"):
        plan = ds.plan_history(t0, stop, ds.duration_s(window), t0, now=now)
        assert [p[0] for p in plan] == (["1m"] if window == "5m" else ["1h"])
        agg = await influx.query_history(tenant, tag, start=t0, stop=stop, window=window)
        monkeypatch.setattr(settings, "influx_downsampling", False)
        raw = await influx.query_history(tenant, tag, start=t0, stop=stop, window=window)
        monkeypatch.setattr(settings, "influx_downsampling", True)
        assert len(raw) == (96 if window == "5m" else 8)
        _close(agg, raw)
    # short ranges / sub-minute windows always read the raw readings
    assert [p[0] for p in ds.plan_history(t0, t0 + timedelta(hours=2), 300, t0, now=now)] == ["raw"]
    assert [p[0] for p in ds.plan_history(t0, stop, 30, t0, now=now)] == ["raw"]
    # a range that starts before the coverage: raw for the part before it
    plan = ds.plan_history(t0 - timedelta(hours=3), stop, 300, t0, now=now)
    assert [p[0] for p in plan] == ["raw", "1m"] and plan[0][2] == t0


async def test_ensure_is_idempotent_sets_retention_and_tasks_run(ds_bucket):
    from src.services import influx_downsampling as ds
    first = await ds.ensure(ds_bucket)
    again = await ds.ensure(ds_bucket)
    assert first["task_1m"] == "created" and again == {"task_1m": "unchanged", "task_1h": "unchanged",
                                                        "created_1m": False}
    api = ds._Api()
    try:
        n = ds.names(ds_bucket)
        rules = {name: (await api.bucket(name)).get("retentionRules") for name in (n["raw"], n["1m"], n["1h"])}
        assert rules[n["raw"]][0]["everySeconds"] == 90 * 86400
        assert rules[n["1m"]][0]["everySeconds"] == 730 * 86400
        assert all(r.get("everySeconds", 0) == 0 for r in rules[n["1h"]] or [])   # hourly: forever
        # the tasks are valid Flux and really run in Influx (manual run → success)
        org = await api.org_id()
        tasks = (await api.c.get("/api/v2/tasks", params={"orgID": org, "limit": 500})).json()["tasks"]
        mine = [t for t in tasks if t["name"] in (n["task_1m"], n["task_1h"])]
        assert len(mine) == 2 and all(t["status"] == "active" for t in mine)
        for t in mine:
            run = (await api.c.post(f"/api/v2/tasks/{t['id']}/runs", json={})).json()
            for _ in range(60):
                st = (await api.c.get(f"/api/v2/tasks/{t['id']}/runs/{run['id']}")).json()
                if st.get("status") in ("success", "failed", "canceled"):
                    break
                await asyncio.sleep(0.5)
            assert st.get("status") == "success", st
    finally:
        await api.close()


async def test_late_readings_are_backfilled_into_the_aggregates(ds_bucket):
    from src.services import influx_downsampling as ds
    from src.services.influx import influx
    tenant, tag = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    t0 = ds.floor(now - timedelta(hours=12), timedelta(hours=1))
    await ds.ensure(ds_bucket)
    await _write(tenant, tag, [(t0 + timedelta(minutes=k), 10.0) for k in range(120)])
    await ds.backfill(t0, t0 + timedelta(hours=2), ds_bucket)
    await ds.write_coverage(t0, ds_bucket)
    # a gateway flushes its buffer hours later: two readings inside the first hour
    late = [(t0 + timedelta(minutes=30, seconds=20), 70.0), (t0 + timedelta(minutes=30, seconds=40), 70.0)]
    await _write(tenant, tag, late)
    for t, _ in late:
        ds.note_late(tenant, t)
    assert await ds.flush_late(ds_bucket) == 1 and not ds._late
    rows = await influx._history_part("1h", tenant, tag, t0, t0 + timedelta(hours=2), "1h")
    assert [round(r["value"], 6) for r in rows] == [round((60 * 10.0 + 140.0) / 62, 6), 10.0]


async def test_recompute_apply_refused_when_usage_is_older_than_the_raw_retention(client, tenants, monkeypatch):
    """kpis.md S7-18: the raw readings expire after 90 days; replacing the accumulators by a replay would erase the
    usage of the older months. apply=true → 409 (reason beyond_raw_retention); the dry run still works; a tenant
    whose history is inside the retention is not affected."""
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.models import UsageBucket
    from usage_helpers import build_plant
    monkeypatch.setattr(settings, "influx_downsampling", True)
    p = await build_plant(client, tenants.a, "RET")
    old = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=120)
    async with AsyncSessionLocal() as s:
        s.add(UsageBucket(scope="machine", entity_id=p.machine["id"], measure="cycles", bucket_start=old,
                          tenant_id=tenants.a.id, value=10.0))
        await s.commit()
    r = await client.post("/api/usage/recompute?apply=true", headers=tenants.a.headers("admin"))
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "beyond_raw_retention", r.text
    r = await client.post("/api/usage/recompute?apply=true&background=true", headers=tenants.a.headers("admin"))
    assert r.status_code == 409
    r = await client.post("/api/usage/recompute?apply=false", headers=tenants.a.headers("admin"))
    assert r.status_code == 200
    # tenant B (no old history) may still apply — the guard is per tenant
    r = await client.post("/api/usage/recompute?apply=true", headers=tenants.b.headers("admin"))
    assert r.status_code == 200, r.text
