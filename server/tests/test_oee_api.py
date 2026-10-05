"""Fase 2 API: registries (shifts, calendar, products, PRI, runs, reason codes), downtime flow
(detector with a synthetic signal source, operator justification, manual stops, failures, WebSocket),
OEE aggregation (shift crossing midnight, product mix, line PRI of the bottleneck, TEEP), data gaps,
and tenant isolation of every new endpoint."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from conftest import _make_tenant
from oee_helpers import add_shift, close
from usage_helpers import add_position, add_tag, api, build_plant

TZ = ZoneInfo("America/Manaus")
UTC = timezone.utc
DAY = date.today() - timedelta(days=10)                      # a past productive day


def zs(d: datetime) -> str:
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def L(day: date, hhmm: str, plus_days: int = 0) -> datetime:
    """Local Manaus time → aware UTC."""
    h, m = map(int, hhmm.split(":")[:2])
    s = int(hhmm.split(":")[2]) if hhmm.count(":") == 2 else 0
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ).astimezone(UTC) + timedelta(days=plus_days)


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


def signal(run_tag, fault_tag, points, end, hb=10):
    """points: [(datetime, run, fault)] → (tag_id, ts, value) rows with heartbeats until `end`."""
    rows = []
    for i, (t0, run, fault) in enumerate(points):
        nxt = points[i + 1][0] if i + 1 < len(points) else end
        cur = t0
        while cur < nxt or cur == t0:
            rows.append((run_tag, cur, float(run)))
            if fault_tag:
                rows.append((fault_tag, cur, float(fault)))
            cur += timedelta(seconds=hb)
            if cur >= nxt:
                break
    rows.sort(key=lambda r: r[1])
    return rows


async def detect_with(machine_id, tenant_id, rows, now):
    from src.db import AsyncSessionLocal
    from src.services.oee.detector import detect_machine
    from src.services.oee.engine import load_calendar, machine_infos

    async def fetch(tid, tag_ids, after, stop):
        return [r for r in rows if r[0] in tag_ids and after < r[1] <= stop]

    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, tenant_id, [machine_id]))[machine_id]
        cal = await load_calendar(s, tenant_id, info.plant_id, info.tz)
        return await detect_machine(s, info, cal, now, fetch)


async def set_checkpoint(machine_id, tenant_id, at):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    async with AsyncSessionLocal() as s:
        s.add(DowntimeDetectorState(machine_id=machine_id, tenant_id=tenant_id, checkpoint=at, state={}))
        await s.commit()


async def add_counts(tenant_id, machine_id, hour_start, good, scrap):
    from src.db import AsyncSessionLocal
    from src.models import UsageBucket
    async with AsyncSessionLocal() as s:
        for m, v in (("good", good), ("scrap", scrap), ("total", good + scrap)):
            s.add(UsageBucket(scope="machine", entity_id=machine_id, measure=m, bucket_start=hour_start,
                              tenant_id=tenant_id, value=v))
        await s.commit()


async def _plant_with_tags(client, t, label):
    p = await build_plant(client, t, label, tz="America/Manaus")
    run = await add_tag(client, t, p, "run", "run_state", data_type="bool")
    fault = await add_tag(client, t, p, "fault", "fault_state")
    await add_tag(client, t, p, "good", "part_counter_good")
    await add_tag(client, t, p, "scrap", "part_counter_scrap")
    return p, run, fault


async def _product(client, t, machine_id, code, ict, default=True):
    h = t.headers("admin")
    prod = await api(client, "POST", "/api/products/", h, 201, json={"code": code, "name": code})
    await api(client, "POST", f"/api/machines/{machine_id}/products", h, 201,
              json={"product_id": prod["id"], "ideal_cycle_time_s": ict, "is_default": default})
    return prod


# ── shift crossing midnight, breaks, productive day ─────────────────────────

async def test_night_shift_crossing_midnight(client):
    t = await _tenant("NIGHT")
    p, run, fault = await _plant_with_tags(client, t, "N")
    await add_shift(client, t, p.plant["id"], "Turno C", "22:00", "06:00",
                    breaks=[{"start_local": "02:00", "end_local": "03:00", "reason_code": "PLN-01"}])
    await _product(client, t, p.machine["id"], "P-N", 60)
    start, end = L(DAY, "22:00"), L(DAY, "06:00", 1)
    pts = [(start, 1, 0),
           (L(DAY, "23:50"), 0, 0), (L(DAY, "00:20", 1), 1, 0),        # 30 min across midnight
           (L(DAY, "02:55", 1), 0, 0), (L(DAY, "03:05", 1), 1, 0),     # 10 min across break end: 5 ADOT + 5 PDOT
           (L(DAY, "05:55", 1), 0, 7), (L(DAY, "06:10", 1), 1, 0)]     # fault across shift end → clipped
    rows = signal(run["id"], fault["id"], pts, L(DAY, "06:30", 1))
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(minutes=1))
    res = await detect_with(p.machine["id"], t.id, rows, L(DAY, "06:30", 1))
    assert {m["action"] for m in res.messages} <= {"open", "close", "update"} and res.messages
    for c in range(2):
        await add_counts(t.id, p.machine["id"], start + timedelta(hours=c), 30, 0)

    h = t.headers("operator")
    evs = sorted(await api(client, "GET", "/api/downtime/", h, params={"machine_id": p.machine["id"]}),
                 key=lambda e: e["start"])
    assert [(e["start"], e["end"]) for e in evs] == [
        (zs(L(DAY, "23:50")), zs(L(DAY, "00:20", 1))),
        (zs(L(DAY, "02:55", 1)), zs(L(DAY, "03:05", 1))),
        (zs(L(DAY, "05:55", 1)), zs(L(DAY, "06:00", 1)))]
    assert {e["productive_day"] for e in evs} == {DAY.isoformat()}          # belongs to the start day
    assert evs[2]["suggested_reason_code"] == "EQP-01" and evs[2]["failure_id"]

    o = await api(client, "GET", "/api/oee", h, params={
        "scope": "machine", "id": p.machine["id"], "start": start.isoformat(), "stop": end.isoformat(),
        "group_by": "day"})
    assert o["pot_s"] == 8 * 3600 and o["pdot_s"] == 3600 and close(o["adot_s"], (30 + 5 + 5) * 60)
    assert close(o["availability"], (7 * 3600 - 40 * 60) / (7 * 3600))
    assert [pt["label"] for pt in o["points"]] == [DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()]
    assert close(o["points"][0]["oee"], o["oee"])            # the whole night belongs to DAY
    assert o["points"][1]["pbt_s"] == 0 and o["points"][1]["oee"] is None
    # TEEP of a day point = Σ PRI·GQ / calendar time of that day inside the query (22:00–24:00)
    assert close(o["points"][0]["teep"], 60 * 60 / (2 * 3600))


# ── data gap → coverage ─────────────────────────────────────────────────────

async def test_data_gap_excluded_from_pbt_with_coverage(client):
    t = await _tenant("GAP")
    p, run, fault = await _plant_with_tags(client, t, "G")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    await _product(client, t, p.machine["id"], "P-G", 30)
    start, end = L(DAY, "06:00"), L(DAY, "14:00")
    rows = signal(run["id"], fault["id"], [(start, 1, 0)], L(DAY, "10:00"))
    rows = [r for r in rows if r[1] <= L(DAY, "10:00")]
    last = max(r[1] for r in rows)                                  # valid until last + 60 s (C11)
    gap_s = (L(DAY, "10:30") - (last + timedelta(seconds=60))).total_seconds()
    rows += signal(run["id"], fault["id"], [(L(DAY, "10:30"), 1, 0)], end + timedelta(minutes=1))
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(seconds=1))
    await detect_with(p.machine["id"], t.id, rows, end + timedelta(minutes=1))
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params={
        "scope": "machine", "id": p.machine["id"], "start": start.isoformat(), "stop": end.isoformat()})
    assert close(o["nodata_s"], gap_s) and 29 * 60 <= gap_s < 30 * 60
    assert close(o["data_coverage"], 1 - gap_s / (480 * 60))
    assert close(o["pbt_s"], 480 * 60 - gap_s) and o["adot_s"] == 0      # not a stop, not running
    assert o["availability"] == 1.0


async def test_machine_without_any_data_has_zero_coverage(client):
    t = await _tenant("NODATA")
    p, run, fault = await _plant_with_tags(client, t, "ND")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "10:00", "end_local": "11:00"}])
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params={
        "scope": "machine", "id": p.machine["id"], "start": L(DAY, "06:00").isoformat(),
        "stop": L(DAY, "14:00").isoformat()})
    assert o["data_coverage"] == 0 and o["pbt_s"] == 0 and o["nodata_s"] == 7 * 3600
    assert o["availability"] is None and o["oee"] is None and o["reason"] == "insufficient_data"


# ── aggregation: product mix, shifts, line PRI of the bottleneck ───────────

async def test_aggregation_sums_components_mix_and_line(client):
    t = await _tenant("AGG")
    h = t.headers("admin")
    p, run, fault = await _plant_with_tags(client, t, "AG")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "11:00", "end_local": "11:30"}])
    await add_shift(client, t, p.plant["id"], "Turno B", "14:00", "22:00",
                    breaks=[{"start_local": "18:00", "end_local": "19:00"}])
    x = await _product(client, t, p.machine["id"], "X", 30, default=True)
    y = await _product(client, t, p.machine["id"], "Y", 40, default=False)
    # runs: X for shift A, then X until 18:00 and Y from 18:00 (hour aligned)
    await api(client, "POST", "/api/production-runs/", t.headers("operator"), 201, json={
        "machine_id": p.machine["id"], "product_id": x["id"], "started_at": L(DAY, "06:00").isoformat()})
    r2 = await api(client, "POST", "/api/production-runs/", t.headers("operator"), 201, json={
        "machine_id": p.machine["id"], "product_id": y["id"], "started_at": L(DAY, "18:00").isoformat()})
    runs = await api(client, "GET", "/api/production-runs/", h, params={"machine_id": p.machine["id"]})
    assert runs[0]["ended_at"] is None                     # starting Y closed X at 18:00
    assert datetime.fromisoformat(runs[1]["ended_at"].replace("Z", "+00:00")) == L(DAY, "18:00")
    stopped = await api(client, "POST", f"/api/production-runs/{r2['id']}/stop", t.headers("operator"),
                        json={"ended_at": L(DAY, "22:00").isoformat()})
    assert stopped["ended_at"] is not None
    # all running except a 25 min failure in A and a 10 min one in B
    start, end = L(DAY, "06:00"), L(DAY, "22:00")
    pts = [(start, 1, 0), (L(DAY, "08:00"), 0, 5), (L(DAY, "08:25"), 1, 0),
           (L(DAY, "15:00"), 0, 5), (L(DAY, "15:10"), 1, 0)]
    rows = signal(run["id"], fault["id"], pts, end + timedelta(minutes=1), hb=30)
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(seconds=1))
    await detect_with(p.machine["id"], t.id, rows, end + timedelta(minutes=1))
    # A: 700 parts of X (679 good); B: 400 X (390 good) 14–18 h, 300 Y (290 good) 18–22 h
    for k in range(7):
        await add_counts(t.id, p.machine["id"], start + timedelta(hours=k), 97, 3)
    await add_counts(t.id, p.machine["id"], start + timedelta(hours=7), 0, 0)
    for k in range(4):
        await add_counts(t.id, p.machine["id"], L(DAY, "14:00") + timedelta(hours=k), [98, 98, 97, 97][k], [3, 2, 3, 2][k])
        await add_counts(t.id, p.machine["id"], L(DAY, "18:00") + timedelta(hours=k), [73, 73, 72, 72][k], [3, 2, 3, 2][k])
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params={
        "scope": "machine", "id": p.machine["id"], "start": start.isoformat(), "stop": end.isoformat(),
        "group_by": "shift"})
    a, b = o["points"]
    pbt_a, apt_a = (480 - 30) * 60, (480 - 30 - 25) * 60
    pbt_b, apt_b = (480 - 60) * 60, (480 - 60 - 10) * 60
    ig_a, ig_b = 679 * 30, 390 * 30 + 290 * 40
    ip_a, ip_b = 700 * 30, 400 * 30 + 300 * 40
    assert close(a["oee"], ig_a / pbt_a) and close(b["oee"], ig_b / pbt_b)
    assert close(a["performance"], ip_a / apt_a)
    assert close(b["performance"], ip_b / apt_b) and close(b["quality"], ig_b / ip_b)
    assert close(o["oee"], (ig_a + ig_b) / (pbt_a + pbt_b))
    assert close(o["availability"], (apt_a + apt_b) / (pbt_a + pbt_b))
    assert not close(o["oee"], (a["oee"] + b["oee"]) / 2)               # never the mean of percentages
    assert o["product_unknown"] is False

    # line: output machine counts; PRI of the bottleneck for the product (X: 36 s, Y: 48 s)
    m2 = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": p.line["id"], "name": "Gargalo"})
    await api(client, "POST", f"/api/machines/{m2['id']}/products", h, 201,
              json={"product_id": x["id"], "ideal_cycle_time_s": 36, "is_default": True})
    await api(client, "POST", f"/api/machines/{m2['id']}/products", h, 201,
              json={"product_id": y["id"], "ideal_cycle_time_s": 48})
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={
        "output_machine_id": p.machine["id"], "bottleneck_machine_id": m2["id"]})
    lo = await api(client, "GET", "/api/oee", t.headers("operator"), params={
        "scope": "line", "id": p.line["id"], "start": start.isoformat(), "stop": end.isoformat()})
    ig_line = (679 + 390) * 36 + 290 * 48
    assert close(lo["oee"], ig_line / (pbt_a + pbt_b))
    assert close(lo["availability"], o["availability"])
    # plant = sum of lines (one line here) and TEEP over the 16 h window
    po = await api(client, "GET", "/api/oee", t.headers("operator"), params={
        "scope": "plant", "id": p.plant["id"], "start": start.isoformat(), "stop": end.isoformat()})
    assert close(po["oee"], lo["oee"]) and close(po["teep"], ig_line / (16 * 3600))


async def test_product_unknown_and_missing_pri(client):
    t = await _tenant("PRI")
    h = t.headers("admin")
    p, run, fault = await _plant_with_tags(client, t, "PR")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    start, end = L(DAY, "06:00"), L(DAY, "14:00")
    await add_counts(t.id, p.machine["id"], start, 100, 0)
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(seconds=1))
    await detect_with(p.machine["id"], t.id, signal(run["id"], fault["id"], [(start, 1, 0)], end, hb=30),
                      end + timedelta(minutes=1))
    params = {"scope": "machine", "id": p.machine["id"], "start": start.isoformat(), "stop": end.isoformat()}
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params=params)
    assert o["performance"] is None and o["oee"] is None and o["reason"] == "insufficient_data"
    await _product(client, t, p.machine["id"], "D", 60)              # default product, no run
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params=params)
    assert o["product_unknown"] is True and o["oee"] is not None
    # a run of a product without PRI on this machine → insufficient_data
    z = await api(client, "POST", "/api/products/", h, 201, json={"code": "Z", "name": "Z"})
    await api(client, "POST", "/api/production-runs/", t.headers("operator"), 201, json={
        "machine_id": p.machine["id"], "product_id": z["id"], "started_at": start.isoformat()})
    o = await api(client, "GET", "/api/oee", t.headers("operator"), params=params)
    assert o["oee"] is None and o["reason"] == "insufficient_data" and o["product_unknown"] is False


# ── downtime: justification, manual, failures, WebSocket ───────────────────

async def test_downtime_flow_justify_manual_failures_ws(client, monkeypatch):
    from src.websocket import manager as wsm
    sent = []

    async def capture(tenant_id, msg):
        sent.append((tenant_id, msg))

    monkeypatch.setattr(wsm.manager, "broadcast", capture)
    t = await _tenant("DT")
    p, run, fault = await _plant_with_tags(client, t, "D")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    start = L(DAY, "06:00")
    # stop at 07:00 that becomes an error at 07:10 (fault 9), back running at 07:20
    pts = [(start, 1, 0), (L(DAY, "07:00"), 0, 0), (L(DAY, "07:10"), 0, 9), (L(DAY, "07:20"), 1, 0)]
    rows = signal(run["id"], fault["id"], pts, L(DAY, "08:00"))
    # live pass at 07:05: open event
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(seconds=1))
    res = await detect_with(p.machine["id"], t.id, rows, L(DAY, "07:05"))
    assert [m["action"] for m in res.messages] == ["open"]
    ev = res.messages[0]["event"]
    assert ev["end"] is None and ev["machine_id"] == p.machine["id"] and ev["suggested_reason_code"] is None
    op = t.headers("operator")
    opened = await api(client, "GET", "/api/downtime/", op, params={"open": "true", "line_id": p.line["id"]})
    assert [e["id"] for e in opened] == [ev["id"]]
    res = await detect_with(p.machine["id"], t.id, rows, L(DAY, "08:00"))
    assert ("close" in [m["action"] for m in res.messages])
    e = (await api(client, "GET", "/api/downtime/", op, params={"machine_id": p.machine["id"]}))[0]
    assert e["id"] == ev["id"] and e["duration_s"] == 1200 and e["suggested_reason_code"] == "EQP-01"
    assert e["fault_code"] == 9 and e["fault_since"] == zs(L(DAY, "07:10"))
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        fr = (await s.execute(select(FailureRecord).where(FailureRecord.id == e["failure_id"]))).scalar_one()
    assert fr.failed_at == L(DAY, "07:10") and fr.restored_at == L(DAY, "07:20") and fr.status == "draft"

    # operator: it was actually a setup → failure draft removed; WS 'justify'
    sent.clear()
    j = await api(client, "PATCH", f"/api/downtime/{e['id']}", op, json={"reason_code": "SET-01", "comment": "troca"})
    assert j["reason_code"] == "SET-01" and j["failure_id"] is None and j["comment"] == "troca"
    assert j["justified_by"] == t.users["operator"]["id"]
    assert sent and sent[0][0] == t.id and sent[0][1]["type"] == "downtime" and sent[0][1]["action"] == "justify"
    assert sent[0][1]["event"]["reason_code"] == "SET-01"
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(FailureRecord).where(FailureRecord.id == fr.id))).scalar_one_or_none() is None
    # unknown code → 422; planned code → planned (PLN-03 needs a calendar preventive: S3-4, 422 → MNT-02)
    await api(client, "PATCH", f"/api/downtime/{e['id']}", op, 422, json={"reason_code": "XXX-99"})
    await api(client, "PATCH", f"/api/downtime/{e['id']}", op, 422, json={"reason_code": "PLN-03"})
    pl = await api(client, "PATCH", f"/api/downtime/{e['id']}", op, json={"reason_code": "PLN-02"})
    assert pl["planned"] is True
    o = await api(client, "GET", "/api/oee", op, params={"scope": "machine", "id": p.machine["id"],
                                                         "start": start.isoformat(), "stop": L(DAY, "08:00").isoformat()})
    assert o["pdot_s"] == 1200 and o["adot_s"] == 0
    # re-running the detector keeps the operator's reason
    await detect_with(p.machine["id"], t.id, rows, L(DAY, "08:30"))
    e2 = (await api(client, "GET", "/api/downtime/", op, params={"machine_id": p.machine["id"]}))[0]
    assert e2["reason_code"] == "PLN-02" and e2["planned"]

    # manual stop with an EQP code → failure draft; validation
    m = await api(client, "POST", "/api/downtime/", op, 201, json={
        "machine_id": p.machine["id"], "start": L(DAY, "09:00").isoformat(), "end": L(DAY, "09:30").isoformat(),
        "reason_code": "EQP-03", "comment": "vazamento"})
    assert m["source"] == "manual" and m["failure_id"] and m["shift_id"] and m["productive_day"] == DAY.isoformat()
    await api(client, "POST", "/api/downtime/", op, 422, json={
        "machine_id": p.machine["id"], "start": L(DAY, "09:00").isoformat(), "end": L(DAY, "08:00").isoformat(),
        "reason_code": "EQP-03"})
    await api(client, "POST", "/api/downtime/", op, 422, json={
        "machine_id": p.machine["id"], "start": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "reason_code": "EQP-03"})
    rel = await api(client, "GET", "/api/reliability", op, params={
        "scope": "machine", "id": p.machine["id"], "start": start.isoformat(), "stop": L(DAY, "14:00").isoformat()})
    assert rel["failures"] == 1 and rel["mttr_s"] == 1800


async def test_unjustified_becomes_unk_after_shift_and_open_event_split(client):
    t = await _tenant("UNK")
    p, run, fault = await _plant_with_tags(client, t, "U")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    await add_shift(client, t, p.plant["id"], "Turno B", "14:00", "22:00")
    start = L(DAY, "06:00")
    rows = signal(run["id"], fault["id"], [(start, 1, 0), (L(DAY, "13:50"), 0, 0)], L(DAY, "14:20"))
    await set_checkpoint(p.machine["id"], t.id, start - timedelta(seconds=1))
    res = await detect_with(p.machine["id"], t.id, rows, L(DAY, "14:20"))
    evs = sorted(await api(client, "GET", "/api/downtime/", t.headers("operator"),
                           params={"machine_id": p.machine["id"]}), key=lambda e: e["start"])
    assert [(e["end"] is None, e["duration_s"]) for e in evs] == [(False, 600.0), (True, None)]
    assert evs[0]["reason_code"] == "UNK-01" and evs[1]["reason_code"] is None   # shift A over; B running
    assert evs[0]["stop_start"] == evs[1]["stop_start"]
    assert {m["action"] for m in res.messages} >= {"close", "open"}


# ── registries: validation, RBAC ────────────────────────────────────────────

async def test_registries_validation_and_rbac(client):
    t = await _tenant("REG")
    h, op = t.headers("admin"), t.headers("operator")
    p = await build_plant(client, t, "R")
    await api(client, "POST", "/api/shifts/", op, 403, json={
        "plant_id": p.plant["id"], "name": "A", "start_local": "06:00", "end_local": "14:00"})
    a = await add_shift(client, t, p.plant["id"], "A", "06:00", "14:00", weekdays=(0, 1, 2, 3, 4))
    await api(client, "POST", "/api/shifts/", h, 409, json={
        "plant_id": p.plant["id"], "name": "X", "start_local": "13:00", "end_local": "15:00", "weekdays": [0]})
    await api(client, "POST", "/api/shifts/", h, 422, json={
        "plant_id": p.plant["id"], "name": "X", "start_local": "25:00", "end_local": "15:00"})
    await api(client, "POST", "/api/shifts/", h, 422, json={
        "plant_id": p.plant["id"], "name": "X", "start_local": "14:00", "end_local": "22:00",
        "breaks": [{"start_local": "21:30", "end_local": "22:30"}]})
    up = await api(client, "PATCH", f"/api/shifts/{a['id']}", h, json={
        "breaks": [{"start_local": "10:00", "end_local": "10:15", "reason_code": "PLN-01"}]})
    assert up["breaks"][0]["start_local"] == "10:00"
    await api(client, "PATCH", f"/api/shifts/{a['id']}", h, 422, json={"end_local": "09:00"})
    lst = await api(client, "GET", "/api/shifts/", op, params={"plant_id": p.plant["id"]})
    assert [s["name"] for s in lst] == ["A"]

    ex = await api(client, "POST", "/api/calendar-exceptions/", h, 201, json={
        "plant_id": p.plant["id"], "date": DAY.isoformat(), "kind": "holiday", "note": "Feriado"})
    await api(client, "POST", "/api/calendar-exceptions/", h, 422, json={
        "plant_id": p.plant["id"], "date": DAY.isoformat(), "kind": "extra_shift"})
    await api(client, "PATCH", f"/api/calendar-exceptions/{ex['id']}", h, json={"note": "Natal"})
    o = await api(client, "GET", "/api/oee", op, params={
        "scope": "machine", "id": p.machine["id"], "start": L(DAY, "00:00").isoformat(),
        "stop": L(DAY, "00:00", 1).isoformat()})
    if DAY.weekday() < 5:
        assert o["pot_s"] == 0 and o["availability"] is None and o["reason"] == "no_planned_time"
    await api(client, "DELETE", f"/api/calendar-exceptions/{ex['id']}", h, 204)

    pr = await api(client, "POST", "/api/products/", h, 201, json={"code": "A1", "name": "Peça"})
    await api(client, "POST", "/api/products/", h, 409, json={"code": "A1", "name": "Dup"})
    await api(client, "POST", "/api/products/", h, 422, json={"code": "  ", "name": "x"})
    mp = await api(client, "POST", f"/api/machines/{p.machine['id']}/products", h, 201,
                   json={"product_id": pr["id"], "ideal_cycle_time_s": 40, "parts_per_cycle": 4, "is_default": True})
    assert mp["ideal_time_per_part_s"] == 10 and mp["product_code"] == "A1"
    await api(client, "POST", f"/api/machines/{p.machine['id']}/products", h, 409,
              json={"product_id": pr["id"], "ideal_cycle_time_s": 40})
    await api(client, "POST", f"/api/machines/{p.machine['id']}/products", h, 422,
              json={"product_id": pr["id"], "ideal_cycle_time_s": 0})
    run = await api(client, "POST", "/api/production-runs/", op, 201, json={
        "machine_id": p.machine["id"], "product_id": pr["id"]})
    await api(client, "DELETE", f"/api/products/{pr['id']}", h, 409)
    await api(client, "POST", f"/api/production-runs/{run['id']}/stop", op)
    await api(client, "POST", f"/api/production-runs/{run['id']}/stop", op, 409)

    codes = await api(client, "GET", "/api/reason-codes/", op)
    assert len([c for c in codes if c["is_default"]]) == 19
    unk = next(c for c in codes if c["code"] == "UNK-01")
    assert unk["category"] == "UNK" and not unk["planned"]
    await api(client, "POST", "/api/reason-codes/", op, 403, json={"code": "EQP-01-A", "category": "EQP", "label": "x"})
    await api(client, "POST", "/api/reason-codes/", h, 409, json={"code": "EQP-01", "category": "EQP", "label": "x"})
    rc = await api(client, "POST", "/api/reason-codes/", h, 201, json={
        "code": "EQP-01-A", "category": "EQP", "label": "Quebra do mandril"})
    await api(client, "PATCH", f"/api/reason-codes/{unk['id']}", h, 404, json={"label": "hack"})   # defaults are shared
    upd = await api(client, "PATCH", f"/api/reason-codes/{rc['id']}", h, json={"label": "Mandril"})
    assert upd["label"] == "Mandril" and not upd["is_default"]
    m = await api(client, "PATCH", f"/api/machines/{p.machine['id']}", h, json={
        "micro_stop_threshold_s": 90, "fault_code_map": {"12": "EQP-01-A"}})
    assert m["micro_stop_threshold_s"] == 90 and m["fault_code_map"] == {"12": "EQP-01-A"}
    await api(client, "PATCH", f"/api/machines/{p.machine['id']}", h, 422, json={"micro_stop_threshold_s": 10})
    await api(client, "PATCH", f"/api/machines/{p.machine['id']}", h, 422, json={"fault_code_map": {"x": "EQP-01"}})


# ── tenant isolation of every new endpoint ─────────────────────────────────

async def test_tenant_isolation_of_production_endpoints(client):
    ta, tb = await _tenant("ISO-A"), await _tenant("ISO-B")
    ha, hb = ta.headers("admin"), tb.headers("admin")
    p, run, fault = await _plant_with_tags(client, ta, "IA")
    pb = await build_plant(client, tb, "IB")
    shift = await add_shift(client, ta, p.plant["id"], "A", "06:00", "14:00")
    ex = await api(client, "POST", "/api/calendar-exceptions/", ha, 201, json={
        "plant_id": p.plant["id"], "date": (DAY - timedelta(days=1)).isoformat(), "kind": "holiday"})
    prod = await _product(client, ta, p.machine["id"], "ISO", 30)
    mps = await api(client, "GET", f"/api/machines/{p.machine['id']}/products", ha)
    runr = await api(client, "POST", "/api/production-runs/", ha, 201, json={
        "machine_id": p.machine["id"], "product_id": prod["id"]})
    rc = await api(client, "POST", "/api/reason-codes/", ha, 201, json={"code": "MAT-01-Z", "category": "MAT", "label": "z"})
    ev = await api(client, "POST", "/api/downtime/", ha, 201, json={
        "machine_id": p.machine["id"], "start": L(DAY, "07:00").isoformat(), "end": L(DAY, "07:30").isoformat(),
        "reason_code": "MAT-01"})

    # B sees nothing of A
    assert await api(client, "GET", "/api/shifts/", hb) == []
    assert await api(client, "GET", "/api/shifts/", hb, params={"plant_id": p.plant["id"]}) == []
    assert await api(client, "GET", "/api/calendar-exceptions/", hb) == []
    assert await api(client, "GET", "/api/products/", hb) == []
    assert await api(client, "GET", "/api/production-runs/", hb) == []
    assert await api(client, "GET", "/api/downtime/", hb) == []
    assert await api(client, "GET", "/api/downtime/", hb, params={"machine_id": p.machine["id"]}) == []
    assert "MAT-01-Z" not in {c["code"] for c in await api(client, "GET", "/api/reason-codes/", hb)}
    for url in (f"/api/shifts/{shift['id']}", f"/api/calendar-exceptions/{ex['id']}", f"/api/products/{prod['id']}",
                f"/api/production-runs/{runr['id']}", f"/api/machines/{p.machine['id']}/products"):
        await api(client, "GET", url, hb, 404)
    for method, url, body in (
            ("PATCH", f"/api/shifts/{shift['id']}", {"name": "x"}),
            ("DELETE", f"/api/shifts/{shift['id']}", None),
            ("PATCH", f"/api/calendar-exceptions/{ex['id']}", {"note": "x"}),
            ("DELETE", f"/api/calendar-exceptions/{ex['id']}", None),
            ("PATCH", f"/api/products/{prod['id']}", {"name": "x"}),
            ("DELETE", f"/api/products/{prod['id']}", None),
            ("PATCH", f"/api/machines/{p.machine['id']}/products/{mps[0]['id']}", {"ideal_cycle_time_s": 1}),
            ("DELETE", f"/api/machines/{p.machine['id']}/products/{mps[0]['id']}", None),
            ("POST", f"/api/production-runs/{runr['id']}/stop", {}),
            ("DELETE", f"/api/production-runs/{runr['id']}", None),
            ("PATCH", f"/api/reason-codes/{rc['id']}", {"label": "x"}),
            ("PATCH", f"/api/downtime/{ev['id']}", {"reason_code": "SET-01"})):
        r = await client.request(method, url, headers=hb, json=body)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    # B cannot reference A's rows
    await api(client, "POST", "/api/shifts/", hb, 404, json={
        "plant_id": p.plant["id"], "name": "x", "start_local": "06:00", "end_local": "14:00"})
    await api(client, "POST", "/api/calendar-exceptions/", hb, 404, json={
        "plant_id": p.plant["id"], "date": DAY.isoformat(), "kind": "holiday"})
    await api(client, "POST", "/api/calendar-exceptions/", hb, 404, json={
        "plant_id": pb.plant["id"], "date": DAY.isoformat(), "kind": "holiday", "shift_id": shift["id"]})
    await api(client, "POST", f"/api/machines/{pb.machine['id']}/products", hb, 404,
              json={"product_id": prod["id"], "ideal_cycle_time_s": 10})
    await api(client, "POST", "/api/production-runs/", hb, 404, json={
        "machine_id": p.machine["id"], "product_id": prod["id"]})
    await api(client, "POST", "/api/downtime/", hb, 404, json={
        "machine_id": p.machine["id"], "start": L(DAY, "07:00").isoformat(), "reason_code": "MAT-01"})
    await api(client, "PATCH", f"/api/downtime/{ev['id']}", hb, 404, json={"reason_code": "MAT-01-Z"})
    # A's tenant code is not usable by B
    evb = await api(client, "POST", "/api/downtime/", hb, 201, json={
        "machine_id": pb.machine["id"], "start": L(DAY, "07:00").isoformat(), "end": L(DAY, "07:10").isoformat(),
        "reason_code": "MAT-01"})
    await api(client, "PATCH", f"/api/downtime/{evb['id']}", hb, 422, json={"reason_code": "MAT-01-Z"})
    # KPI endpoints on A's ids → 404 for B
    win = {"start": L(DAY, "06:00").isoformat(), "stop": L(DAY, "14:00").isoformat()}
    for scope, sid in (("machine", p.machine["id"]), ("line", p.line["id"]), ("plant", p.plant["id"])):
        await api(client, "GET", "/api/oee", hb, 404, params={"scope": scope, "id": sid, **win})
        await api(client, "GET", "/api/losses/pareto", hb, 404, params={"scope": scope, "id": sid, **win})
    for scope, sid in (("machine", p.machine["id"]), ("line", p.line["id"])):
        await api(client, "GET", "/api/reliability", hb, 404, params={"scope": scope, "id": sid, **win})
    await api(client, "GET", "/api/reports/shift", hb, 404, params={
        "line_id": p.line["id"], "productive_day": DAY.isoformat()})
    await api(client, "GET", "/api/overview", hb, 404, params={"plant_id": p.plant["id"]})
    ov = await api(client, "GET", "/api/overview", hb)
    assert ov["plant_id"] == pb.plant["id"] and all(ln["line_id"] != p.line["id"] for ln in ov["lines"])
    # and A still sees its own
    assert len(await api(client, "GET", "/api/downtime/", ha)) == 1
    pa = await api(client, "GET", "/api/losses/pareto", ha, params={"scope": "machine", "id": p.machine["id"], **win})
    assert pa[0]["reason_code"] == "MAT-01"


# ── overview (raio-x) ───────────────────────────────────────────────────────

async def test_overview_raio_x(client):
    t = await _tenant("OV")
    h = t.headers("admin")
    p, run, fault = await _plant_with_tags(client, t, "OV")
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": p.machine["id"]})
    await _product(client, t, p.machine["id"], "OV", 30)
    now = datetime.now(UTC)
    # no shifts: implicit 24 h calendar
    await add_counts(t.id, p.machine["id"], now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1), 50, 2)
    await api(client, "POST", "/api/downtime/", t.headers("operator"), 201, json={
        "machine_id": p.machine["id"], "start": (now - timedelta(minutes=30)).isoformat(), "reason_code": "MAT-01"})
    await api(client, "PATCH", f"/api/machines/{p.machine['id']}", h, json={"status": "error"})
    pos = await add_position(client, t, p, "Cilindro OV")
    await api(client, "POST", "/api/components/", h, 201, json={
        "component_type": "pneumatic_cylinder", "serial_number": "OV-1", "position_id": pos["id"],
        "nominal_life": {"cycles": 100}, "installed_at": (now - timedelta(days=1)).isoformat()})
    from src.db import AsyncSessionLocal
    from src.models import Component, UsageTotal
    from sqlalchemy import select
    async with AsyncSessionLocal() as s:
        cid = (await s.execute(select(Component.id).where(Component.tenant_id == t.id))).scalar_one()
        s.add(UsageTotal(scope="component", entity_id=cid, measure="cycles", tenant_id=t.id, value=85))
        await s.commit()
    ov = await api(client, "GET", "/api/overview", t.headers("operator"), params={"plant_id": p.plant["id"]})
    ln = ov["lines"][0]
    assert ln["open_downtime_count"] == 1 and ln["good"] >= 0
    assert ov["machines_in_error"][0]["machine_id"] == p.machine["id"]
    assert ov["components_near_end"][0]["life_consumed_pct"] == 0.85
    assert "projected_end_date" in ov["components_near_end"][0]
    assert {"since", "fault_code"} <= set(ov["machines_in_error"][0])
    assert any(r["reason_code"] == "MAT-01" for r in ov["top_losses"])
    assert ln["oee_today"] is not None and ln["worst_machine"]["machine_id"] == p.machine["id"]
