"""Sprint 3 review fixes through the API: S3-4 (PLN-03 → MNT-02), S3-6 (extra shift overlap), planned
reason codes on breaks / calendar stops, S3-1 (holiday TEEP 0), S3-7/S3-8 (production outside the
calendar, grouped total = sum of points), D7 (pro-rata hourly counts on misaligned windows) and D11
(shift versions: editing or deleting a shift never rewrites the past)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oee_helpers import add_shift, close
from test_oee_api import DAY, L, TZ, _plant_with_tags, _product, _tenant, add_counts
from usage_helpers import api


async def _calendar(t, plant_id):
    from src.db import AsyncSessionLocal
    from src.services.oee.engine import load_calendar
    async with AsyncSessionLocal() as s:
        return await load_calendar(s, t.id, plant_id, TZ)


# ── S3-6 ──────────────────────────────────────────────────────────────────

async def test_extra_shift_overlapping_a_shift_instance_is_409(client):
    t = await _tenant("S36")
    h = t.headers("admin")
    p, _run, _fault = await _plant_with_tags(client, t, "S36")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    body = {"plant_id": p.plant["id"], "kind": "extra_shift", "date": DAY.isoformat(),
            "start_local": "10:00", "end_local": "18:00"}
    r = await api(client, "POST", "/api/calendar-exceptions/", h, 409, json=body)
    assert "Turno A" in r["detail"]
    ok = await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={**body, "start_local": "14:00"})
    # moving it onto the shift (PATCH) is refused too; two overlapping extra shifts as well
    await api(client, "PATCH", f"/api/calendar-exceptions/{ok['id']}", h, 409, json={"start_local": "13:00"})
    await api(client, "POST", "/api/calendar-exceptions/", h, 409, json={**body, "start_local": "16:00",
                                                                           "end_local": "20:00"})
    # on a holiday the shift does not run: the extra shift is accepted
    nxt = (DAY + timedelta(days=1)).isoformat()
    await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={
        "plant_id": p.plant["id"], "kind": "holiday", "date": nxt})
    await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={**body, "date": nxt})


async def test_breaks_and_planned_stops_need_a_planned_reason(client):
    t = await _tenant("PLNRC")
    h = t.headers("admin")
    p, _run, _fault = await _plant_with_tags(client, t, "PR")
    shift = {"plant_id": p.plant["id"], "name": "Turno A", "start_local": "06:00", "end_local": "14:00",
             "weekdays": [0, 1, 2, 3, 4, 5, 6]}
    r = await api(client, "POST", "/api/shifts/", h, 422, json={
        **shift, "breaks": [{"start_local": "10:00", "end_local": "10:15", "reason_code": "EQP-01"}]})
    assert "PLN" in r["detail"]
    await api(client, "POST", "/api/shifts/", h, 422, json={
        **shift, "breaks": [{"start_local": "10:00", "end_local": "10:15", "reason_code": "XYZ-99"}]})
    await api(client, "POST", "/api/calendar-exceptions/", h, 422, json={
        "plant_id": p.plant["id"], "kind": "planned_stop", "date": DAY.isoformat(), "reason_code": "EQP-02"})
    await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={
        "plant_id": p.plant["id"], "kind": "planned_stop", "date": DAY.isoformat(), "reason_code": "PLN-03"})


# ── S3-4 ──────────────────────────────────────────────────────────────────

async def test_pln03_outside_a_calendar_preventive_is_422_suggesting_mnt02(client):
    t = await _tenant("S34")
    h, op = t.headers("admin"), t.headers("operator")
    p, _run, _fault = await _plant_with_tags(client, t, "S34")
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    # outside every shift instance (QA repro) and inside a shift but not a planned preventive
    for a, b in ((L(DAY, "20:00"), L(DAY, "21:00")), (L(DAY, "08:00"), L(DAY, "09:00"))):
        r = await api(client, "POST", "/api/downtime/", op, 422, json={
            "machine_id": mid, "start": a.isoformat(), "end": b.isoformat(), "reason_code": "PLN-03"})
        assert r["detail"]["suggested_reason_code"] == "MNT-02"
    # MNT-02 is accepted; and PATCHing an event to PLN-03 is refused the same way
    ev = await api(client, "POST", "/api/downtime/", op, 201, json={
        "machine_id": mid, "start": L(DAY, "08:00").isoformat(), "end": L(DAY, "09:00").isoformat(),
        "reason_code": "MNT-02"})
    r = await api(client, "PATCH", f"/api/downtime/{ev['id']}", op, 422, json={"reason_code": "PLN-03"})
    assert r["detail"]["suggested_reason_code"] == "MNT-02"
    # with the preventive in the calendar (planned_stop PLN-03 10:00–12:00) it is accepted
    await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={
        "plant_id": p.plant["id"], "kind": "planned_stop", "date": DAY.isoformat(), "start_local": "10:00",
        "end_local": "12:00", "reason_code": "PLN-03"})
    got = await api(client, "POST", "/api/downtime/", op, 201, json={
        "machine_id": mid, "start": L(DAY, "10:00").isoformat(), "end": L(DAY, "11:00").isoformat(),
        "reason_code": "PLN-03"})
    assert got["planned"] is True
    # other PLN-* reclassifications stay allowed (S3-4: they become PDOT)
    await api(client, "PATCH", f"/api/downtime/{ev['id']}", op, 200, json={"reason_code": "PLN-02"})


# ── S3-1, S3-7, S3-8, D7 ──────────────────────────────────────────────────

async def test_holiday_teep_zero_outside_production_and_grouped_total(client):
    t = await _tenant("S37")
    h, op = t.headers("admin"), t.headers("operator")
    p, _run, _fault = await _plant_with_tags(client, t, "S37")
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    await _product(client, t, mid, "P-37", 30)
    hol = DAY + timedelta(days=1)
    await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={
        "plant_id": p.plant["id"], "kind": "holiday", "date": hol.isoformat()})
    await add_counts(t.id, mid, L(DAY, "07:00"), 100, 0)          # in the shift
    from src.db import AsyncSessionLocal
    from src.models import MachineDataSpan
    async with AsyncSessionLocal() as s:                           # the machine reported all shift long
        s.add(MachineDataSpan(tenant_id=t.id, machine_id=mid, start=L(DAY, "06:00"), end=L(DAY, "14:00")))
        await s.commit()
    await add_counts(t.id, mid, L(hol, "09:00"), 12, 0)           # maintenance test on the holiday
    # holiday alone: no planned time, TEEP 0 (not null), loading 0
    o = await api(client, "GET", "/api/oee", op, params={
        "scope": "machine", "id": mid, "start": L(hol, "00:00").isoformat(), "stop": L(hol, "00:00", 1).isoformat()})
    assert o["oee"] is None and o["reason"] == "no_planned_time" and o["loading"] == 0 and o["teep"] == 0
    assert o["produced"] == 0 and o["produced_outside_schedule"] == {"total": 12, "good": 12, "scrap": 0}
    # both days: the interval total ignores the 12 off-schedule parts and equals the sum of the days
    params = {"scope": "machine", "id": mid, "start": L(DAY, "00:00").isoformat(),
              "stop": L(hol, "00:00", 1).isoformat()}
    free = await api(client, "GET", "/api/oee", op, params=params)
    grouped = await api(client, "GET", "/api/oee", op, params={**params, "group_by": "day"})
    assert free["produced"] == 100 and free["produced_outside_schedule"]["total"] == 12
    assert grouped["produced"] == 100 and sum(pt["good"] for pt in grouped["points"]) == 100
    for k in ("pbt_s", "net_productive_s", "teep", "loading"):
        assert close(free[k], grouped[k]), k
    assert close(grouped["teep"], 100 * 30 / (2 * 86400))


async def test_misaligned_window_counts_hourly_parts_pro_rata(client):
    t = await _tenant("D7")
    op = t.headers("operator")
    p, _run, _fault = await _plant_with_tags(client, t, "D7")
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    await _product(client, t, mid, "P-D7", 3)
    await add_counts(t.id, mid, L(DAY, "09:00"), 60, 0)
    base = {"scope": "machine", "id": mid}
    q = await api(client, "GET", "/api/oee", op, params={
        **base, "start": L(DAY, "09:30").isoformat(), "stop": L(DAY, "09:45").isoformat()})
    assert close(q["produced"], 15) and q["counts_prorated"] is True        # was 0 (bucket start outside)
    full = await api(client, "GET", "/api/oee", op, params={
        **base, "start": L(DAY, "09:00").isoformat(), "stop": L(DAY, "10:00").isoformat()})
    assert full["produced"] == 60 and full["counts_prorated"] is False
    # adjacent windows never double count
    a = await api(client, "GET", "/api/oee", op, params={
        **base, "start": L(DAY, "09:00").isoformat(), "stop": L(DAY, "09:20").isoformat()})
    b = await api(client, "GET", "/api/oee", op, params={
        **base, "start": L(DAY, "09:20").isoformat(), "stop": L(DAY, "10:00").isoformat()})
    assert close(a["produced"] + b["produced"], 60)


# ── D11: shift versions ──────────────────────────────────────────────────

async def _age(shift_id, days):
    from sqlalchemy import update
    from src.db import AsyncSessionLocal
    from src.models import Shift
    async with AsyncSessionLocal() as s:
        await s.execute(update(Shift).where(Shift.id == shift_id).values(
            created_at=datetime.now(timezone.utc) - timedelta(days=days)))
        await s.commit()


async def test_editing_a_shift_that_ran_keeps_the_past_schedule(client):
    t = await _tenant("D11")
    h = t.headers("admin")
    p, _run, _fault = await _plant_with_tags(client, t, "D11")
    sh = await add_shift(client, t, p.plant["id"], "Turno B", "14:00", "22:00")
    await _age(sh["id"], 30)
    today = datetime.now(TZ).date()
    new = await api(client, "PATCH", f"/api/shifts/{sh['id']}", h, json={"start_local": "15:00", "end_local": "23:00"})
    assert new["id"] != sh["id"] and new["valid_from"] == today.isoformat() and new["start_local"] == "15:00"
    cal = await _calendar(t, p.plant["id"])
    past = cal.day_instances(DAY)
    assert [(i.shift_id, i.start) for i in past] == [(sh["id"], L(DAY, "14:00"))]       # history unchanged
    assert [(i.shift_id, i.start) for i in cal.day_instances(today)] == [(new["id"], L(today, "15:00"))]
    listed = await api(client, "GET", "/api/shifts/", h, params={"plant_id": p.plant["id"]})
    assert [s["id"] for s in listed] == [new["id"]]
    hist = await api(client, "GET", "/api/shifts/", h, params={"plant_id": p.plant["id"], "include_history": "true"})
    old = next(s for s in hist if s["id"] == sh["id"])
    assert old["valid_to"] == (today - timedelta(days=1)).isoformat() and old["is_active"] is False
    # a closed version cannot be edited; a name-only edit of the current one is in place
    await api(client, "PATCH", f"/api/shifts/{sh['id']}", h, 409, json={"is_active": False})
    same = await api(client, "PATCH", f"/api/shifts/{new['id']}", h, json={"name": "Turno B2"})
    assert same["id"] == new["id"] and same["name"] == "Turno B2"
    # deactivating the version that starts today (never ran a past day) is in place: from today no
    # instance, the past keeps the old version
    off = await api(client, "PATCH", f"/api/shifts/{new['id']}", h, json={"is_active": False})
    assert off["id"] == new["id"] and off["valid_to"] is None
    cal = await _calendar(t, p.plant["id"])
    assert cal.day_instances(today) == [] and len(cal.day_instances(DAY)) == 1


async def test_deleting_a_shift_with_stops_is_409_and_one_that_ran_is_closed(client):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    t = await _tenant("D11DEL")
    h = t.headers("admin")
    p, _run, _fault = await _plant_with_tags(client, t, "DD")
    a = await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    b = await add_shift(client, t, p.plant["id"], "Turno B", "14:00", "22:00")
    c = await add_shift(client, t, p.plant["id"], "Turno C", "22:00", "06:00")
    async with AsyncSessionLocal() as s:
        s.add(DowntimeEvent(tenant_id=t.id, machine_id=p.machine["id"], start=L(DAY, "08:00"), end=L(DAY, "08:30"),
                            duration_s=1800, source="auto_run_state", planned=False, shift_id=a["id"],
                            productive_day=DAY))
        await s.commit()
    await api(client, "DELETE", f"/api/shifts/{a['id']}", h, 409)
    await _age(b["id"], 30)
    await api(client, "DELETE", f"/api/shifts/{b['id']}", h, 204)          # ran before: closed, not removed
    await api(client, "DELETE", f"/api/shifts/{c['id']}", h, 204)          # created today: removed
    cal = await _calendar(t, p.plant["id"])
    assert {i.shift_id for i in cal.day_instances(DAY)} == {a["id"], b["id"]}
    today = datetime.now(TZ).date()
    assert {i.shift_id for i in cal.day_instances(today)} == {a["id"]}
