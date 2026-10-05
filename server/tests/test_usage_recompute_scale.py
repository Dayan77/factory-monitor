"""Recompute at scale (streamed, windowed), concurrent with live processing, as a background job;
and the topology cache of the MQTT hot path."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from conftest import _make_tenant
from usage_helpers import add_component, add_position, add_tag, api, build_plant, publish, totals_of


def base_ts(hours_ago: int = 3) -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=hours_ago)


async def _setup(client, label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, f"Cilindro {label}")
    T = base_ts(4)
    comp = await add_component(client, t, p, "c", pos, installed_at=T - timedelta(days=1))
    cnt = await add_tag(client, t, p, f"{label}.ciclos", "cycle_counter", pos, counter_max=65535, max_rate_per_s=50)
    run = await add_tag(client, t, p, f"{label}.run", "run_state", data_type="bool")
    return t, p, pos, comp, cnt, run, T


async def test_recompute_streams_small_windows_equal_to_incremental(client, db, monkeypatch):
    """History read window by window (here 10 min) gives the same result as the live pipeline,
    including run intervals and counters that cross window boundaries."""
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.services.usage import recompute as rc
    t, p, pos, comp, cnt, run, T = await _setup(client, "W")
    seq = [(run, 0, 1), (cnt, 0, 100)]
    for i in range(1, 25):                       # 2 h of data, one reading every 5 min
        seq += [(cnt, i * 300, 100 + 7 * i), (run, i * 300, 0 if i % 6 == 0 else 1)]
    for tag, sec, v in seq:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))

    queries = []
    orig = rc.tag_readings

    async def counting(*a, **kw):
        rows = await orig(*a, **kw)
        queries.append(len(rows))
        return rows

    monkeypatch.setattr(rc, "tag_readings", counting)
    monkeypatch.setattr(settings, "usage_recompute_window_h", 10 / 60)
    async with AsyncSessionLocal() as s:
        report = await rc.recompute_usage(s, t.id)
    assert report.readings == len(seq)
    assert report.differences == []
    assert len(queries) >= 12 and max(queries) <= 6       # many small windows, never the whole history
    assert report.totals[("position", pos["id"], "cycles")] == 7 * 24


async def test_recompute_apply_keeps_readings_processed_while_it_runs(client, db, monkeypatch):
    """Live readings processed during the (unlocked) replay and readings arriving while the
    accumulators are being replaced are neither lost nor double counted."""
    from src.db import AsyncSessionLocal
    from src.services.usage import recompute as rc
    t, p, pos, comp, cnt, run, T = await _setup(client, "C")
    for sec, v in [(0, 0), (60, 10), (120, 20)]:
        await publish(t.id, cnt["id"], v, T + timedelta(seconds=sec))

    orig_pass = rc.replay_pass
    calls = {"n": 0}

    async def pass_with_live_traffic(r, tenant_id, tag_ids, stop):
        n = await orig_pass(r, tenant_id, tag_ids, stop)
        calls["n"] += 1
        if calls["n"] == 1:
            # processed live (committed into the old totals) after the history was read
            await publish(t.id, cnt["id"], 30, T + timedelta(seconds=180))
        return n

    late = {}
    orig_lock = rc.lock_tenant_usage_exclusive

    async def lock_then_live(db_, tenant_id):
        await orig_lock(db_, tenant_id)
        # arrives while the tenant is locked: must wait, then count from the replayed state
        late["task"] = asyncio.create_task(publish(t.id, cnt["id"], 45, T + timedelta(seconds=240)))
        await asyncio.sleep(0.5)
        assert not late["task"].done(), "live usage processing must wait for the recompute"

    monkeypatch.setattr(rc, "replay_pass", pass_with_live_traffic)
    monkeypatch.setattr(rc, "lock_tenant_usage_exclusive", lock_then_live)
    monkeypatch.setattr(rc, "CATCHUP_TARGET_S", -1.0)      # force an unlocked catch-up pass too
    async with AsyncSessionLocal() as s:
        report = await rc.recompute_usage(s, t.id, apply=True)
    await asyncio.wait_for(late["task"], 30)
    assert report.applied
    assert report.totals[("position", pos["id"], "cycles")] >= 30
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 45
    assert (await totals_of(db, "component", comp["id"]))["cycles"] == 45
    monkeypatch.setattr(rc, "replay_pass", orig_pass)
    monkeypatch.setattr(rc, "lock_tenant_usage_exclusive", orig_lock)
    async with AsyncSessionLocal() as s:
        again = await rc.recompute_usage(s, t.id)
    assert again.differences == []


async def test_recompute_background_job_and_one_per_tenant(client, db):
    from src.services.usage import recompute as rc
    t, p, pos, comp, cnt, run, T = await _setup(client, "J")
    for sec, v in [(0, 0), (60, 5)]:
        await publish(t.id, cnt["id"], v, T + timedelta(seconds=sec))
    h = t.headers("admin")
    with rc.tenant_slot(t.id):                               # another recompute is running
        r = await client.post("/api/usage/recompute", headers=h, params={"background": "true"})
        assert r.status_code == 409
        assert (await client.post("/api/usage/recompute", headers=h)).status_code == 409
    r = await client.post("/api/usage/recompute", headers=h, params={"background": "true", "apply": "true"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] in ("running", "done") and job["apply"] is True
    for _ in range(100):
        job = await api(client, "GET", f"/api/usage/recompute/jobs/{job['job_id']}", h)
        if job["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "done", job
    assert job["result"]["applied"] is True and job["result"]["readings"] == 2
    assert job["result"]["differences"] == []
    # tenant-scoped and admin-only
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        other = await _make_tenant(s, "J2")
        await s.commit()
    assert (await client.get(f"/api/usage/recompute/jobs/{job['job_id']}",
                             headers=other.headers("admin"))).status_code == 404
    assert (await client.get(f"/api/usage/recompute/jobs/{job['job_id']}",
                             headers=t.headers("operator"))).status_code == 403


# ── topology cache (MQTT hot path) ──────────────────────────────────────────

async def test_cache_sees_tag_relinked_right_after_commit(client, db):
    """Readings populate the cache; re-linking the tag to a position takes effect immediately."""
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, "K")
        await s.commit()
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro K")
    T = base_ts(2)
    tag = await add_tag(client, t, p, "K.ciclos", "cycle_counter", max_rate_per_s=100)
    for sec, v in [(0, 0), (10, 10)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    assert (await totals_of(db, "machine", p.machine["id"])).get("cycles") == 10
    await api(client, "PATCH", f"/api/tags/{tag['id']}", t.headers("admin"), json={"position_id": pos["id"]})
    await publish(t.id, tag["id"], 25, T + timedelta(seconds=20))
    assert (await totals_of(db, "position", pos["id"])).get("cycles") == 15


async def test_cache_does_not_store_a_load_that_raced_an_invalidation(monkeypatch):
    from src.services.usage import cache as cache_mod
    c = cache_mod.TopologyCache(ttl_s=60)
    calls = {"n": 0}

    async def slow_load(db, tenant_id, tag_id):
        calls["n"] += 1
        c.invalidate()                      # a commit lands while the lookup is in flight
        return object()

    monkeypatch.setattr(cache_mod, "load_tag_info", slow_load)
    await c.tag_info(None, "t", "x")
    await c.tag_info(None, "t", "x")
    assert calls["n"] == 2                  # nothing stale was cached

    async def none_load(db, tenant_id, tag_id):
        calls["n"] += 1
        return None

    monkeypatch.setattr(cache_mod, "load_tag_info", none_load)
    await c.tag_info(None, "t", "y")
    await c.tag_info(None, "t", "y")
    assert calls["n"] == 4                  # unknown tags are never cached (no negative caching)


@pytest.mark.parametrize("ttl", [0])
async def test_cache_can_be_disabled(monkeypatch, ttl):
    from src.services.usage import cache as cache_mod
    c = cache_mod.TopologyCache(ttl_s=ttl)
    calls = {"n": 0}

    async def load(db, tenant_id, tag_id):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(cache_mod, "load_tag_info", load)
    await c.tag_info(None, "t", "x")
    await c.tag_info(None, "t", "x")
    assert calls["n"] == 2
