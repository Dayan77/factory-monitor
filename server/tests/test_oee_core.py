"""Pure OEE engine: kpis.md §6 worked examples through kpi.py, calendar expansion (midnight shift,
exceptions), detection rules (C22–C24, data gaps, stop → error), segmentation and micro-stops."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.services.oee import kpi
from src.services.oee.calendar import Calendar, ExceptionDef, ShiftDef, shifts_overlap
from src.services.oee.detection import (Params, Reading, SignalState, classify_cycle_gaps, detect, micro_pieces,
                                        segments, suggested_reason)
from src.services.oee.intervals import intersect, normalize, subtract, total_s

DOC = Path(__file__).resolve().parents[2] / "docs" / "kpis.md"
CASES = {c["id"]: c for c in json.loads(re.search(r"```json kpi-test-cases\n(.*?)\n```",
                                                    DOC.read_text(encoding="utf-8"), re.S).group(1))}
TZ = ZoneInfo("America/Manaus")
UTC = timezone.utc


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(b))


def Z(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


# ── kpis.md §6 through the engine ───────────────────────────────────────────

def _shift_components(s) -> kpi.Components:
    c = kpi.Components(pot_s=s["pot_min"] * 60, pdot_s=s["planned_downtime_min"] * 60,
                       adot_s=sum(s["unplanned_downtime_min"]) * 60)
    for p in s["products"]:
        c.produced += p["produced"]
        c.good += p["good"]
        c.scrap += p["produced"] - p["good"]
        c.ideal_produced_s += p["ideal_cycle_time_s"] * p["produced"]
        c.ideal_good_s += p["ideal_cycle_time_s"] * p["good"]
    return c


def test_spec_oee_single_shift():
    case = CASES["oee_single_shift"]
    r = kpi.oee(_shift_components(case["input"]))
    e = case["expected"]
    assert close(r["pbt_s"], e["pbt_min"] * 60) and close(r["apt_s"], e["apt_min"] * 60)
    for k in ("availability", "performance", "quality", "oee"):
        assert close(r[k], e[k]), k


def test_spec_oee_aggregate_sums_components_never_averages():
    case = CASES["oee_two_shifts_mix"]
    parts = [_shift_components(s) for s in case["input"]["shifts"]]
    e = case["expected"]
    assert [round(kpi.oee(p)["oee"], 6) for p in parts] == e["shift_oee"]
    b = kpi.oee(parts[1])
    assert close(b["performance"], e["shift_b_performance"]) and close(b["quality"], e["shift_b_quality"])
    day = kpi.oee(kpi.total(parts))
    for k in ("availability", "performance", "quality", "oee"):
        assert close(day[k], e[k]), k
    assert not close(day["oee"], e["wrong_mean_of_shift_oee"])


def test_spec_teep():
    e = CASES["teep_day"]["expected"]
    parts = [_shift_components(s) for s in CASES["oee_two_shifts_mix"]["input"]["shifts"]]
    c = kpi.total(parts)
    c.calendar_s = 1440 * 60
    r = kpi.oee(c)
    assert close(r["loading"], e["loading"]) and close(r["teep"], e["teep"])


def test_spec_micro_stops_from_cycle_gaps():
    i, e = CASES["micro_stops_from_cycle_gaps"]["input"], CASES["micro_stops_from_cycle_gaps"]["expected"]
    g = classify_cycle_gaps(i["ideal_cycle_time_s"], i["micro_stop_threshold_s"], i["cycle_gaps_s"])
    assert (g.speed_loss_s, g.micro_stop_count, g.micro_stop_s, g.downtime_events_s) == \
        (e["speed_loss_s"], e["micro_stop_count"], e["micro_stop_s"], e["downtime_events_s"])


def test_cycle_gap_detection_matches_e4():
    """Without run_state the detector finds the same stops from counter timestamps (rule 1)."""
    t0 = Z("2026-10-05T10:00:00")
    cycles, t = [t0], t0
    for g in (31, 95, 40, 400):
        t += timedelta(seconds=g)
        cycles.append(t)
    rd = [Reading(c, "cycle", 1) for c in cycles]
    hb = t0 + timedelta(seconds=10)
    while hb < t + timedelta(seconds=20):            # counter heartbeats (no new cycle): data present
        rd.append(Reading(hb, "data", 0))
        hb += timedelta(seconds=10)
    d = detect(SignalState(), rd, t + timedelta(seconds=20), Params(mode="cycle", ideal_cycle_s=30))
    durs = [s.duration_s for s in d.stops]
    assert durs == [65.0, 370.0]
    assert all(s.source == "auto_cycle_gap" for s in d.stops)


def test_spec_cadence_and_pareto():
    i, e = CASES["cadence"]["input"], CASES["cadence"]["expected"]
    c = kpi.Components(pot_s=i["pbt_min"] * 60, adot_s=(i["pbt_min"] - i["apt_min"]) * 60, produced=i["produced"])
    c.ideal_produced_s = i["produced"] * i["ideal_cycle_time_s"]
    r = kpi.oee(c)
    assert close(r["cadence_planned_per_hour"], e["parts_per_hour_planned"])
    assert close(r["cadence_running_per_hour"], e["parts_per_hour_running"])

    i, e = CASES["loss_pareto"]["input"], CASES["loss_pareto"]["expected"]
    c = kpi.Components(pot_s=i["pbt_min"] * 60, adot_s=(i["pbt_min"] - i["apt_min"]) * 60,
                       micro_s=i["micro_stop_min"] * 60)
    c.ideal_produced_s = i["ideal_time_produced_min"] * 60
    c.ideal_good_s = c.ideal_produced_s - i["ideal_cycle_time_s"] * i["scrap"]
    c.losses = {k: [v * 60, 1] for k, v in i["downtime_by_reason_min"].items()}
    rows = kpi.pareto(c, {})
    assert close(sum(r["duration_s"] for r in rows), e["total_loss_min"] * 60)
    assert [r["reason_code"] for r in rows] == [x[0] for x in e["items"]]
    for r, (_, v, pct, cum) in zip(rows, e["items"]):
        assert close(r["duration_s"], v * 60) and abs(r["pct"] - pct) < 5e-7 and abs(r["cumulative_pct"] - cum) < 5e-7


def test_spec_reliability_cases():
    i = CASES["mtbf"]["input"]
    r = kpi.reliability(i["operating_hours"] * 3600, [(f["severity"], None) for f in i["failures"]])
    assert r["failures"] == 4 and r["incipient"] == 1 and close(r["mtbf_h"], 180)
    r0 = kpi.reliability(720 * 3600, [])
    assert r0["mtbf_h"] is None and r0["reason"] == "no_failures" and close(r0["operating_h"], 720)
    d = CASES["mttr"]["input"]["restore_durations_min"]
    r = kpi.reliability(720 * 3600, [("critical", m * 60) for m in d])
    assert close(r["mttr_s"] / 60, CASES["mttr"]["expected"]["mttr_min"])
    r = kpi.reliability(720 * 3600, [("critical", 3600.0)] * 4)
    assert close(r["availability_inherent"], CASES["availability_inherent"]["expected"]["ai"])
    i = CASES["availability_operational"]["input"]
    c = kpi.Components(pot_s=i["pot_h"] * 3600, ao_excluded_s=i["excluded_h"] * 3600,
                       ao_equipment_s=(i["failure_downtime_h"] + i["maintenance_downtime_h"]) * 3600)
    assert close(kpi.availability_operational(c), CASES["availability_operational"]["expected"]["ao"])


def test_undefined_ratios_are_null_not_zero():
    r = kpi.oee(kpi.Components())
    assert r["availability"] is None and r["oee"] is None and r["reason"] == "no_planned_time"
    r = kpi.oee(kpi.Components(pot_s=3600, pri_missing=True, produced=10, good=10))
    assert r["performance"] is None and r["oee"] is None and r["reason"] == "insufficient_data"
    r = kpi.oee(kpi.Components(pot_s=3600))          # planned, nothing produced
    assert r["quality"] is None and r["oee"] == 0.0 and r["reason"] == "no_production"
    r = kpi.oee(kpi.Components(pot_s=3600, produced=200, good=200, ideal_produced_s=4000, ideal_good_s=4000))
    assert r["performance"] > 1 and r["ideal_cycle_time_suspect"]          # not truncated


# ── intervals / calendar ────────────────────────────────────────────────────

def test_interval_ops():
    a = normalize([(Z("2026-01-01T00:00"), Z("2026-01-01T02:00")), (Z("2026-01-01T01:00"), Z("2026-01-01T03:00"))])
    assert a == [(Z("2026-01-01T00:00"), Z("2026-01-01T03:00"))]
    b = [(Z("2026-01-01T01:00"), Z("2026-01-01T01:30")), (Z("2026-01-01T02:00"), Z("2026-01-01T04:00"))]
    assert total_s(intersect(a, b)) == 5400
    assert total_s(subtract(a, b)) == 3 * 3600 - 5400


def _manaus_cal(exceptions=()):
    shifts = [
        ShiftDef("A", "Turno A", "06:00", "14:00", (0, 1, 2, 3, 4, 5),
                 ({"start_local": "09:00", "end_local": "09:10"}, {"start_local": "11:00", "end_local": "12:00"})),
        ShiftDef("B", "Turno B", "14:00", "22:00", (0, 1, 2, 3, 4, 5)),
        ShiftDef("C", "Turno C", "22:00", "06:00", (0, 1, 2, 3, 4, 5),
                 ({"start_local": "02:00", "end_local": "03:00", "reason_code": "PLN-01"},)),
    ]
    return Calendar(TZ, shifts, exceptions)


def test_shift_crossing_midnight_belongs_to_start_day():
    cal = _manaus_cal()
    # Monday 2026-10-05 22:00 local → Tuesday 06:00 local = productive day Monday (C19)
    insts = cal.day_instances(date(2026, 10, 5))
    c = [i for i in insts if i.shift_id == "C"][0]
    assert c.start == Z("2026-10-06T02:00") and c.end == Z("2026-10-06T10:00")
    assert c.productive_day == date(2026, 10, 5)
    # break 02:00 local falls on the next calendar day, inside the instance
    assert [(b.start, b.end) for b in c.breaks] == [(Z("2026-10-06T06:00"), Z("2026-10-06T07:00"))]
    inst = cal.instance_at(Z("2026-10-06T05:00"))      # 01:00 local Tuesday
    assert inst.shift_id == "C" and inst.productive_day == date(2026, 10, 5)
    # Sunday: no shifts (Mon–Sat); Saturday's C ends Sunday 06:00
    assert cal.day_instances(date(2026, 10, 11)) == []
    sat_c = [i for i in cal.day_instances(date(2026, 10, 10)) if i.shift_id == "C"][0]
    assert sat_c.end.astimezone(TZ).date() == date(2026, 10, 11)
    # POT of one productive day = 24 h; breaks = 10 + 60 + 60 min
    day = cal.day_instances(date(2026, 10, 5))
    assert sum((i.end - i.start).total_seconds() for i in day) == 86400
    assert sum(total_s(i.break_intervals()) for i in day) == 130 * 60


def test_calendar_exceptions():
    cal = _manaus_cal([
        ExceptionDef(date(2026, 10, 12), "holiday"),                                   # all shifts
        ExceptionDef(date(2026, 10, 13), "holiday", shift_id="C"),
        ExceptionDef(date(2026, 10, 11), "extra_shift", start_local="06:00", end_local="12:00"),
        ExceptionDef(date(2026, 10, 14), "planned_stop", shift_id="B", reason_code="PLN-04"),
        ExceptionDef(date(2026, 10, 15), "planned_stop", start_local="07:00", end_local="08:00",
                     reason_code="PLN-03"),
    ])
    assert cal.day_instances(date(2026, 10, 12)) == []
    assert [i.shift_id for i in cal.day_instances(date(2026, 10, 13))] == ["A", "B"]
    extra = cal.day_instances(date(2026, 10, 11))
    assert len(extra) == 1 and extra[0].extra and (extra[0].end - extra[0].start) == timedelta(hours=6)
    b = [i for i in cal.day_instances(date(2026, 10, 14)) if i.shift_id == "B"][0]
    assert total_s(b.break_intervals()) == 8 * 3600 and b.breaks[0].reason_code == "PLN-04"
    a = [i for i in cal.day_instances(date(2026, 10, 15)) if i.shift_id == "A"][0]
    assert any(br.reason_code == "PLN-03" and br.start == Z("2026-10-15T11:00") for br in a.breaks)


def test_shift_overlap_detection():
    a = ShiftDef(None, "A", "06:00", "14:00", (0, 1, 2, 3, 4))
    assert shifts_overlap(a, ShiftDef("x", "B", "13:00", "22:00", (0,)), TZ)
    assert not shifts_overlap(a, ShiftDef("x", "B", "14:00", "22:00", (0, 1, 2, 3, 4)), TZ)
    # overnight Friday shift spills into Saturday morning only if Saturday has a morning shift
    assert shifts_overlap(ShiftDef(None, "C", "22:00", "07:00", (4,)), ShiftDef("y", "S", "06:00", "10:00", (5,)), TZ)
    assert not shifts_overlap(ShiftDef(None, "C", "22:00", "06:00", (4,)), ShiftDef("y", "S", "06:00", "10:00", (5,)), TZ)


# ── detection rules ─────────────────────────────────────────────────────────

def _signal(points, hb=10, until=None):
    """points: [(iso, run, fault)] → readings with heartbeats every hb s."""
    out = []
    for i, (t, run, fault) in enumerate(points):
        t0 = Z(t)
        nxt = Z(points[i + 1][0]) if i + 1 < len(points) else (until or t0)
        cur = t0
        while cur < nxt or cur == t0:
            out.append(Reading(cur, "run", run))
            out.append(Reading(cur, "fault", fault))
            cur += timedelta(seconds=hb)
            if cur >= nxt:
                break
    return out


def _cal_day():
    return Calendar(TZ, [ShiftDef("A", "Turno A", "06:00", "14:00", tuple(range(7)),
                                  ({"start_local": "09:00", "end_local": "09:10"},)),
                         ShiftDef("B", "Turno B", "14:00", "22:00", tuple(range(7)))])


def test_threshold_is_inclusive_and_119_is_micro():
    rd = _signal([("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:10:00", 0, 0), ("2026-10-05T11:11:59", 1, 0),
                  ("2026-10-05T11:20:00", 0, 0), ("2026-10-05T11:22:00", 1, 0), ("2026-10-05T11:30:00", 1, 0)])
    d = detect(SignalState(), rd, Z("2026-10-05T11:30:00"), Params())
    assert [s.duration_s for s in d.stops] == [119.0, 120.0]
    assert all(s.final for s in d.stops)


def test_debounce_and_merge_under_10s():
    rd = _signal([("2026-10-05T11:00:00", 1, 0),
                  ("2026-10-05T11:05:00", 0, 0), ("2026-10-05T11:05:00.500000", 1, 0),   # 0,5 s: not a stop
                  ("2026-10-05T11:10:00", 0, 0), ("2026-10-05T11:11:00", 1, 0),          # 60 s
                  ("2026-10-05T11:11:09", 0, 0), ("2026-10-05T11:12:09", 1, 0),          # 9 s later: merged
                  ("2026-10-05T11:20:00", 0, 0), ("2026-10-05T11:21:00", 1, 0),          # 60 s
                  ("2026-10-05T11:21:10", 0, 0), ("2026-10-05T11:22:00", 1, 0),          # 10 s later: separate
                  ("2026-10-05T11:30:00", 1, 0)])
    d = detect(SignalState(), rd, Z("2026-10-05T11:30:00"), Params())
    assert [(s.start.strftime("%H:%M:%S"), s.duration_s) for s in d.stops] == [
        ("11:10:00", 129.0), ("11:20:00", 60.0), ("11:21:10", 50.0)]


def test_stop_becoming_error_is_one_event_with_eqp_and_failed_at_at_fault():
    """Decisões Sprint 3: stopped → error mid-way = one event, suggested EQP-*, failed_at = fault onset."""
    rd = _signal([("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:10:00", 0, 0), ("2026-10-05T11:13:00", 0, 204),
                  ("2026-10-05T11:20:00", 1, 0), ("2026-10-05T11:30:00", 1, 0)])
    d = detect(SignalState(), rd, Z("2026-10-05T11:30:00"), Params())
    assert len(d.stops) == 1
    s = d.stops[0]
    assert s.start == Z("2026-10-05T11:10:00") and s.end == Z("2026-10-05T11:20:00")
    assert s.fault_code == 204 and s.fault_since == Z("2026-10-05T11:13:00") and s.source == "auto_fault"
    assert suggested_reason(s.fault_code, {"204": "EQP-02"}) == "EQP-02"
    assert suggested_reason(s.fault_code, {}) == "EQP-01"


def test_open_stop_and_checkpoint_resume_gives_same_result():
    pts = [("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:10:00", 0, 101), ("2026-10-05T11:20:00", 1, 0),
           ("2026-10-05T11:40:00", 1, 0)]
    rd = _signal(pts)
    full = detect(SignalState(), rd, Z("2026-10-05T11:40:00"), Params())
    # pass 1 at 11:15 (stop open), pass 2 from its checkpoint
    p1 = detect(SignalState(), [r for r in rd if r.ts <= Z("2026-10-05T11:15:00")], Z("2026-10-05T11:15:00"), Params())
    assert len(p1.stops) == 1 and p1.stops[0].end is None and not p1.stops[0].final
    # the checkpoint advances inside the open stop (no re-reading of the whole stop on every tick):
    # it carries the stop start and its fault
    assert p1.checkpoint > Z("2026-10-05T11:10:00")
    assert p1.checkpoint_state.down_since == Z("2026-10-05T11:10:00")
    assert p1.checkpoint_state.stop_fault == 101
    p2 = detect(p1.checkpoint_state, [r for r in rd if r.ts > p1.checkpoint], Z("2026-10-05T11:40:00"), Params())
    assert [(s.start, s.end, s.fault_code, s.fault_since) for s in p2.stops] == \
        [(s.start, s.end, s.fault_code, s.fault_since) for s in full.stops]


def test_data_gap_is_no_data_not_stop_or_running():
    """C11: a reading is valid until last + 60 s; the rest of the gap is 'sem dados'."""
    rd = _signal([("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:10:00", 1, 0)], hb=10)
    rd = [r for r in rd if r.ts <= Z("2026-10-05T11:10:00")]
    rd += _signal([("2026-10-05T11:30:00", 0, 0), ("2026-10-05T11:35:00", 1, 0), ("2026-10-05T11:40:00", 1, 0)])
    d = detect(SignalState(), rd, Z("2026-10-05T11:40:00"), Params())
    assert d.spans == [(Z("2026-10-05T11:00:00"), Z("2026-10-05T11:11:00")),
                       (Z("2026-10-05T11:30:00"), Z("2026-10-05T11:40:00"))]
    assert [(s.start, s.end) for s in d.stops] == [(Z("2026-10-05T11:30:00"), Z("2026-10-05T11:35:00"))]


def test_stop_cut_by_data_gap_waits_for_data_to_return():
    rd = _signal([("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:05:00", 0, 0), ("2026-10-05T11:06:00", 0, 0)])
    rd = [r for r in rd if r.ts <= Z("2026-10-05T11:06:00")]
    d = detect(SignalState(), rd, Z("2026-10-05T11:20:00"), Params())
    s = d.stops[0]
    assert s.end == Z("2026-10-05T11:07:00") and s.ended_by_nodata and not s.final


def test_segments_split_at_shift_boundary_and_break_overlap():
    cal = _cal_day()
    # 13:55–14:05 local (17:55Z–18:05Z): one stop of 10 min ≥ threshold → two events (regra 5)
    rd = _signal([("2026-10-05T17:00:00", 1, 0), ("2026-10-05T17:55:00", 0, 0), ("2026-10-05T18:05:00", 1, 0),
                  ("2026-10-05T18:30:00", 1, 0)])
    d = detect(SignalState(), rd, Z("2026-10-05T18:30:00"), Params())
    segs = segments(d.stops[0], cal, Z("2026-10-05T18:30:00"))
    assert [(g.start, g.end, g.shift_id) for g in segs] == [
        (Z("2026-10-05T17:55:00"), Z("2026-10-05T18:00:00"), "A"),
        (Z("2026-10-05T18:00:00"), Z("2026-10-05T18:05:00"), "B")]
    # threshold applies to the whole stop: a 3 min stop across the boundary → two 90 s events
    rd = _signal([("2026-10-05T17:00:00", 1, 0), ("2026-10-05T17:58:30", 0, 0), ("2026-10-05T18:01:30", 1, 0),
                  ("2026-10-05T18:30:00", 1, 0)])
    s = detect(SignalState(), rd, Z("2026-10-05T18:30:00"), Params()).stops[0]
    assert s.duration_s == 180 and [g.adot_s for g in segments(s, cal, Z("2026-10-05T18:30:00"))] == [90, 90]
    # 08:55–09:05 local: crosses the coffee break → 5 min ADOT + 5 min PDOT (C24b)
    rd = _signal([("2026-10-05T12:00:00", 1, 0), ("2026-10-05T12:55:00", 0, 0), ("2026-10-05T13:05:00", 1, 0),
                  ("2026-10-05T13:30:00", 1, 0)])
    s = detect(SignalState(), rd, Z("2026-10-05T13:30:00"), Params()).stops[0]
    g = segments(s, cal, Z("2026-10-05T13:30:00"))
    assert len(g) == 1 and g[0].adot_s == 300 and g[0].pdot_s == 300
    # stopped exactly during the break: no event (C24d)
    rd = _signal([("2026-10-05T12:00:00", 1, 0), ("2026-10-05T13:00:00", 0, 0), ("2026-10-05T13:10:00", 1, 0),
                  ("2026-10-05T13:30:00", 1, 0)])
    s = detect(SignalState(), rd, Z("2026-10-05T13:30:00"), Params()).stops[0]
    assert s.duration_s == 600 and segments(s, cal, Z("2026-10-05T13:30:00")) == []


def test_open_stop_crossing_shift_end():
    cal = _cal_day()
    rd = _signal([("2026-10-05T17:00:00", 1, 0), ("2026-10-05T17:50:00", 0, 0), ("2026-10-05T18:20:00", 0, 0)])
    now = Z("2026-10-05T18:20:00")
    s = detect(SignalState(), rd, now, Params()).stops[0]
    assert s.end is None
    segs = segments(s, cal, now)
    assert [(g.end, g.shift_id) for g in segs] == [(Z("2026-10-05T18:00:00"), "A"), (None, "B")]


def test_micro_pieces_and_short_fault():
    cal = _cal_day()
    rd = _signal([("2026-10-05T11:00:00", 1, 0), ("2026-10-05T11:59:30", 0, 305), ("2026-10-05T12:00:30", 1, 0),
                  ("2026-10-05T12:10:00", 1, 0)])
    s = detect(SignalState(), rd, Z("2026-10-05T12:10:00"), Params()).stops[0]
    pieces = micro_pieces(s, cal)
    assert [(p.bucket_start.hour, p.loss_s, p.count, p.short_faults) for p in pieces] == [(11, 30, 1, 1), (12, 30, 0, 0)]
