"""Sprint 6 §2 / acceptance 4 — drill-down KPIs through the API with the kpis.md case E2 (`oee_two_shifts_mix`):
every level aggregates components (never a mean of OEEs), the plant Pareto minutes are the sum of the lines'
Pareto minutes, and `?period=yesterday` resolves to the productive day (first shift start → last shift end) at
every level."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import _make_tenant
from oee_helpers import add_shift, close
from test_oee_api import L, add_counts, detect_with, set_checkpoint, signal
from usage_helpers import add_tag, api, build_plant

TZ = ZoneInfo("America/Manaus")


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


async def _product(client, t, machine_id, code, ict, default=True):
    h = t.headers("admin")
    prod = await api(client, "POST", "/api/products/", h, 201, json={"code": code, "name": code})
    await api(client, "POST", f"/api/machines/{machine_id}/products", h, 201,
              json={"product_id": prod["id"], "ideal_cycle_time_s": ict, "is_default": default})
    return prod


async def test_e2_aggregation_pareto_sums_and_period_yesterday(client):
    t = await _tenant("DRL")
    h, ho = t.headers("admin"), t.headers("operator")
    day = datetime.now(TZ).date() - timedelta(days=1)            # yesterday's productive day (shifts 06–22)
    p = await build_plant(client, t, "DRL", tz="America/Manaus")
    m1 = p.machine
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": m1["id"]})
    line2 = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Linha 2 DRL", "plant_id": p.plant["id"]})
    m2 = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": line2["id"], "name": "Saída L2"})
    await api(client, "PATCH", f"/api/lines/{line2['id']}", h, json={"output_machine_id": m2["id"]})
    # E2 calendar: A 06–14 with meal 30 min; B 14–22 with meal 30 + meeting PLN-02 30
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00",
                    breaks=[{"start_local": "11:00", "end_local": "11:30"}])
    await add_shift(client, t, p.plant["id"], "Turno B", "14:00", "22:00",
                    breaks=[{"start_local": "18:00", "end_local": "18:30"},
                            {"start_local": "19:00", "end_local": "19:30", "reason_code": "PLN-02"}])
    x = await _product(client, t, m1["id"], "X", 30)
    y = await _product(client, t, m1["id"], "Y", 40, default=False)
    z = await _product(client, t, m2["id"], "Z", 60)
    await api(client, "POST", "/api/production-runs/", ho, 201, json={
        "machine_id": m1["id"], "product_id": x["id"], "started_at": L(day, "06:00").isoformat()})
    r2 = await api(client, "POST", "/api/production-runs/", ho, 201, json={
        "machine_id": m1["id"], "product_id": y["id"], "started_at": L(day, "18:00").isoformat()})
    await api(client, "POST", f"/api/production-runs/{r2['id']}/stop", ho, json={"ended_at": L(day, "22:00").isoformat()})
    zr = await api(client, "POST", "/api/production-runs/", ho, 201, json={
        "machine_id": m2["id"], "product_id": z["id"], "started_at": L(day, "06:00").isoformat()})
    await api(client, "POST", f"/api/production-runs/{zr['id']}/stop", ho, json={"ended_at": L(day, "22:00").isoformat()})

    start, end = L(day, "06:00"), L(day, "22:00")
    # machine 1: A — failure 25 min, setup 20 min, material 15 min (ADOT 60); B — failure 10 min
    run1 = await add_tag(client, t, p, "run1", "run_state", data_type="bool")
    fault1 = await add_tag(client, t, p, "fault1", "fault_state")
    pts1 = [(start, 1, 0), (L(day, "07:00"), 0, 5), (L(day, "07:25"), 1, 0), (L(day, "08:00"), 0, 0),
            (L(day, "08:20"), 1, 0), (L(day, "09:00"), 0, 0), (L(day, "09:15"), 1, 0),
            (L(day, "15:00"), 0, 5), (L(day, "15:10"), 1, 0)]
    await set_checkpoint(m1["id"], t.id, start - timedelta(seconds=1))
    await detect_with(m1["id"], t.id, signal(run1["id"], fault1["id"], pts1, end + timedelta(minutes=1), hb=30),
                      end + timedelta(minutes=1))
    # machine 2 (line 2): no data before 08:00 (gap: out of PBT, so the lines weigh differently), one 30 min stop
    p2 = type(p)(plant=p.plant, line=line2, machine=m2)
    run2 = await add_tag(client, t, p2, "run2", "run_state", data_type="bool")
    fault2 = await add_tag(client, t, p2, "fault2", "fault_state")
    pts2 = [(L(day, "08:00"), 1, 0), (L(day, "09:00"), 0, 7), (L(day, "09:30"), 1, 0)]
    await set_checkpoint(m2["id"], t.id, start - timedelta(seconds=1))
    await detect_with(m2["id"], t.id, signal(run2["id"], fault2["id"], pts2, end + timedelta(minutes=1), hb=30),
                      end + timedelta(minutes=1))
    # counts — m1: A 700 X (679 good); B 400 X 14–18 (390 good), 300 Y 18–22 (290 good); m2: 800 Z (780 good)
    for k in range(7):
        await add_counts(t.id, m1["id"], start + timedelta(hours=k), 97, 3)
    await add_counts(t.id, m1["id"], start + timedelta(hours=7), 0, 0)
    for k in range(4):
        await add_counts(t.id, m1["id"], L(day, "14:00") + timedelta(hours=k), [98, 98, 97, 97][k], [3, 2, 3, 2][k])
        await add_counts(t.id, m1["id"], L(day, "18:00") + timedelta(hours=k), [73, 73, 72, 72][k], [3, 2, 3, 2][k])
    for k in range(2, 16):
        await add_counts(t.id, m2["id"], start + timedelta(hours=k), [49, 48][k % 2], [1, 2][k % 2])

    q = {"period": "yesterday"}
    # machine level: E2 numbers, shift points, never the mean of the shift OEEs
    mo = await api(client, "GET", "/api/oee", ho, params={**q, "scope": "machine", "id": m1["id"], "group_by": "shift"})
    assert mo["period"]["productive_day"] == day.isoformat()
    assert datetime.fromisoformat(mo["period_start"].replace("Z", "+00:00")) == start
    assert datetime.fromisoformat(mo["period_end"].replace("Z", "+00:00")) == end
    a, b = mo["points"]
    assert close(a["oee"], 0.754444, 1e-5) and close(b["oee"], 0.924603, 1e-5)
    assert close(b["performance"], 0.975610, 1e-5) and close(b["quality"], 0.970833, 1e-5)
    assert close(mo["oee"], 0.836590, 1e-5) and close(mo["availability"], 0.919540, 1e-5)
    assert close(mo["performance"], 0.937500, 1e-5) and close(mo["quality"], 0.970444, 1e-5)
    assert not close(mo["oee"], 0.839524, 1e-4)                         # mean of the shift OEEs — wrong
    # line level = output machine (no bottleneck): same components
    l1 = await api(client, "GET", "/api/oee", ho, params={**q, "scope": "line", "id": p.line["id"]})
    assert close(l1["oee"], mo["oee"]) and close(l1["pbt_s"], mo["pbt_s"])
    l2 = await api(client, "GET", "/api/oee", ho, params={**q, "scope": "line", "id": line2["id"]})
    # plant = Σ components of the lines, never the mean of line OEEs
    po = await api(client, "GET", "/api/oee", ho, params={**q, "scope": "plant", "id": p.plant["id"]})
    pbt = l1["pbt_s"] + l2["pbt_s"]
    assert l2["pbt_s"] < l1["pbt_s"] and l2["data_coverage"] < 1
    assert close(po["pbt_s"], pbt) and close(po["apt_s"], l1["apt_s"] + l2["apt_s"])
    assert close(po["good"], l1["good"] + l2["good"]) and close(po["scrap"], l1["scrap"] + l2["scrap"])
    ideal_good = l1["oee"] * l1["pbt_s"] + l2["oee"] * l2["pbt_s"]
    assert close(po["oee"], ideal_good / pbt, 1e-6)
    assert not close(po["oee"], (l1["oee"] + l2["oee"]) / 2, 1e-4)
    assert close(po["availability"], (l1["apt_s"] + l2["apt_s"]) / pbt, 1e-6)

    # Pareto: minutes of the plant = Σ minutes of the lines, per reason
    pp = await api(client, "GET", "/api/losses/pareto", ho, params={**q, "scope": "plant", "id": p.plant["id"]})
    pl1 = await api(client, "GET", "/api/losses/pareto", ho, params={**q, "scope": "line", "id": p.line["id"]})
    pl2 = await api(client, "GET", "/api/losses/pareto", ho, params={**q, "scope": "line", "id": line2["id"]})
    def by(rows):
        out = {}
        for r in rows:
            out[r["reason_code"]] = out.get(r["reason_code"], 0.0) + r["duration_s"]
        return out
    bp, b1, b2 = by(pp), by(pl1), by(pl2)
    assert set(bp) == set(b1) | set(b2)
    for code, dur in bp.items():
        assert close(dur, b1.get(code, 0.0) + b2.get(code, 0.0)), code
    stops = {r["reason_code"]: r["duration_s"] for r in pp if r["events"]}
    assert stops == {"EQP-01": (25 + 10 + 30) * 60, "UNK-01": (20 + 15) * 60}   # ADOT minutes add up line → plant
    assert close(bp["speed_loss"], b1["speed_loss"] + b2["speed_loss"])
    # "today" is a productive day too (never 00:00–00:00); custom needs start/stop; unknown → 422
    td = await api(client, "GET", "/api/oee", ho, params={"period": "today", "scope": "plant", "id": p.plant["id"]})
    ts = datetime.fromisoformat(td["period_start"].replace("Z", "+00:00")).astimezone(TZ)
    assert (ts.hour, ts.minute) == (6, 0)
    await api(client, "GET", "/api/oee", ho, 422, params={"period": "custom", "scope": "plant", "id": p.plant["id"]})
    await api(client, "GET", "/api/oee", ho, 422, params={"period": "ontem", "scope": "plant", "id": p.plant["id"]})
    # isolation: another tenant's line/plant → 404 whatever the period
    other = await _tenant("DRLB")
    await api(client, "GET", "/api/oee", other.headers("admin"), 404, params={**q, "scope": "plant", "id": p.plant["id"]})
    await api(client, "GET", "/api/losses/pareto", other.headers("admin"), 404,
              params={**q, "scope": "line", "id": p.line["id"]})
