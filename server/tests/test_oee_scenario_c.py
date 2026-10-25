"""Golden scenario C (docs/scenarios/scenario_c_oee_shift.json) end to end:
real MQTT handler (Influx + usage) → downtime detector (from Influx) → operator justification via the
API → GET /api/oee, /api/losses/pareto, /api/reliability, /api/downtime, /api/reports/shift.
Every `expected` number of the scenario must come out exactly (tolerance 1e-6, Pareto pct 5e-7)."""
from datetime import timedelta

from conftest import _make_tenant
from oee_helpers import (SCENARIO_C, close, publish_all, run_detector, scenario_c_readings, setup_scenario_c, ts)
from usage_helpers import api

C = SCENARIO_C
X = C["expected"]


async def _build(client):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, "SC")
        await s.commit()
    p, tags, shift, prod = await setup_scenario_c(client, t)
    await publish_all(t.id, scenario_c_readings(tags))
    end = ts(C["shift"]["end_utc"])
    # live passes during the shift (open events, merge window) and after it
    for minutes in (75, 150, 330, 481):
        res = await run_detector(p.machine["id"], t.id, ts(C["shift"]["start_utc"]) + timedelta(minutes=minutes))
    return t, p, shift, end, res


async def test_scenario_c_end_to_end(client):
    t, p, shift, end, last = await _build(client)
    start = ts(C["shift"]["start_utc"])
    h = t.headers("operator")
    mid = p.machine["id"]
    params = {"start": start.isoformat(), "stop": end.isoformat()}

    # ── downtime events (before justification) ──────────────────────────
    evs = await api(client, "GET", "/api/downtime/", h, params={"machine_id": mid, **params})
    evs = sorted(evs, key=lambda e: e["start"])
    local = lambda e: (ts_local(e["start"]))  # noqa: E731
    assert [local(e) for e in evs] == [d["from_local"] for d in X["downtime_events"]]
    assert [e["duration_s"] for e in evs] == [d["duration_s"] for d in X["downtime_events"]]
    assert all(e["end"] is not None for e in evs)
    by_local = {local(e): e for e in evs}
    assert by_local["07:12:00"]["suggested_reason_code"] == "EQP-01"
    assert by_local["09:45:00"]["suggested_reason_code"] == "EQP-02"
    assert by_local["12:20:00"]["suggested_reason_code"] == "EQP-01"
    assert by_local["07:12:00"]["source"] == "auto_fault" and by_local["08:30:00"]["source"] == "auto_run_state"
    # shift is over: the stop without reason and without suggestion became UNK-01 (regra 4)
    assert by_local["11:50:00"]["reason_code"] == "UNK-01" and by_local["11:50:00"]["unjustified"]
    assert all(e["shift_id"] == shift["id"] for e in evs)
    assert {e["productive_day"] for e in evs} == {ts(C["shift"]["start_utc"]).date().isoformat()}

    # operator justifies the two run_state stops (MAT-01, SET-01) and confirms the faults
    for lt, code in (("08:30:00", "MAT-01"), ("13:00:00", "SET-01"), ("07:12:00", "EQP-01"),
                     ("09:45:00", "EQP-02"), ("12:20:00", "EQP-01")):
        r = await api(client, "PATCH", f"/api/downtime/{by_local[lt]['id']}", h, json={"reason_code": code})
        assert r["reason_code"] == code and not r["planned"] and not r["unjustified"]

    evs = await api(client, "GET", "/api/downtime/", h, params={"machine_id": mid, **params})
    got = sorted((local(e), e["effective_reason_code"], e["duration_s"]) for e in evs)
    assert got == [(d["from_local"], d["reason_code"], d["duration_s"]) for d in X["downtime_events"]]
    unj = await api(client, "GET", "/api/downtime/", h, params={"machine_id": mid, "unjustified": "true", **params})
    assert [local(e) for e in unj] == ["11:50:00"]

    # ── OEE ─────────────────────────────────────────────────────────────
    o = await api(client, "GET", "/api/oee", h, params={"scope": "machine", "id": mid, **params})
    assert close(o["pot_s"], X["pot_min"] * 60) and close(o["pdot_s"], X["pdot_min"] * 60)
    assert close(o["pbt_s"], X["pbt_min"] * 60) and close(o["adot_s"], X["adot_min"] * 60)
    assert close(o["apt_s"], X["apt_min"] * 60)
    assert close(o["data_coverage"], X["data_coverage"])
    assert o["micro_stop_count"] == X["micro_stop_count"] and close(o["micro_stop_loss_s"], X["micro_stop_s"])
    assert close(o["micro_stops_per_apt_hour"], X["micro_stops_per_apt_hour"])
    assert close(o["ideal_produced_s"], X["ideal_time_produced_min"] * 60)
    assert close(o["net_productive_s"], X["net_productive_min"] * 60)
    for k in ("availability", "performance", "quality", "oee"):
        assert close(o[k], X[k]), (k, o[k], X[k])
    assert (o["good"], o["scrap"], o["produced"]) == (C["production"]["good"], C["production"]["scrap"],
                                                      C["production"]["produced"])
    cad = X["cadence"]
    assert close(o["cadence_planned_per_hour"], cad["parts_per_hour_planned"])
    assert close(o["cadence_running_per_hour"], cad["parts_per_hour_running"])
    assert close(o["speed_loss_s"], X["loss_pareto"]["speed_loss_min"] * 60)
    assert close(o["availability_operational"], X["availability_operational"]["ao"])
    assert o["product_unknown"] is False and o["calendar_default"] is False
    # TEEP over the shift window: Σ PRI·GQ / calendar time of the window (8 h here)
    assert close(o["teep"], X["net_productive_min"] / X["pot_min"])
    # wrong answers of the scenario are really different
    assert not close(o["availability"], X["wrong_answers"]["breaks_in_pbt"]["availability"])

    # group_by=shift gives one point equal to the total; group_by=hour sums back to it
    g = await api(client, "GET", "/api/oee", h, params={"scope": "machine", "id": mid, "group_by": "shift", **params})
    assert len(g["points"]) == 1 and close(g["points"][0]["oee"], X["oee"])
    assert g["points"][0]["label"].startswith(C["shift"]["name"])
    hr = await api(client, "GET", "/api/oee", h, params={"scope": "machine", "id": mid, "group_by": "hour", **params})
    assert len(hr["points"]) == 8
    assert close(sum(pt["pbt_s"] for pt in hr["points"]), X["pbt_min"] * 60)
    assert sum(pt["good"] for pt in hr["points"]) == C["production"]["good"]
    day = await api(client, "GET", "/api/oee", h, params={"scope": "machine", "id": mid, "group_by": "day", **params})
    assert len(day["points"]) == 1 and close(day["points"][0]["oee"], X["oee"])

    # line scope (output machine = this machine) = the same numbers
    lo = await api(client, "GET", "/api/oee", h, params={"scope": "line", "id": p.line["id"], **params})
    assert close(lo["oee"], X["oee"]) and close(lo["availability"], X["availability"])
    po = await api(client, "GET", "/api/oee", h, params={"scope": "plant", "id": p.plant["id"], **params})
    assert close(po["oee"], X["oee"])

    # ── Pareto ──────────────────────────────────────────────────────────
    par = await api(client, "GET", "/api/losses/pareto", h, params={"scope": "machine", "id": mid, **params})
    exp = X["loss_pareto"]["items"]
    assert [r["reason_code"] for r in par] == [i[0] for i in exp]
    for r, (code, minutes, pct, cum) in zip(par, exp):
        assert close(r["duration_s"], minutes * 60), (code, r["duration_s"])
        assert abs(r["pct"] - pct) <= 5e-7 and abs(r["cumulative_pct"] - cum) <= 5e-7
        assert r["planned"] is False
    assert close(sum(r["duration_s"] for r in par), X["loss_pareto"]["total_loss_min"] * 60)
    eqp = next(r for r in par if r["reason_code"] == "EQP-01")
    assert eqp["events"] == 2 and eqp["category"] == "EQP"
    micro = next(r for r in par if r["reason_code"] == "micro_stops")
    assert micro["events"] == X["micro_stop_count"]
    cat = await api(client, "GET", "/api/losses/pareto", h,
                    params={"scope": "machine", "id": mid, "by": "category", **params})
    assert [(r["category"], round(r["duration_s"] / 60, 6)) for r in cat] == \
        [(k, v) for k, v in X["loss_pareto_by_category"]]

    # ── reliability ─────────────────────────────────────────────────────
    rel = await api(client, "GET", "/api/reliability", h, params={"scope": "machine", "id": mid, **params})
    assert rel["failures"] == X["failures"]["n"] and rel["incipient"] == X["failures"]["incipient"]
    assert rel["short_faults"] == X["failures"]["short_faults_not_failures"]
    assert close(rel["operating_h"] * 60, X["operating_min_run_state"])
    assert close(rel["mtbf_h"], X["mtbf_h"])
    assert close(rel["mttr_s"], X["mttr_s"]) and close(rel["mttr_h"] * 60, X["mttr_min"])
    assert close(rel["availability_inherent"], X["mtbf_h"] / (X["mtbf_h"] + X["mttr_min"] / 60))
    assert not close(rel["mtbf_h"], X["wrong_answers"]["mtbf_with_apt"]["mtbf_h"])
    lrel = await api(client, "GET", "/api/reliability", h, params={"scope": "line", "id": p.line["id"], **params})
    assert lrel["failures"] == 3 and close(lrel["mtbf_h"], X["mtbf_h"])

    # failure drafts: failed_at = the instant fault_state went non-zero, restored_at = back running
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        frs = (await s.execute(select(FailureRecord).where(FailureRecord.tenant_id == t.id)
                               .order_by(FailureRecord.failed_at))).scalars().all()
    faults = [e for e in C["events"] if e["kind"] == "fault"]
    assert [(f.failed_at, f.restored_at) for f in frs] == [(ts(e["from_utc"]), ts(e["to_utc"])) for e in faults]
    assert all(f.status == "draft" and f.downtime_event_id for f in frs)

    # ── shift report ────────────────────────────────────────────────────
    rep = await api(client, "GET", "/api/reports/shift", h, params={
        "line_id": p.line["id"], "productive_day": start.date().isoformat(), "shift_id": shift["id"]})
    assert close(rep["oee"]["oee"], X["oee"]) and rep["shift_name"] == C["shift"]["name"]
    assert [r["reason_code"] for r in rep["pareto"]] == [i[0] for i in exp]
    assert len(rep["downtime"]) == 6
    assert rep["production"][0]["good"] == C["production"]["good"]
    assert close(rep["production"][0]["oee"], X["oee"])

    # re-running the detector later changes nothing (idempotent: micro-stops counted once)
    await run_detector(mid, t.id, end + timedelta(hours=2))
    o2 = await api(client, "GET", "/api/oee", h, params={"scope": "machine", "id": mid, **params})
    for k in ("availability", "performance", "quality", "oee", "micro_stop_count", "micro_stop_loss_s", "adot_s"):
        assert close(o2[k], o[k]), k


def ts_local(iso: str) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(C["plant"]["timezone"])).strftime("%H:%M:%S")
