"""Golden scenario D (docs/scenarios/scenario_d_line_multiday.json) — oracle + conformance.

1. **Oracle** (always runs): recomputes every `expected` value of scenario D from its inputs (calendar,
   events, micro-stops, data gaps, hourly production, production runs, PRI of the bottleneck), following
   docs/kpis.md §1 + "Decisões Sprint 3" + "Esclarecimentos Sprint 3" — without importing server code.
2. **Conformance** (runs when server/src/services/oee is importable): feeds the same inputs, as in-memory
   `MachineData`, through the server's pure OEE engine (calendar → windows → components → kpi.oee/pareto)
   and checks it gives the same numbers. Known divergences between the engine and the spec are marked
   `xfail(strict=False)` with the clarification code, so they show up in the report until fixed.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
D = json.loads((HERE / "scenario_d_line_multiday.json").read_text(encoding="utf-8"))
X = D["expected"]
TZ = ZoneInfo(D["plant"]["timezone"])
UTC = timezone.utc
TOL = 1e-6


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def loc(s: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(s, fmt).replace(tzinfo=TZ).astimezone(UTC)


def close(a, b, tol=TOL) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


def mins(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


# ════════════════════════════════════════════════════════════════════════════
# Oracle
# ════════════════════════════════════════════════════════════════════════════

def oracle_instances(cal: dict, first: date, last: date) -> list[dict]:
    """domain.md §4.1 + D9: shift belongs to the productive day it starts on; holiday removes the day's
    instances (or only `shift_id`'s); extra_shift adds one with the breaks of `shift_id`."""
    by_id = {s["id"]: s for s in cal["shifts"]}
    exc = defaultdict(list)
    for e in cal["exceptions"]:
        exc[date.fromisoformat(e["date"])].append(e)

    def make(day, sdef, start_hhmm, end_hhmm, extra):
        h0, m0 = map(int, start_hhmm.split(":"))
        h1, m1 = map(int, end_hhmm.split(":"))
        start = datetime(day.year, day.month, day.day, h0, m0, tzinfo=TZ)
        end_day = day + timedelta(days=1) if (h1, m1) <= (h0, m0) else day
        end = datetime(end_day.year, end_day.month, end_day.day, h1, m1, tzinfo=TZ)
        brk = []
        for b in (sdef or {}).get("breaks", []):
            bh, bm = map(int, b["start_local"].split(":"))
            eh, em = map(int, b["end_local"].split(":"))
            for d in (day, day + timedelta(days=1)):
                bs = datetime(d.year, d.month, d.day, bh, bm, tzinfo=TZ)
                if start <= bs < end:
                    be = bs.replace(hour=eh, minute=em)
                    if be <= bs:
                        be += timedelta(days=1)
                    brk.append((bs.astimezone(UTC), min(be, end).astimezone(UTC), b["reason_code"]))
                    break
        return {"shift_id": sdef["id"] if sdef else None, "name": sdef["name"] if sdef else "Turno extra",
                "extra": extra, "productive_day": day.isoformat(),
                "start": start.astimezone(UTC), "end": end.astimezone(UTC), "breaks": brk}

    out = []
    d = first
    while d <= last:
        es = exc.get(d, [])
        all_off = any(e["kind"] == "holiday" and not e.get("shift_id") for e in es)
        off = {e["shift_id"] for e in es if e["kind"] == "holiday" and e.get("shift_id")}
        if not all_off:
            for s in cal["shifts"]:
                if d.weekday() in s["weekdays"] and s["id"] not in off:
                    out.append(make(d, s, s["start_local"], s["end_local"], False))
        for e in es:
            if e["kind"] == "extra_shift":
                out.append(make(d, by_id.get(e.get("shift_id")), e["start_local"], e["end_local"], True))
        d += timedelta(days=1)
    return sorted(out, key=lambda i: i["start"])


Q = D["query"]
QS, QE = ts(Q["start_utc"]), ts(Q["stop_utc"])
INST = [i for i in oracle_instances(D["calendar"], QS.astimezone(TZ).date() - timedelta(days=1),
                                    (QE - timedelta(seconds=1)).astimezone(TZ).date())
        if i["end"] > QS and i["start"] < QE]
PRI = D["line_pri_s"]


def _inst_of(t: datetime):
    return next((i for i in INST if i["start"] <= t < i["end"]), None)


def _run_product(t: datetime):
    for r in D["production_runs"]:
        if ts(r["from_utc"]) <= t < ts(r["to_utc"]):
            return r["product"]
    return None


def oracle_components(insts: list[dict], pri: dict) -> dict:
    c = defaultdict(float)
    losses = defaultdict(float)
    for i in insts:
        a, b = i["start"], i["end"]
        c["pot"] += mins(a, b)
        brk = [(x, y) for x, y, _ in i["breaks"]]
        c["pdot"] += sum(mins(x, y) for x, y in brk)
        c["ao_excl"] += sum(mins(x, y) for x, y, rc in i["breaks"] if rc in ("PLN-01", "PLN-02", "PLN-04", "PLN-05"))
        for e in D["downtime_events"]:
            s, t = ts(e["from_utc"]), ts(e["to_utc"])
            s, t = max(s, a), min(t, b)
            if t <= s:
                continue
            d = mins(s, t)
            reason = e["operator_reason_code"] or "UNK-01"            # D2: no reason, no suggestion → UNK-01
            if reason.startswith("PLN-"):                                # S3-4: reclassified planned stop → PDOT
                c["pdot"] += d
                if reason in ("PLN-01", "PLN-02", "PLN-04", "PLN-05"):
                    c["ao_excl"] += d
            else:
                c["adot"] += d
                losses[reason] += d
                if reason.startswith("EQP-") or reason in ("PLN-03", "MNT-02"):
                    c["deq"] += d
        for g in D["data_gaps"]:
            s, t = max(ts(g["from_utc"]), a), min(ts(g["to_utc"]), b)
            if t > s:
                c["nodata"] += mins(s, t)
        for m in D["micro_stops"]:
            if a <= ts(m["from_utc"]) < b:
                c["micro_n"] += 1
                c["micro"] += m["duration_s"] / 60
        for r in D["production_by_hour"]:
            h = ts(r["hour_utc"])
            if not (a <= h < b):            # S3-7: production outside the shift instances is not OEE production
                continue
            p = _run_product(h)
            pq = r["good"] + r["scrap"]
            c["pq"] += pq
            c["gq"] += r["good"]
            c["ip"] += pri[p] * pq / 60
            c["ig"] += pri[p] * r["good"] / 60
    pbt = c["pot"] - c["pdot"] - c["nodata"]
    apt = pbt - c["adot"]
    req = c["pot"] - c["ao_excl"] - c["nodata"]
    return {"pot_min": c["pot"], "pdot_min": c["pdot"], "nodata_min": c["nodata"], "pbt_min": pbt,
            "adot_min": c["adot"], "apt_min": apt,
            "data_coverage": (c["pot"] - c["nodata"]) / c["pot"] if c["pot"] else None,
            "produced": c["pq"], "good": c["gq"], "scrap": c["pq"] - c["gq"],
            "ideal_produced_min": c["ip"], "net_productive_min": c["ig"],
            "availability": apt / pbt if pbt else None, "performance": c["ip"] / apt if apt else None,
            "quality": c["ig"] / c["ip"] if c["ip"] else None, "oee": c["ig"] / pbt if pbt else None,
            "micro_stop_count": int(c["micro_n"]), "micro_stop_min": c["micro"],
            "speed_loss_min": apt - c["ip"] - c["micro"], "quality_loss_min": c["ip"] - c["ig"],
            "losses_by_reason_min": dict(losses),
            "ao": (req - c["deq"]) / req if req else None, "required_min": req, "d_eq_min": c["deq"]}


KEYS = ("pot_min", "pdot_min", "nodata_min", "pbt_min", "adot_min", "apt_min", "data_coverage", "produced", "good",
        "scrap", "ideal_produced_min", "net_productive_min", "availability", "performance", "quality", "oee",
        "micro_stop_count", "micro_stop_min", "speed_loss_min", "quality_loss_min")


def _check(got: dict, exp: dict, keys=KEYS, ctx=""):
    for k in keys:
        assert close(got[k], exp[k]), (ctx, k, got[k], exp[k])


def test_d_calendar_instances():
    """Holiday, shift-only holiday, extra shift and the Sunday night shift that ends on the holiday."""
    got = [(i["shift_id"], i["extra"], i["productive_day"], i["start"], i["end"],
            [(a, b, rc) for a, b, rc in i["breaks"]]) for i in INST]
    exp = [(e["shift_id"], e["extra"], e["productive_day"], ts(e["start_utc"]), ts(e["end_utc"]),
            [(ts(a), ts(b), rc) for a, b, rc in e["breaks_utc"]]) for e in X["instances"]]
    assert got == exp
    # the C shift of Sunday 11/10 runs into the holiday morning and still belongs to Sunday (C19)
    s2 = next(i for i in INST if i["productive_day"] == "2026-10-11")
    assert s2["end"].astimezone(TZ).date().isoformat() == "2026-10-12"
    assert not [i for i in INST if i["productive_day"] == "2026-10-12"]


def test_d_inputs_consistent():
    # every stop/micro-stop lies inside one shift instance, outside breaks; nothing overlaps
    stops = [(ts(e["from_utc"]), ts(e["to_utc"])) for e in D["downtime_events"]]
    stops += [(ts(m["from_utc"]), ts(m["from_utc"]) + timedelta(seconds=m["duration_s"])) for m in D["micro_stops"]]
    stops += [(ts(g["from_utc"]), ts(g["to_utc"])) for g in D["data_gaps"]]
    stops.sort()
    for (a, b), (c, d) in zip(stops, stops[1:]):
        assert b <= c, (a, b, c, d)
    for a, b in stops:
        i = _inst_of(a)
        assert i is not None and b <= i["end"], (a, b)
        assert not any(x < b and a < y for x, y, _ in i["breaks"]), (a, b)
    thr = D["machines"]["Transportador de Saída"]["micro_stop_threshold_s"]
    assert all(m["duration_s"] < thr for m in D["micro_stops"])
    assert all(e["duration_s"] >= thr for e in D["downtime_events"])
    # hourly production adds up to the per-(shift, product) totals, and per hour P ≤ 1 (plausible counts)
    tot = defaultdict(lambda: [0, 0])
    for r in D["production_by_hour"]:
        h = ts(r["hour_utc"])
        i = _inst_of(h)
        if i is None:
            assert r["shift"] is None
            continue
        p = _run_product(h)
        assert p == r["product"]
        tot[(r["shift"], p)][0] += r["good"] + r["scrap"]
        tot[(r["shift"], p)][1] += r["good"]
        assert PRI[p] * (r["good"] + r["scrap"]) / 60 <= r["running_min"] + 1e-9, r
    assert {k: tuple(v) for k, v in tot.items()} == {
        (t["shift"], t["product"]): (t["produced"], t["good"]) for t in D["production_totals"]}
    # gap: last reading + machine_offline_timeout_s (60 s) = start of "sem dados" (C11)
    for g in D["data_gaps"]:
        assert ts(g["last_reading_utc"]) + timedelta(seconds=60) == ts(g["from_utc"])


def test_d_signals_match_events():
    """The run_state/fault_state transitions reproduce stops, micro-stops, breaks and the gap."""
    tr = [s for s in D["signals_output_machine"]["transitions"] if not s.get("last_reading_before_gap")
          and not s.get("after_gap")]
    zeros, cur = [], None
    for s in tr:
        t = ts(s["ts"])
        if s["run_state"] == 0 and cur is None:
            cur = (t, s["fault_state"])
        elif s["run_state"] == 1 and cur is not None:
            zeros.append((cur[0], t, cur[1]))
            cur = None
    expect = [(ts(e["from_utc"]), ts(e["to_utc"]), e["fault_code"] or 0) for e in D["downtime_events"]]
    expect += [(ts(m["from_utc"]), ts(m["from_utc"]) + timedelta(seconds=m["duration_s"]), 0) for m in D["micro_stops"]]
    expect += [(a, b, 0) for i in INST for a, b, _ in i["breaks"]]
    inside = [z for z in zeros if _inst_of(z[0]) is not None]
    assert sorted(inside) == sorted(expect)
    fmap = D["machines"]["Transportador de Saída"]["fault_code_map"]
    for e in D["downtime_events"]:
        if e["fault_code"]:
            assert fmap[str(e["fault_code"])] == e["operator_reason_code"]


def test_d_per_shift():
    for i, e in zip(INST, X["shifts"]):
        got = oracle_components([i], PRI)
        _check(got, e, ctx=e["key"])
        assert got["losses_by_reason_min"] == e["losses_by_reason_min"], e["key"]
        ao = e["availability_operational"]
        assert close(got["ao"], ao["ao"]) and close(got["required_min"], ao["required_min"]) \
            and close(got["d_eq_min"], ao["d_eq_min"])
        assert e["label"] == f"{i['name']} {i['productive_day'][8:10]}/{i['productive_day'][5:7]}"
    s4 = X["shifts"][3]
    assert s4["data_coverage"] < 0.95          # UI: "dados incompletos"


def test_d_per_productive_day_and_teep():
    for e in X["days"]:
        insts = [i for i in INST if i["productive_day"] == e["productive_day"]]
        got = oracle_components(insts, PRI)
        _check(got, e, ctx=e["productive_day"])
        assert e["calendar_min"] == 1440
        assert close(got["pbt_min"] / 1440, e["loading"])
        assert close(got["net_productive_min"] / 1440, e["teep"])           # S3-1: holiday TEEP = 0, not null
        if not insts:
            assert e["oee"] is None and e["reason"] == "no_planned_time" and e["teep"] == 0 and e["loading"] == 0
        else:
            assert close(e["teep"], e["oee"] * e["loading"])


def test_d_period_is_sum_of_components_not_mean():
    P = X["period"]
    got = oracle_components(INST, PRI)
    _check(got, P, ctx="period")
    days = [oracle_components([i for i in INST if i["productive_day"] == d], PRI) for d in Q["productive_days"]]
    for k in ("pot_min", "pbt_min", "apt_min", "ideal_produced_min", "net_productive_min", "produced", "good"):
        assert close(sum(d[k] for d in days), P[k]), k
    cal = 1440 * len(Q["productive_days"])
    assert P["calendar_min"] == cal == mins(QS, QE)
    assert close(P["loading"], P["pbt_min"] / cal) and close(P["teep"], P["net_productive_min"] / cal)
    assert close(P["teep"], P["oee"] * P["loading"])
    w = X["wrong_answers"]
    assert close(w["mean_of_shift_oee"]["value"], sum(s["oee"] for s in X["shifts"]) / 4)
    assert not close(w["mean_of_shift_oee"]["value"], P["oee"])
    assert not close(w["mean_of_day_oee"]["value"], P["oee"])
    ao = P["availability_operational"]
    assert close(got["ao"], ao["ao"]) and close(got["required_min"], ao["required_min"])


def test_d_quality_is_pri_weighted_with_mix():
    s3 = X["shifts"][2]
    by = s3["by_product"]
    ip = sum(PRI[p] * v["produced"] for p, v in by.items()) / 60
    ig = sum(PRI[p] * v["good"] for p, v in by.items()) / 60
    assert close(ig / ip, s3["quality"])
    w = X["wrong_answers"]["unweighted_quality_s3"]["value"]
    assert close(w, s3["good"] / s3["produced"]) and not close(w, s3["quality"])
    assert close(s3["availability"] * s3["performance"] * s3["quality"], s3["oee"])


def test_d_loss_pareto():
    P = X["period"]
    items = dict(P["losses_by_reason_min"])
    items.update(speed_loss=P["speed_loss_min"], micro_stops=P["micro_stop_min"], quality_loss=P["quality_loss_min"])
    tot = sum(items.values())
    lp = P["loss_pareto"]
    assert close(tot, lp["total_loss_min"]) and close(tot, P["pbt_min"] - P["net_productive_min"])
    ranked = sorted(items.items(), key=lambda kv: (-kv[1], kv[0]))
    assert len({v for _, v in ranked}) == len(ranked)          # no ties: the order is unambiguous
    acc = 0.0
    assert [k for k, _ in ranked] == [r[0] for r in lp["items"]]
    for (k, v), (_, e, pct, cum) in zip(ranked, lp["items"]):
        acc += v
        assert close(v, e) and abs(v / tot - pct) <= 5e-7 and abs(acc / tot - cum) <= 5e-7
    cat = defaultdict(float)
    for k, v in items.items():
        cat[k.split("-")[0] if "-" in k else k] += v
    assert [[k, v] for k, v in sorted(cat.items(), key=lambda kv: (-kv[1], kv[0]))] == P["loss_pareto_by_category"]
    # the data gap is neither a loss nor productive time
    assert "nodata" not in items and close(sum(e for _, e, _, _ in lp["items"]), P["pbt_min"] - P["net_productive_min"])


def test_d_day_grouping_productive_vs_calendar():
    prod = defaultdict(lambda: {"good": 0, "scrap": 0})
    cal = defaultdict(lambda: {"good": 0, "scrap": 0})
    for r in D["production_by_hour"]:
        h = ts(r["hour_utc"])
        i = _inst_of(h)
        if i is not None:
            prod[i["productive_day"]]["good"] += r["good"]
            prod[i["productive_day"]]["scrap"] += r["scrap"]
        k = h.astimezone(TZ).date().isoformat()
        cal[k]["good"] += r["good"]
        cal[k]["scrap"] += r["scrap"]
    for v in list(prod.values()) + list(cal.values()):
        v["total"] = v["good"] + v["scrap"]
    assert dict(prod) == X["production_by_productive_day"]
    assert dict(cal) == X["wrong_answers"]["by_local_calendar_day"]
    assert X["production_by_productive_day"]["2026-10-11"]["total"] == 450          # whole C shift on Sunday
    assert "2026-10-12" not in X["production_by_productive_day"]                    # holiday: nothing in POT
    off = X["produced_outside_schedule"]
    assert cal["2026-10-12"]["total"] - sum(r["good"] + r["scrap"] for r in D["production_by_hour"]
                                            if r["shift"] == "S2" and r["hour_local"] >= "2026-10-12") == off["total"]


def test_d_wrong_answers_differ():
    P = X["period"]
    w = X["wrong_answers"]
    out = oracle_components(INST, {p: v["ideal_cycle_time_s"] for p, v in
                                   ((m["product"], m) for m in D["machines"]["Transportador de Saída"]["products"])})
    assert close(out["oee"], X["machine_scope_output"]["oee"]) and close(out["oee"], w["output_machine_pri"]["oee"])
    assert not close(out["oee"], P["oee"])
    robot = sum(e["duration_s"] for e in D["bottleneck_events"]) / 60
    assert close(w["bottleneck_downtime_added"]["adot_min"], P["adot_min"] + robot)
    s4 = X["shifts"][3]
    assert close(w["gap_as_running"]["s4_pbt_min"], s4["pbt_min"] + s4["nodata_min"])
    off_ig = X["produced_outside_schedule"]["good"] * PRI["SUP-100"] / 60
    assert close(w["off_schedule_included"]["period_oee"], (P["net_productive_min"] + off_ig) / P["pbt_min"])
    assert not close(w["off_schedule_included"]["period_oee"], P["oee"])


def test_d_variant_declared_gap_stop():
    """D5: a manual stop declared over the gap counts as ADOT and as covered time."""
    v = X["variants"]["declared_gap_stop"]
    s4 = X["shifts"][3]
    g = sum(mins(ts(x["from_utc"]), ts(x["to_utc"])) for x in D["data_gaps"])
    assert close(v["s4"]["pbt_min"], s4["pbt_min"] + g) and close(v["s4"]["adot_min"], s4["adot_min"] + g)
    assert close(v["s4"]["apt_min"], s4["apt_min"]) and v["s4"]["data_coverage"] == 1.0
    assert close(v["s4"]["oee"], s4["net_productive_min"] / v["s4"]["pbt_min"])
    assert close(v["period"]["oee"], X["period"]["net_productive_min"] / (X["period"]["pbt_min"] + g))


def test_readme_lists_d():
    text = (HERE / "README.md").read_text(encoding="utf-8")
    assert "scenario_d_line_multiday.json" in text and "Cenário D" in text
    # the README's period numbers are the JSON's (6 decimals, decimal comma)
    for k in ("availability", "performance", "quality", "oee", "teep"):
        assert f"{X['period'][k]:.6f}".replace(".", ",") in text, k
    for s in X["shifts"]:
        assert f"{s['oee']:.6f}".replace(".", ",") in text, s["key"]


# ════════════════════════════════════════════════════════════════════════════
# Conformance — the server's OEE engine must give the same numbers
# ════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def eng():
    server = ROOT / "server"
    if not (server / "src" / "services" / "oee" / "engine.py").exists():
        pytest.skip("server OEE engine not present")
    if str(server) not in sys.path:
        sys.path.insert(0, str(server))
    try:
        from src.services.oee import calendar as cal_mod, engine, kpi   # noqa: WPS433
    except Exception as exc:  # pragma: no cover — missing deps outside the server venv
        pytest.skip(f"server OEE engine not importable: {exc}")
    return cal_mod, engine, kpi


def _machine_data(eng):
    cal_mod, engine, _ = eng
    cdef = D["calendar"]
    shifts = [cal_mod.ShiftDef(s["id"], s["name"], s["start_local"], s["end_local"], tuple(s["weekdays"]),
                               tuple(s["breaks"])) for s in cdef["shifts"]]
    excs = [cal_mod.ExceptionDef(date.fromisoformat(e["date"]), e["kind"], e.get("shift_id"), e.get("start_local"),
                                 e.get("end_local"), e.get("reason_code"), e.get("note")) for e in cdef["exceptions"]]
    cal = cal_mod.Calendar(TZ, shifts, excs)
    out_m = D["machines"]["Transportador de Saída"]
    info = engine.MachineInfo("m-out", "t-d", "Transportador de Saída", "l-d", "p-d", TZ, "running",
                              out_m["micro_stop_threshold_s"], 30.0, out_m["fault_code_map"])
    events = [engine.EventLite(f"ev{k}", ts(e["from_utc"]), ts(e["to_utc"]), e["operator_reason_code"] or "UNK-01",
                               bool(e["planned"])) for k, e in enumerate(D["downtime_events"])]
    gaps = sorted((ts(g["from_utc"]), ts(g["to_utc"])) for g in D["data_gaps"])
    spans, cur = [], QS - timedelta(days=1)
    for a, b in gaps:
        spans.append((cur, a))
        cur = b
    spans.append((cur, QE + timedelta(days=1)))
    micro = [(ts(m["from_utc"]), 1, float(m["duration_s"]), 0) for m in D["micro_stops"]]
    production = defaultdict(lambda: {"good": 0.0, "scrap": 0.0, "total": 0.0})
    for r in D["production_by_hour"]:
        p = production[ts(r["hour_utc"])]
        p["good"] += r["good"]
        p["scrap"] += r["scrap"]
        p["total"] += r["good"] + r["scrap"]
    runs = [(r["product"], ts(r["from_utc"]), ts(r["to_utc"])) for r in D["production_runs"]]
    out_pri = engine.ProductPri(pri={m["product"]: float(m["ideal_cycle_time_s"]) for m in out_m["products"]},
                                default_product="SUP-100", fallback_pri=30.0)
    md = engine.MachineData(info, cal, events, spans, micro, dict(production), runs, out_pri)
    # line: output machine's data with the bottleneck's PRI (service.load_units)
    line_pri = engine.ProductPri(pri={**out_pri.pri, **{k: float(v) for k, v in PRI.items()}},
                                 default_product="SUP-100", fallback_pri=float(PRI["SUP-100"]))
    return cal, md, line_pri


NOW = QE + timedelta(days=1)


def _engine_row(kpi, c) -> dict:
    r = kpi.oee(c)
    return {"pot_min": c.pot_s / 60, "pdot_min": c.pdot_s / 60, "nodata_min": c.nodata_s / 60,
            "pbt_min": r["pbt_s"] / 60, "adot_min": r["adot_s"] / 60, "apt_min": r["apt_s"] / 60,
            "data_coverage": r["data_coverage"], "produced": c.produced, "good": c.good, "scrap": c.scrap,
            "ideal_produced_min": c.ideal_produced_s / 60, "net_productive_min": c.ideal_good_s / 60,
            "availability": r["availability"], "performance": r["performance"], "quality": r["quality"],
            "oee": r["oee"], "micro_stop_count": c.micro_count, "micro_stop_min": c.micro_s / 60,
            "speed_loss_min": c.speed_loss_s / 60, "quality_loss_min": c.quality_loss_s / 60,
            "loading": r["loading"], "teep": r["teep"], "ao": r["availability_operational"], "reason": r["reason"],
            "losses": {k: v[0] / 60 for k, v in c.losses.items()}}


def test_engine_shift_windows(eng):
    _, engine, kpi = eng
    cal, md, line_pri = _machine_data(eng)
    ws = engine.windows(cal, TZ, QS, QE, "shift")
    assert [w.label for w in ws] == [s["label"] for s in X["shifts"]]
    for w, e in zip(ws, X["shifts"]):
        got = _engine_row(kpi, engine.window_components(md, w, NOW, line_pri))
        _check(got, e, ctx=e["key"])
        assert got["losses"] == pytest.approx(e["losses_by_reason_min"])
        assert close(got["ao"], e["availability_operational"]["ao"])


def test_engine_day_windows(eng):
    _, engine, kpi = eng
    cal, md, line_pri = _machine_data(eng)
    ws = engine.windows(cal, TZ, QS, QE, "day")
    assert [w.label for w in ws] == Q["productive_days"]
    for w, e in zip(ws, X["days"]):
        got = _engine_row(kpi, engine.window_components(md, w, NOW, line_pri))
        if e["pot_min"] == 0:          # the holiday: checked in test_engine_holiday_teep
            assert got["oee"] is None and got["reason"] == "no_planned_time" and got["loading"] == 0
            continue
        _check(got, e, ctx=e["productive_day"])
        assert close(got["loading"], e["loading"]) and close(got["teep"], e["teep"])


def test_engine_period_sum_of_days_and_pareto(eng):
    _, engine, kpi = eng
    cal, md, line_pri = _machine_data(eng)
    c = kpi.Components()
    for w in engine.windows(cal, TZ, QS, QE, "day"):
        c.add(engine.window_components(md, w, NOW, line_pri))
    got = _engine_row(kpi, c)
    _check(got, X["period"], ctx="period")
    assert close(got["teep"], X["period"]["teep"]) and close(got["loading"], X["period"]["loading"])
    meta = {code: (code.split("-")[0], code, code.startswith("PLN-")) for code in got["losses"]}
    rows = kpi.pareto(c, meta)
    lp = X["period"]["loss_pareto"]
    assert [r["reason_code"] for r in rows] == [i[0] for i in lp["items"]]
    for r, (_, v, pct, cum) in zip(rows, lp["items"]):
        assert close(r["duration_s"] / 60, v) and abs(r["pct"] - pct) <= 5e-7 and abs(r["cumulative_pct"] - cum) <= 5e-7
    cats = kpi.pareto(c, meta, by="category")
    assert [[r["category"], r["duration_s"] / 60] for r in cats] == \
        [[k, pytest.approx(v)] for k, v in X["period"]["loss_pareto_by_category"]]


def test_engine_machine_scope_uses_own_pri(eng):
    _, engine, kpi = eng
    cal, md, _ = _machine_data(eng)
    c = kpi.Components()
    for w in engine.windows(cal, TZ, QS, QE, "day"):
        c.add(engine.window_components(md, w, NOW))
    got = _engine_row(kpi, c)
    _check(got, X["machine_scope_output"], keys=("pbt_min", "apt_min", "ideal_produced_min", "net_productive_min",
                                                 "availability", "performance", "quality", "oee"))


def test_engine_holiday_teep_is_zero(eng):
    _, engine, kpi = eng
    cal, md, line_pri = _machine_data(eng)
    w = next(w for w in engine.windows(cal, TZ, QS, QE, "day") if w.label == "2026-10-12")
    r = kpi.oee(engine.window_components(md, w, NOW, line_pri))
    assert r["oee"] is None and r["loading"] == 0 and r["teep"] == 0


def test_engine_interval_total_ignores_off_schedule_production(eng):
    _, engine, kpi = eng
    cal, md, line_pri = _machine_data(eng)
    c = engine.components(md, QS, QE, NOW, line_pri)
    got = _engine_row(kpi, c)
    _check(got, X["period"], ctx="interval total")
    out = kpi.oee(c)["produced_outside_schedule"]
    exp = X["produced_outside_schedule"]
    assert {k: out[k] for k in ("total", "good", "scrap")} == {k: exp[k] for k in ("total", "good", "scrap")}
