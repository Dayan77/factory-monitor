"""Sprint 5 — S5-1 (kpis.md "Decisões Sprint 5"): an equipment fault that starts during a planned break or
off-shift (Sprint 4 QA finding: nothing was created until the break ended).

Binding decision:
  * FailureRecord (failed_at = fault start) and the emergency call are created immediately, whatever the calendar;
  * OEE accounting is unchanged: the break stays PDOT, off-shift time stays outside POT;
  * when production time resumes with the fault still active, the DowntimeEvent covers only the productive part and
    is linked to the same FailureRecord (one failure per stop);
  * MTTR uses clock time from failed_at to restoration.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from oee_helpers import add_shift
from test_oee_api import DAY, L, _plant_with_tags, _tenant, detect_with, set_checkpoint, signal
from usage_helpers import api


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def _failures(tenant_id, machine_id):
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(FailureRecord).where(
            FailureRecord.tenant_id == tenant_id, FailureRecord.machine_id == machine_id)
            .order_by(FailureRecord.failed_at))).scalars())


async def _emergency(tenant_id, now):
    from src.db import AsyncSessionLocal
    from src.services.maintenance import engine
    async with AsyncSessionLocal() as s:
        n = await engine.emergency_calls(s, tenant_id, now)
        await s.commit()
    return n


async def test_fault_during_planned_break_creates_failure_and_call_immediately(client):
    t = await _tenant("BRK")
    p, run, fault = await _plant_with_tags(client, t, "BRK")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "10:15", "reason_code": "PLN-01"}])
    mid, op = p.machine["id"], t.headers("operator")
    # the operator stops for the break at 10:00; at 10:05 the machine faults (code 7) and stays faulted until 10:40
    pts = [(L(DAY, "09:00"), 1, 0), (L(DAY, "10:00"), 0, 0), (L(DAY, "10:05"), 0, 7), (L(DAY, "10:40"), 1, 0)]
    rows = signal(run["id"], fault["id"], pts, L(DAY, "11:30"))
    await set_checkpoint(mid, t.id, L(DAY, "09:00") - timedelta(seconds=1))

    # 10:12, still inside the break: no event (no production time lost) but the failure exists already
    await detect_with(mid, t.id, rows, L(DAY, "10:12"))
    assert await api(client, "GET", "/api/downtime/", op, params={"machine_id": mid}) == []
    frs = await _failures(t.id, mid)
    assert len(frs) == 1
    fr = frs[0]
    assert fr.failed_at == L(DAY, "10:05") and fr.restored_at is None and fr.downtime_event_id is None
    assert fr.detection_method == "production_interference" and fr.status == "draft"
    # it is an open failure for the maintenance screens
    lst = await api(client, "GET", "/api/failures/", t.headers("admin"), params={"machine_id": mid, "open": "true"})
    assert [f["id"] for f in lst] == [fr.id] and lst[0]["open"] is True

    # emergency call goes out right away (engine tick at 10:12), with the emergency WO
    assert await _emergency(t.id, L(DAY, "10:12")) == 1
    frs = await _failures(t.id, mid)
    assert frs[0].emergency_notified_at is not None and frs[0].work_order_id
    wo_id = frs[0].work_order_id
    assert await _emergency(t.id, L(DAY, "10:13")) == 0                       # once

    # 10:20: the break is over and the fault is still active → event from 10:15 (productive part only),
    # linked to the SAME failure; the emergency WO now points at the stop too
    await detect_with(mid, t.id, rows, L(DAY, "10:20"))
    evs = await api(client, "GET", "/api/downtime/", op, params={"machine_id": mid})
    assert len(evs) == 1 and evs[0]["end"] is None
    assert _dt(evs[0]["start"]) == L(DAY, "10:15")
    assert evs[0]["failure_id"] == fr.id and evs[0]["suggested_reason_code"] == "EQP-01"
    wo = await api(client, "GET", f"/api/work-orders/{wo_id}", t.headers("admin"))
    assert wo["downtime_event_id"] == evs[0]["id"]

    # 11:30: restored at 10:40 → event 10:15–10:40 (1500 s); one failure, MTTR = clock time 10:05 → 10:40
    await detect_with(mid, t.id, rows, L(DAY, "11:30"))
    evs = await api(client, "GET", "/api/downtime/", op, params={"machine_id": mid})
    assert len(evs) == 1 and evs[0]["duration_s"] == 1500 and _dt(evs[0]["fault_since"]) == L(DAY, "10:05")
    frs = await _failures(t.id, mid)
    assert len(frs) == 1 and frs[0].restored_at == L(DAY, "10:40") and frs[0].downtime_event_id == evs[0]["id"]
    rel = await api(client, "GET", "/api/reliability", op, params={
        "scope": "machine", "id": mid, "start": L(DAY, "06:00").isoformat(), "stop": L(DAY, "14:00").isoformat()})
    assert rel["failures"] == 1 and rel["mttr_s"] == 35 * 60

    # OEE unchanged: the break (900 s) stays PDOT, the fault after it is ADOT (1500 s)
    o = await api(client, "GET", "/api/oee", op, params={"scope": "machine", "id": mid,
                                                         "start": L(DAY, "09:00").isoformat(),
                                                         "stop": L(DAY, "11:30").isoformat()})
    assert o["pdot_s"] == 900 and o["adot_s"] == 1500

    # re-running is idempotent (no second failure / event)
    await detect_with(mid, t.id, rows, L(DAY, "11:40"))
    assert len(await _failures(t.id, mid)) == 1
    assert len(await api(client, "GET", "/api/downtime/", op, params={"machine_id": mid})) == 1


async def test_fault_cleared_inside_break_and_off_shift(client):
    t = await _tenant("OFS")
    p, run, fault = await _plant_with_tags(client, t, "OFS")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "10:30", "reason_code": "PLN-01"}])
    mid, op = p.machine["id"], t.headers("operator")
    pts = [(L(DAY, "05:00"), 0, 0),
           (L(DAY, "05:30"), 0, 4),            # fault off-shift, still active at shift start
           (L(DAY, "06:20"), 1, 0),
           (L(DAY, "10:00"), 0, 0), (L(DAY, "10:05"), 0, 3), (L(DAY, "10:20"), 0, 0),   # fault inside the break
           (L(DAY, "10:30"), 1, 0),
           (L(DAY, "14:00"), 0, 0), (L(DAY, "14:30"), 0, 5), (L(DAY, "15:00"), 0, 0)]  # fault after the shift
    rows = signal(run["id"], fault["id"], pts, L(DAY, "16:00"))
    await set_checkpoint(mid, t.id, L(DAY, "05:00") - timedelta(seconds=1))
    await detect_with(mid, t.id, rows, L(DAY, "16:00"))

    frs = await _failures(t.id, mid)
    assert [(f.failed_at, f.restored_at) for f in frs] == [
        (L(DAY, "05:30"), L(DAY, "06:20")),
        (L(DAY, "10:05"), L(DAY, "10:20")),     # cleared inside the break: restored when it clears (S5-1)
        (L(DAY, "14:30"), L(DAY, "15:00")),     # cleared off-shift: same, even though the machine stays stopped
    ]
    evs = sorted(await api(client, "GET", "/api/downtime/", op, params={"machine_id": mid}), key=lambda e: e["start"])
    # only the in-shift productive part of the first stop is an event; the break and off-shift faults have none
    assert len(evs) == 1 and evs[0]["duration_s"] == 1200 and evs[0]["failure_id"] == frs[0].id
    assert frs[1].downtime_event_id is None and frs[2].downtime_event_id is None
    o = await api(client, "GET", "/api/oee", op, params={"scope": "machine", "id": mid,
                                                         "start": L(DAY, "06:00").isoformat(),
                                                         "stop": L(DAY, "14:00").isoformat()})
    assert o["pot_s"] == 8 * 3600 and o["pdot_s"] == 1800 and o["adot_s"] == 1200
    # restored failures without an event are not "open" and get no late emergency call
    assert await _emergency(t.id, L(DAY, "16:00")) == 0


async def test_stale_breaktime_failure_removed_when_late_data_erases_the_fault(client):
    """A draft created from a fault reading inside the break disappears when the re-detection (rewind) no longer
    sees the fault — unless someone already acted on it."""
    from src.db import AsyncSessionLocal
    from src.services.oee.detector import _drop_stale_failures
    from src.services.oee.engine import machine_infos
    t = await _tenant("STL")
    p, run, fault = await _plant_with_tags(client, t, "STL")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "10:30", "reason_code": "PLN-01"}])
    mid = p.machine["id"]
    rows = signal(run["id"], fault["id"], [(L(DAY, "09:00"), 1, 0), (L(DAY, "10:00"), 0, 0),
                                           (L(DAY, "10:05"), 0, 3)], L(DAY, "10:20"))
    await set_checkpoint(mid, t.id, L(DAY, "09:00") - timedelta(seconds=1))
    await detect_with(mid, t.id, rows, L(DAY, "10:20"))
    assert len(await _failures(t.id, mid)) == 1
    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, t.id, [mid]))[mid]
        await _drop_stale_failures(s, info, L(DAY, "09:00"), set())
        await s.commit()
    assert await _failures(t.id, mid) == []


async def test_break_short_fault_is_only_a_short_fault(client):
    """kpis.md S5-1b (case `break_short_fault`): the C22 threshold applies to the fault's own length when it starts
    and clears outside production time. 20 s inside the break → short_faults = 1, no failure, no emergency call;
    120 s (threshold inclusive) → one failure restored when it clears, MTTR 2 min. A fault still active waits for
    the threshold before the draft (and the call) exist."""
    t = await _tenant("BSF")
    p, run, fault = await _plant_with_tags(client, t, "BSF")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "10:30", "reason_code": "PLN-01"}])
    mid, op = p.machine["id"], t.headers("operator")
    pts = [(L(DAY, "09:00"), 1, 0), (L(DAY, "10:00"), 0, 0), (L(DAY, "10:05"), 0, 7), (L(DAY, "10:05:20"), 0, 0),
           (L(DAY, "10:30"), 1, 0)]
    rows = signal(run["id"], fault["id"], pts, L(DAY, "11:00"), hb=5)
    await set_checkpoint(mid, t.id, L(DAY, "09:00") - timedelta(seconds=1))
    # 10:05:10 — the fault is active but below the threshold: nothing yet
    await detect_with(mid, t.id, [r for r in rows if r[1] <= L(DAY, "10:05:10")], L(DAY, "10:05:10"))
    assert await _failures(t.id, mid) == []
    await detect_with(mid, t.id, rows, L(DAY, "11:00"))
    assert await _failures(t.id, mid) == []
    assert await _emergency(t.id, L(DAY, "11:00")) == 0
    rel = await api(client, "GET", "/api/reliability", op, params={
        "scope": "machine", "id": mid, "start": L(DAY, "06:00").isoformat(), "stop": L(DAY, "14:00").isoformat()})
    assert rel["failures"] == 0 and rel["short_faults"] == 1
    await detect_with(mid, t.id, rows, L(DAY, "11:10"))                      # idempotent: counted once
    rel = await api(client, "GET", "/api/reliability", op, params={
        "scope": "machine", "id": mid, "start": L(DAY, "06:00").isoformat(), "stop": L(DAY, "14:00").isoformat()})
    assert rel["short_faults"] == 1
    o = await api(client, "GET", "/api/oee", op, params={"scope": "machine", "id": mid,
                                                         "start": L(DAY, "06:00").isoformat(),
                                                         "stop": L(DAY, "14:00").isoformat()})
    assert o["pdot_s"] == 1800 and o["adot_s"] == 0


async def test_break_fault_at_the_threshold_is_a_failure(client):
    t = await _tenant("BSL")
    p, run, fault = await _plant_with_tags(client, t, "BSL")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "10:30", "reason_code": "PLN-01"}])
    mid, op = p.machine["id"], t.headers("operator")
    pts = [(L(DAY, "09:00"), 1, 0), (L(DAY, "10:00"), 0, 0), (L(DAY, "10:05"), 0, 7), (L(DAY, "10:07"), 0, 0),
           (L(DAY, "10:30"), 1, 0)]
    rows = signal(run["id"], fault["id"], pts, L(DAY, "11:00"), hb=5)
    await set_checkpoint(mid, t.id, L(DAY, "09:00") - timedelta(seconds=1))
    # still active at 10:06:59 (119 s): no draft yet; at 10:07 it completes the threshold
    await detect_with(mid, t.id, [r for r in rows if r[1] <= L(DAY, "10:06:55")], L(DAY, "10:06:59"))
    assert await _failures(t.id, mid) == []
    await detect_with(mid, t.id, rows, L(DAY, "11:00"))
    frs = await _failures(t.id, mid)
    assert [(f.failed_at, f.restored_at) for f in frs] == [(L(DAY, "10:05"), L(DAY, "10:07"))]
    rel = await api(client, "GET", "/api/reliability", op, params={
        "scope": "machine", "id": mid, "start": L(DAY, "06:00").isoformat(), "stop": L(DAY, "14:00").isoformat()})
    assert rel["failures"] == 1 and rel["mttr_s"] == 120 and rel["short_faults"] == 0
