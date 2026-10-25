"""Sprint 7b — bugs found by the owner-facing screenshot run on the live virtual plant (kpis.md "Decisões Sprint 7"):

  * 4a / S7-22  GET /api/downtime/no-data: explicit intervals WITHOUT data per machine, so the state timeline paints
                them as "sem dados" and never as green "sem parada registrada" (+ tenant isolation, operator scope)
  * 4b / S7-21  the default reliability window ends NOW: a failure of today is in GET /api/maintenance/reliability
                without start/stop (it used to end today 00:00 local)
  * 4c / S7-23  an open failure older than FAILURE_STALE_OPEN_H is "falha aberta há muito tempo — verificar" on the
                map/tablet (badge + text), not a live failure; incipient records never stop the machine; the
                failures API flags `open_stale` (seed fix: tests/test_seed_demo_plant.py::test_seed_demo_maintenance)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from maint_helpers import make_maint_tenant
from test_oee_api import _tenant
from usage_helpers import api, build_plant

UTC = timezone.utc


async def _span(tenant_id, machine_id, a, b):
    from src.db import AsyncSessionLocal
    from src.models import MachineDataSpan
    async with AsyncSessionLocal() as s:
        s.add(MachineDataSpan(tenant_id=tenant_id, machine_id=machine_id, start=a, end=b))
        await s.commit()


# ── 4a: no-data intervals ───────────────────────────────────────────────────

async def test_no_data_intervals_are_explicit(client):
    """Window 8 h ago → 2 h ago; data only 6 h ago → 5 h ago and 4 h ago → 3 h ago: the API returns the three gaps
    (8→6, 5→4, 3→2). A machine of the same line without any data is one gap covering the window. The part of the
    window after `now` is not a gap (the future is not missing data)."""
    t = await _tenant("NDA")
    p = await build_plant(client, t, "NDA")
    h = t.headers("admin")
    other = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": p.line["id"], "name": "Sem sinal"})
    now = datetime.now(UTC).replace(microsecond=0)
    w0, w1 = now - timedelta(hours=8), now - timedelta(hours=2)
    mid = p.machine["id"]
    await _span(t.id, mid, now - timedelta(hours=6), now - timedelta(hours=5))
    await _span(t.id, mid, now - timedelta(hours=4), now - timedelta(hours=3))
    rows = await api(client, "GET", "/api/downtime/no-data", h,
                     params={"line_id": p.line["id"], "start": w0.isoformat(), "stop": w1.isoformat()})
    by = {r["machine_id"]: [(datetime.fromisoformat(i["start"]), datetime.fromisoformat(i["end"]))
                            for i in r["intervals"]] for r in rows}
    assert set(by) == {mid, other["id"]}
    assert by[mid] == [(w0, now - timedelta(hours=6)), (now - timedelta(hours=5), now - timedelta(hours=4)),
                       (now - timedelta(hours=3), w1)]
    assert by[other["id"]] == [(w0, w1)]
    # window reaching into the future: the gap stops at now (± request latency), nothing after it
    fut = await api(client, "GET", "/api/downtime/no-data", h, params={
        "machine_id": other["id"], "start": w0.isoformat(), "stop": (now + timedelta(hours=4)).isoformat()})
    (iv,) = fut[0]["intervals"]
    end = datetime.fromisoformat(iv["end"])
    assert datetime.fromisoformat(iv["start"]) == w0 and now <= end <= datetime.now(UTC)
    # validation
    await api(client, "GET", "/api/downtime/no-data", h, 422, params={"start": w1.isoformat(), "stop": w0.isoformat()})
    await api(client, "GET", "/api/downtime/no-data", h, 422, params={
        "start": (now - timedelta(days=401)).isoformat(), "stop": now.isoformat()})


async def test_no_data_tenant_isolation(client):
    t = await _tenant("NDB")
    p = await build_plant(client, t, "NDB")
    now = datetime.now(UTC).replace(microsecond=0)
    await _span(t.id, p.machine["id"], now - timedelta(hours=2), now - timedelta(hours=1))
    b = await _tenant("NDB2")
    hb = b.headers("admin")
    q = {"start": (now - timedelta(hours=3)).isoformat(), "stop": now.isoformat()}
    assert await api(client, "GET", "/api/downtime/no-data", hb, params={**q, "line_id": p.line["id"]}) == []
    r = await client.get("/api/downtime/no-data", headers=hb, params={**q, "machine_id": p.machine["id"]})
    assert r.status_code in (200, 404) and (r.status_code == 404 or r.json() == [])
    assert await api(client, "GET", "/api/downtime/no-data", hb, params=q) == []        # B has no machines


# ── 4b: reliability default window ends now ────────────────────────────────

async def test_reliability_default_window_includes_todays_failure(client):
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    ctx = await make_maint_tenant("REL7B")
    mid = ctx.machines["Estação de Fixação"]
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        s.add(FailureRecord(tenant_id=ctx.id, machine_id=mid, failed_at=now - timedelta(minutes=30),
                            restored_at=now - timedelta(minutes=10), severity="critical", status="draft",
                            detection_method="production_interference"))
        await s.commit()
    rel = await api(client, "GET", "/api/maintenance/reliability", ctx.h("maintenance_planner"),
                    params={"group_by": "machine"})
    end = datetime.fromisoformat(rel["period_end"].replace("Z", "+00:00"))
    assert abs((end - now).total_seconds()) < 120                    # ends now, not at today 00:00 local
    row = next(r for r in rel["rows"] if r.get("machine_id") == mid)
    assert row["n"] == 1 and row["restored"] == 1
    # start: local midnight 12 months ago (unchanged)
    start = datetime.fromisoformat(rel["period_start"].replace("Z", "+00:00"))
    assert 365 <= (now - start).days <= 367
    # tenant isolation: another tenant's default window never sees it
    other = await make_maint_tenant("REL7C")
    relb = await api(client, "GET", "/api/maintenance/reliability", other.h("maintenance_planner"),
                     params={"group_by": "machine"})
    assert all(r.get("machine_id") != mid for r in relb["rows"]) and sum(r["n"] for r in relb["rows"]) == 0


# ── 4c: stale open failure ─────────────────────────────────────────────────

async def _running(machine_ids):
    from src.db import AsyncSessionLocal
    from src.models import Machine, MachineStatus
    async with AsyncSessionLocal() as s:
        await s.execute(update(Machine).where(Machine.id.in_(machine_ids)).values(
            status=MachineStatus.running, last_seen_at=datetime.now(UTC)))
        await s.commit()


async def _failure(tenant_id, machine_id, age, severity="critical"):
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        f = FailureRecord(tenant_id=tenant_id, machine_id=machine_id, failed_at=datetime.now(UTC) - age,
                          severity=severity, status="draft", detection_method="production_interference")
        s.add(f)
        await s.commit()
        return f.id


async def test_stale_open_failure_is_flagged_not_live(client, monkeypatch):
    """Seed bug: a failure record with restored_at empty from 40 days ago showed "falha · há 40 d" on the map,
    tablet and Raio-X forever. Now: open > FAILURE_STALE_OPEN_H (72 h) → the machine keeps its real state
    (running), with the badge `stale_failure` and the text "falha aberta há muito tempo — verificar"; a fresh open
    failure is still a live failure; an open incipient record never stops the machine; the threshold is
    configurable."""
    from src.config import settings
    t = await _tenant("STF")
    p = await build_plant(client, t, "STF")
    h = t.headers("admin")
    fresh_m = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": p.line["id"], "name": "Fresca"})
    inc_m = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": p.line["id"], "name": "Incipiente"})
    old_m = p.machine["id"]
    await _running([old_m, fresh_m["id"], inc_m["id"]])
    old_id = await _failure(t.id, old_m, timedelta(days=40))
    await _failure(t.id, fresh_m["id"], timedelta(hours=1))
    await _failure(t.id, inc_m["id"], timedelta(hours=1), severity="incipient")

    def markers(view):
        return {m["machine_id"]: m for m in view["machines"]}

    view = markers(await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h, params={"kpis": "false"}))
    old = view[old_m]
    assert old["state"] != "failure" and old["state"] in ("running", "off_shift")
    assert "falha aberta há muito tempo — verificar" in old["text"]
    assert old["detail"]["stale_failure_id"] == old_id
    assert any(b["kind"] == "stale_failure" and b["failure_id"] == old_id for b in old["badges"])
    assert view[fresh_m["id"]]["state"] == "failure" and not view[fresh_m["id"]]["badges"]
    assert view[inc_m["id"]]["state"] != "failure"

    # the failures API says the same
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": old_m})
    assert [r["open_stale"] for r in rows if r["id"] == old_id] == [True]
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": fresh_m["id"]})
    assert [r["open_stale"] for r in rows] == [False]

    # configurable: with a 1 000 h threshold the 40-day failure (960 h) is a live failure again
    monkeypatch.setattr(settings, "failure_stale_open_h", 1000.0)
    view = markers(await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h, params={"kpis": "false"}))
    assert view[old_m]["state"] == "failure" and "stale_failure_id" not in view[old_m]["detail"]

    # a machine that reports a fault NOW is a failure even with only a stale record (flagged for checking)
    monkeypatch.setattr(settings, "failure_stale_open_h", 72.0)
    from src.db import AsyncSessionLocal
    from src.models import Machine, MachineStatus
    async with AsyncSessionLocal() as s:
        await s.execute(update(Machine).where(Machine.id == old_m).values(status=MachineStatus.error))
        await s.commit()
    view = markers(await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h, params={"kpis": "false"}))
    assert view[old_m]["state"] == "failure"
    # ... and the old record is then NOT contradicted by the data (machine not running, no closed linked stop):
    # it is a live failure, no "verificar" (review S7-23: age alone never downgrades a failure)
    assert "verificar" not in view[old_m]["text"] and not view[old_m]["badges"]
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": old_m})
    assert [r["open_stale"] for r in rows if r["id"] == old_id] == [False]

    # tenant isolation: tenant B never reaches A's plant view
    b = await _tenant("STF2")
    await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", b.headers("admin"), 404,
              params={"kpis": "false"})


async def _downtime(tenant_id, machine_id, start, end=None):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    async with AsyncSessionLocal() as s:
        ev = DowntimeEvent(tenant_id=tenant_id, machine_id=machine_id, start=start, end=end, source="auto_fault",
                           reason_code="EQP-01", planned=False,
                           duration_s=(end - start).total_seconds() if end else None)
        s.add(ev)
        await s.commit()
        return ev.id


async def _link(failure_id, event_id):
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        await s.execute(update(FailureRecord).where(FailureRecord.id == failure_id).values(downtime_event_id=event_id))
        await s.commit()


async def test_long_real_failure_with_open_linked_stop_stays_failure(client):
    """Review S7-23: press breaks Friday 14:00, its FailureRecord is linked to an open EQP stop and the machine stays
    down (waiting for a spare part). On Monday 14:01 (> 72 h) it is still "falha" on the map and `open_stale` is
    false — the open stop confirms it. Once that linked stop is closed (the machine ran again) without a restore,
    the record becomes stale ("verificar") on the map AND in the API (one predicate)."""
    t = await _tenant("STL")
    p = await build_plant(client, t, "STL")
    h = t.headers("admin")
    mid = p.machine["id"]
    from src.db import AsyncSessionLocal
    from src.models import Machine, MachineStatus
    async with AsyncSessionLocal() as s:
        await s.execute(update(Machine).where(Machine.id == mid).values(
            status=MachineStatus.stopped, last_seen_at=datetime.now(UTC)))
        await s.commit()
    failed = timedelta(hours=72, minutes=1)
    fid = await _failure(t.id, mid, failed)
    ev = await _downtime(t.id, mid, datetime.now(UTC) - failed)
    await _link(fid, ev)

    def marker(view):
        return next(m for m in view["machines"] if m["machine_id"] == mid)

    m = marker(await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h, params={"kpis": "false"}))
    assert m["state"] == "failure" and "verificar" not in m["text"] and not m["badges"]
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": mid})
    assert [(r["open"], r["open_stale"]) for r in rows] == [(True, False)]
    one = await api(client, "GET", f"/api/failures/{fid}", h)
    assert one["open_stale"] is False

    # the linked stop ended (the machine ran again) but nobody recorded the restore → stale, everywhere
    async with AsyncSessionLocal() as s:
        from src.models import DowntimeEvent
        await s.execute(update(DowntimeEvent).where(DowntimeEvent.id == ev).values(
            end=datetime.now(UTC) - timedelta(hours=24), duration_s=48 * 3600))
        await s.commit()
    m = marker(await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h, params={"kpis": "false"}))
    assert m["state"] != "failure" and "verificar" in m["text"]
    assert any(b["kind"] == "stale_failure" and b["failure_id"] == fid for b in m["badges"])
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": mid})
    assert [r["open_stale"] for r in rows] == [True]

    # tenant isolation: tenant B never reads A's failure
    b = await _tenant("STL2")
    await api(client, "GET", f"/api/failures/{fid}", b.headers("admin"), 404)


async def test_old_incipient_record_is_never_open_stale(client):
    """Review S7-23: an `incipient` record (condition monitoring) left unrestored for 5 days: no badge on the map
    and `open_stale` false in /api/failures/ — the same predicate as the map (state.py excludes incipient)."""
    t = await _tenant("STI")
    p = await build_plant(client, t, "STI")
    h = t.headers("admin")
    mid = p.machine["id"]
    await _running([mid])
    fid = await _failure(t.id, mid, timedelta(days=5), severity="incipient")
    m = next(x for x in (await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/auto", h,
                                   params={"kpis": "false"}))["machines"] if x["machine_id"] == mid)
    assert m["state"] != "failure" and not any(b["kind"] == "stale_failure" for b in m["badges"])
    rows = await api(client, "GET", "/api/failures/", h, params={"machine_id": mid})
    assert [(r["id"], r["open_stale"]) for r in rows] == [(fid, False)]
