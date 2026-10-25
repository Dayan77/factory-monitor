"""Golden scenarios (docs/scenarios/*.json) — self-consistency + conformance.

Two layers:

1. **Oracle** (always runs): an implementation of the rules in docs/domain.md §3–4 and docs/kpis.md
   (incl. "Esclarecimentos Sprint 2"), written from the spec and independent of server/, recomputes
   every `expected` value of every scenario from its `input`/`readings`. If a scenario's numbers are
   edited by hand and stop matching the formulas, this fails.
2. **Conformance** (runs when server/src/services/usage/core.py exists): feeds the same readings
   through the server's pure usage functions (counter_step, run_step, split_hourly, life_consumed,
   rul). This is the unit-level proof; QA's end-to-end run (MQTT → processor → API) is described in
   README.md and must give the same numbers.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
A = json.loads((HERE / "scenario_a_usage.json").read_text(encoding="utf-8"))
B = json.loads((HERE / "scenario_b_production.json").read_text(encoding="utf-8"))
C = json.loads((HERE / "scenario_c_oee_shift.json").read_text(encoding="utf-8"))
TZ = ZoneInfo("America/Manaus")
UTC = timezone.utc


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def zs(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


def hour_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


# ════════════════════════════════════════════════════════════════════════════
# Oracle — written from the spec text, not from server code
# ════════════════════════════════════════════════════════════════════════════

def oracle_counter(readings, counter_max, max_rate):
    """domain.md §3.3 rules 1–5 + clarifications C1–C6 (baseline, Δt from last accepted,
    rule 4 after rules 1–3, modulus counter_max+1, out-of-order ignored, re-baseline)."""
    last_v = last_t = None
    pending = None  # (value, ts) of a glitched reading
    out = []
    for r in readings:
        cur, t = int(r["value"]), ts(r["ts"])
        if last_v is None:
            last_v, last_t = cur, t
            out.append((0, None, None))
            continue
        if t < last_t:
            out.append((0, None, "out_of_order"))
            continue
        if t == last_t and cur == last_v:
            out.append((0, None, "duplicate"))
            continue
        dt = (t - last_t).total_seconds()
        if cur >= last_v:
            d, ev = cur - last_v, None
        elif counter_max is not None and last_v >= 0.9 * counter_max:
            d, ev = (counter_max + 1 - last_v) + cur, "counter_rollover"
        else:
            d, ev = cur, "counter_reset"
        bound = max_rate * dt * 1.5 + 10
        if d > bound:
            if pending and t > pending[1] and cur >= pending[0] and \
                    cur - pending[0] <= max_rate * (t - pending[1]).total_seconds() * 1.5 + 10:
                out.append((cur - pending[0], "counter_rebaseline", None))
                last_v, last_t, pending = cur, t, None
                continue
            pending = (cur, t)
            out.append((0, "counter_glitch", None))
            continue
        last_v, last_t, pending = cur, t, None
        out.append((d, ev, None))
    return out


def oracle_run_state(readings, timeout_s, debounce_s):
    """domain.md §3.4 + C8–C11: Σ intervals with 1 (capped at last reading + timeout), starts =
    0→1 edges whose preceding 0 lasted ≥ debounce; first reading and 1 after a gap = no start."""
    state = last_t = off_since = None
    intervals, starts, debounced = [], [], 0
    for r in readings:
        on, t = bool(r["value"]), ts(r["ts"])
        if state is None:
            state, last_t, off_since = on, t, (None if on else t)
            continue
        if t < last_t or (t == last_t and on == state):
            continue
        if state:
            end = min(t, last_t + timedelta(seconds=timeout_s))
            if end > last_t:
                intervals.append((last_t, end))
        if on and not state:
            if off_since is not None and (t - off_since).total_seconds() < debounce_s:
                debounced += 1
            else:
                starts.append(t)
            off_since = None
        elif not on and state:
            off_since = t
        state, last_t = on, t
    return intervals, starts, debounced


def seconds_by_hour(intervals):
    out = defaultdict(float)
    for a, b in intervals:
        cur = a
        while cur < b:
            nxt = min(b, hour_utc(cur) + timedelta(hours=1))
            out[zs(hour_utc(cur))] += (nxt - cur).total_seconds()
            cur = nxt
    return dict(out)


def oracle_life(nominal, usage):
    best = (None, None)
    for m, L in nominal.items():
        if L and L > 0:
            v = usage.get(m, 0) / L
            if best[0] is None or v > best[0]:
                best = (v, m)
    return best


def oracle_rul(today: date, nominal, usage, window14):
    best = None
    for m, L in nominal.items():
        if not L:
            continue
        remaining = L - usage.get(m, 0)
        rate = window14.get(m, 0) / 14
        if remaining <= 0:
            cand = (0, m)
        elif rate <= 0:
            continue
        else:
            cand = (math.ceil(remaining / rate - 1e-9), m)
        if best is None or cand[0] < best[0]:
            best = cand
    if best is None:
        return None, None, None
    return best[0], best[1], today + timedelta(days=best[0])


def plant_today(evaluate_at: str) -> date:
    return ts(evaluate_at).astimezone(TZ).date()


# ── Scenario A1: cylinder counter ────────────────────────────────────────────

A1 = A["a1_cylinder"]
A1X = A1["expected"]


def test_a1_max_rate_derivation():
    a = A1["asset"]
    assert close(2 * a["cycles_per_part"] / a["machine_ideal_cycle_time_s"], A1["derived"]["max_rate_per_s"])


def test_a1_per_reading_deltas_events():
    res = oracle_counter(A1["readings"], A1["tag"]["counter_max"], A1["derived"]["max_rate_per_s"])
    for r, (d, ev, ign) in zip(A1["readings"], res):
        assert d == r["expect_delta"], (r["ts"], d, r)
        assert ev == ({"rollover": "counter_rollover"}.get(r.get("expect_event"), r.get("expect_event"))), r
        assert ign == r.get("expect_ignored"), r
    assert sum(d for d, _, _ in res) == A1X["cycles_delta"] == 197
    evs = [{"ts": r["ts"], "event": ev, "delta": d}
           for r, (d, ev, _) in zip(A1["readings"], res) if ev]
    assert evs == A1X["events"]
    ign = [{"ts": r["ts"], "reason": i} for r, (_, _, i) in zip(A1["readings"], res) if i]
    assert ign == A1X["ignored"]


def test_a1_hour_buckets_attribute_to_reading_ts():
    res = oracle_counter(A1["readings"], A1["tag"]["counter_max"], A1["derived"]["max_rate_per_s"])
    by = defaultdict(int)
    for r, (d, _, _) in zip(A1["readings"], res):
        if d:
            by[zs(hour_utc(ts(r["ts"])))] += d
    assert dict(by) == A1X["cycles_by_hour_utc"]
    local = {ts(k).astimezone(TZ).strftime("%H:%M"): v for k, v in by.items()}
    assert local == A1X["cycles_by_hour_local"]


def test_a1_totals_life_and_projection():
    d = A1X["cycles_delta"]
    pos = A1["seed"]["position_usage_total"]["cycles"] + d
    comp = A1["seed"]["component_usage_since_install"]["cycles"] + d
    assert pos == A1X["position"]["cycles_total"] == 7_200_000
    assert comp == A1X["component"]["usage_since_install"]["cycles"] == 3_600_000
    life, m = oracle_life(A1["nominal_life"], {"cycles": comp})
    assert close(life, A1X["component"]["life_consumed_pct"]) and m == A1X["component"]["life_measure"]
    today = plant_today(A["evaluate_at"])
    assert today.isoformat() == A["today_plant"] == "2026-10-05"
    days, meas, end = oracle_rul(today, A1["nominal_life"], {"cycles": comp}, A1["window_14d_usage"])
    assert (days, meas, end.isoformat()) == (70, "cycles", A1X["component"]["projected_end_date"])
    # trap: UTC date as "today"
    utc_end = ts(A["evaluate_at"]).date() + timedelta(days=days)
    assert utc_end.isoformat() == A1X["wrong_answers"]["projection_with_utc_today"]["projected_end_date"]
    assert close(d / A1["nominal_life"]["cycles"], A1X["without_seed"]["life_consumed_pct"])


# ── Scenario A2: motor run_state ────────────────────────────────────────────

A2 = A["a2_motor"]
A2X = A2["expected"]
CFG = A["config"]


def test_a2_heartbeats_cover_timeout():
    rs = A2["readings"]
    gap = (ts(A2["gap"]["from"]), ts(A2["gap"]["to"]))
    for p, q in zip(rs, rs[1:]):
        dt = (ts(q["ts"]) - ts(p["ts"])).total_seconds()
        if (ts(p["ts"]), ts(q["ts"])) != gap:
            assert dt <= A2["heartbeat_s"] < CFG["machine_offline_timeout_s"], (p, q)


def test_a2_hours_starts():
    iv, starts, deb = oracle_run_state(A2["readings"], CFG["machine_offline_timeout_s"], CFG["run_state_debounce_s"])
    # merge contiguous heartbeat pieces back into running intervals for comparison
    merged = []
    for a, b in iv:
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    secs = sum((b - a).total_seconds() for a, b in iv)
    assert close(secs, A2X["operating_seconds"]) and close(secs / 3600, A2X["hours_delta"])
    assert len(starts) == A2X["starts_delta"] == 3 and deb == A2X["debounced_drops"] == 3
    exp_iv = [s for *_, s in A2X["running_intervals_local"]]
    # the 07:30:00–07:31:00 credit is contiguous with 07:10–07:30 in time; split it the same way
    got = []
    for a, b in merged:
        gap_start = ts(A2["gap"]["from"])
        if a < gap_start < b:
            got += [(gap_start - a).total_seconds(), (b - gap_start).total_seconds()]
        else:
            got.append((b - a).total_seconds())
    assert all(close(g, e) for g, e in zip(got, exp_iv)) and len(got) == len(exp_iv), got
    by = seconds_by_hour(iv)
    assert by.keys() == A2X["operating_seconds_by_hour_utc"].keys()
    assert all(close(by[k], v) for k, v in A2X["operating_seconds_by_hour_utc"].items())
    sb = defaultdict(int)
    for t in starts:
        sb[zs(hour_utc(t))] += 1
    assert dict(sb) == A2X["starts_by_hour_utc"]


def _with_heartbeat(readings, hb_s, gap):
    changes = [r for r in readings if not r.get("heartbeat")]
    base = ts(changes[0]["ts"])
    out = []
    for i, r in enumerate(changes):
        out.append(r)
        if i + 1 == len(changes):
            break
        t, nxt = ts(r["ts"]), ts(changes[i + 1]["ts"])
        k = int((t - base).total_seconds() // hb_s) + 1
        while (hb := base + timedelta(seconds=k * hb_s)) < nxt:
            if not (gap[0] < hb < gap[1]):
                out.append({"ts": zs(hb), "value": r["value"]})
            k += 1
    return out


def test_a2_invariant_to_heartbeat_period():
    gap = (ts(A2["gap"]["from"]), ts(A2["gap"]["to"]))
    rs10 = _with_heartbeat(A2["readings"], 10, gap)
    iv, starts, _ = oracle_run_state(rs10, CFG["machine_offline_timeout_s"], CFG["run_state_debounce_s"])
    assert close(sum((b - a).total_seconds() for a, b in iv), A2X["operating_seconds"])
    assert len(starts) == A2X["starts_delta"]


def test_a2_wrong_answers_are_actually_different():
    w = A2X["wrong_answers"]
    _, s0, _ = oracle_run_state(A2["readings"], CFG["machine_offline_timeout_s"], 0)
    assert len(s0) == w["no_debounce"]["starts_delta"] != A2X["starts_delta"]
    _, s_le, _ = oracle_run_state(A2["readings"], CFG["machine_offline_timeout_s"], 1.0000001)
    assert len(s_le) == w["debounce_inclusive_le_1s"]["starts_delta"]
    iv, _, _ = oracle_run_state(A2["readings"], 10 ** 9, CFG["run_state_debounce_s"])
    assert close(sum((b - a).total_seconds() for a, b in iv), w["gap_credited"]["operating_seconds"])


def test_a2_totals_life_projection():
    h = A2["seed"]["component_usage_since_install"]["hours"] + A2X["hours_delta"]
    s = A2["seed"]["component_usage_since_install"]["starts"] + A2X["starts_delta"]
    assert close(h, A2X["component"]["usage_since_install"]["hours"]) and s == 130_000
    assert close(A2["seed"]["position_usage_total"]["hours"] + A2X["hours_delta"], A2X["position"]["hours_total"])
    life, m = oracle_life(A2["nominal_life"], {"hours": h, "starts": s})
    assert close(life, A2X["component"]["life_consumed_pct"]) and m == "starts"
    for k, v in A2X["component"]["life_by_measure"].items():
        assert close({"hours": h, "starts": s}[k] / A2["nominal_life"][k], v)
    days, meas, end = oracle_rul(plant_today(A["evaluate_at"]), A2["nominal_life"],
                                 {"hours": h, "starts": s}, A2["window_14d_usage"])
    assert (days, meas, end.isoformat()) == (47, "starts", A2X["component"]["projected_end_date"])
    life3, _ = oracle_life(A["a3_no_nominal_life"]["nominal_life"], {"hours": h})
    assert life3 is None and A["a3_no_nominal_life"]["expected"]["life_consumed_pct"] is None


# ── Scenario B: production ──────────────────────────────────────────────────

BX = B["expected"]


def _b_deltas():
    out = {}
    for role in ("good", "scrap"):
        rs = B["readings"][role]
        res = oracle_counter(rs, B["tags"][role]["counter_max"], B["derived"]["max_rate_per_s"])
        for r, (d, ev, ign) in zip(rs, res):
            assert d == r["expect_delta"] and ev is None and ign == r.get("expect_ignored"), r
        out[role] = [(ts(r["ts"]), d) for r, (d, _, _) in zip(rs, res) if d]
    return out


def test_b_shift_totals_and_hour_buckets():
    d = _b_deltas()
    g, s = sum(x for _, x in d["good"]), sum(x for _, x in d["scrap"])
    assert {"good": g, "scrap": s, "total": g + s} == BX["shift_totals"]
    local = defaultdict(lambda: {"good": 0, "scrap": 0})
    for role in ("good", "scrap"):
        for t, x in d[role]:
            local[t.astimezone(TZ).strftime("%Y-%m-%d %H:00")][role] += x
    for k, v in local.items():
        v["total"] = v["good"] + v["scrap"]
        v["cadence_parts_per_h"] = v["total"] / 1.0
    assert dict(local) == BX["by_hour_local"]
    utc = {zs(datetime.strptime(k, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)):
           {kk: v[kk] for kk in ("good", "scrap", "total")} for k, v in local.items()}
    assert utc == BX["by_hour_utc"]


def test_b_day_grouping():
    d = _b_deltas()

    def group(keyf):
        out = defaultdict(lambda: {"good": 0, "scrap": 0})
        for role in ("good", "scrap"):
            for t, x in d[role]:
                out[keyf(t)][role] += x
        return {k: {**v, "total": v["good"] + v["scrap"]} for k, v in out.items()}

    assert group(lambda t: t.astimezone(TZ).date().isoformat()) == BX["by_local_calendar_day"]
    shift_start = datetime.fromisoformat(B["shift"]["start_local"]).replace(tzinfo=TZ)
    assert group(lambda t: shift_start.date().isoformat()) == BX["by_productive_day"]
    wrong = {k: v for k, v in BX["wrong_answers"]["by_utc_day"].items() if k != "why"}
    assert group(lambda t: t.astimezone(UTC).date().isoformat()) == wrong
    assert wrong != BX["by_local_calendar_day"]


def test_b_cadence():
    c, sh = BX["cadence"], B["shift"]
    pdot = sum(b["min"] for b in sh["planned_breaks"])
    adot = sum(b["min"] for b in sh["unplanned_stops"])
    assert (c["pdot_min"], c["adot_min"]) == (pdot, adot)
    pbt = c["pot_min"] - pdot
    apt = pbt - adot
    pq = BX["shift_totals"]["total"]
    assert (pbt, apt) == (c["pbt_min"], c["apt_min"])
    assert close(pq / (pbt / 60), c["parts_per_hour_planned"])
    assert close(pq / (apt / 60), c["parts_per_hour_running"])
    assert close(3600 / B["asset"]["ideal_cycle_time_s"], c["ideal_parts_per_h"])
    assert close(B["asset"]["ideal_cycle_time_s"] * pq / (apt * 60), c["performance"])


# ── Scenario C: OEE preview ─────────────────────────────────────────────────

CX = C["expected"]


def _c_classify():
    thr = C["machine"]["micro_stop_threshold_s"]
    pdot = adot = micro = 0.0
    micro_n = 0
    events, failures = [], []
    for e in C["events"]:
        d = e["duration_s"]
        if e["kind"] == "planned_break":
            pdot += d
            continue
        if d < thr:
            micro += d
            micro_n += 1
            continue
        reason = e.get("reason_code")
        if e.get("fault_code") is not None:
            reason = C["machine"]["fault_code_map"][str(e["fault_code"])]
        reason = reason or "UNK-01"
        adot += d
        events.append({"from_local": e["from_local"], "reason_code": reason, "duration_s": d})
        if reason.startswith("EQP-") and e.get("severity") in ("critical", "degraded"):
            failures.append(d)
    return pdot / 60, adot / 60, micro / 60, micro_n, events, failures


def test_c_signals_match_events():
    """The run_state/fault_state transitions reproduce exactly the event list (QA feeds signals)."""
    sig = C["signals"]
    stops, cur = [], None
    for s in sig[1:-1]:
        if s["run_state"] == 0:
            cur = (s["ts"], s["fault_state"])
        else:
            stops.append((cur[0], s["ts"], cur[1]))
    assert len(stops) == len(C["events"])
    for (a, b, f), e in zip(stops, C["events"]):
        assert (a, b) == (e["from_utc"], e["to_utc"])
        assert f == (e.get("fault_code") or 0)
    shift = (ts(sig[0]["ts"]), ts(sig[-1]["ts"]))
    assert (shift[1] - shift[0]).total_seconds() / 60 == CX["pot_min"]
    run_min = (shift[1] - shift[0]).total_seconds() / 60 - sum(e["duration_s"] for e in C["events"]) / 60
    assert close(run_min, CX["operating_min_run_state"])


def test_c_time_model_and_oee():
    pdot, adot, micro, micro_n, events, _ = _c_classify()
    pot = CX["pot_min"]
    pbt = pot - pdot
    apt = pbt - adot
    assert (pdot, pbt, adot, apt) == (CX["pdot_min"], CX["pbt_min"], CX["adot_min"], CX["apt_min"])
    assert events == CX["downtime_events"]
    assert (micro_n, micro * 60) == (CX["micro_stop_count"], CX["micro_stop_s"])
    assert close(micro_n / (apt / 60), CX["micro_stops_per_apt_hour"])
    pri = C["machine"]["ideal_cycle_time_s"] / 60
    p = C["production"]
    ip, ig = pri * p["produced"], pri * p["good"]
    assert p["produced"] == p["good"] + p["scrap"]
    assert close(ip, CX["ideal_time_produced_min"]) and close(ig, CX["net_productive_min"])
    A_, P_, Q_ = apt / pbt, ip / apt, ig / ip
    assert close(A_, CX["availability"]) and close(P_, CX["performance"]) and close(Q_, CX["quality"])
    assert close(A_ * P_ * Q_, CX["oee"]) and close(ig / pbt, CX["oee"])
    c = CX["cadence"]
    assert close(p["produced"] / (pbt / 60), c["parts_per_hour_planned"])
    assert close(p["produced"] / (apt / 60), c["parts_per_hour_running"])
    assert close(60 / pri, c["parts_per_hour_ideal"])


def test_c_reliability():
    pdot, adot, micro, _, events, failures = _c_classify()
    n = len(failures)
    assert n == CX["failures"]["n"] == 3
    op_h = (CX["pbt_min"] - adot - micro) / 60
    assert close(op_h * 60, CX["operating_min_run_state"])
    assert close(op_h / n, CX["mtbf_h"])
    assert close(sum(failures) / n / 60, CX["mttr_min"]) and close(sum(failures) / n, CX["mttr_s"])
    w = CX["wrong_answers"]
    assert not close(CX["apt_min"] / 60 / n, CX["mtbf_h"]) and close(CX["apt_min"] / 60 / n, w["mtbf_with_apt"]["mtbf_h"])
    deq = sum(e["duration_s"] for e in events if e["reason_code"].startswith("EQP-")) / 60
    req = CX["pot_min"] - pdot  # all planned stops here are PLN-01
    ao = CX["availability_operational"]
    assert (req, deq) == (ao["required_min"], ao["d_eq_min"]) and close((req - deq) / req, ao["ao"])


def test_c_loss_pareto():
    pdot, adot, micro, _, events, _ = _c_classify()
    pri = C["machine"]["ideal_cycle_time_s"] / 60
    p = C["production"]
    apt = CX["apt_min"]
    speed = apt - pri * p["produced"] - micro
    quality = pri * p["scrap"]
    by = defaultdict(float)
    for e in events:
        by[e["reason_code"]] += e["duration_s"] / 60
    items = dict(by)
    items.update(speed_loss=speed, micro_stops=micro, quality_loss=quality)
    ranked = sorted(items.items(), key=lambda kv: -kv[1])
    total = sum(items.values())
    lp = CX["loss_pareto"]
    assert close(total, lp["total_loss_min"]) and close(total, CX["pbt_min"] - CX["net_productive_min"])
    assert close(speed, lp["speed_loss_min"]) and close(quality, lp["quality_loss_min"])
    assert [k for k, _ in ranked] == [it[0] for it in lp["items"]]
    acc = 0.0
    for (_, v), (_, e, pct, cum) in zip(ranked, lp["items"]):
        acc += v
        assert close(v, e) and abs(v / total - pct) <= 5e-7 and abs(acc / total - cum) <= 5e-7
    cat = defaultdict(float)
    for k, v in items.items():
        cat[k.split("-")[0] if "-" in k else k] += v
    rc = sorted(cat.items(), key=lambda kv: -kv[1])
    assert [k for k, _ in rc] == [k for k, _ in CX["loss_pareto_by_category"]]
    assert all(close(v, e) for (_, v), (_, e) in zip(rc, CX["loss_pareto_by_category"]))


def test_readme_lists_every_scenario_file():
    text = (HERE / "README.md").read_text(encoding="utf-8")
    for f in HERE.glob("scenario_*.json"):
        assert f.name in text


# ════════════════════════════════════════════════════════════════════════════
# Conformance — the server's pure usage core must give the same numbers
# ════════════════════════════════════════════════════════════════════════════

CORE_PATH = ROOT / "server" / "src" / "services" / "usage" / "core.py"


@pytest.fixture(scope="module")
def core():
    if not CORE_PATH.exists():
        pytest.skip("server usage core not present")
    spec = importlib.util.spec_from_file_location("usage_core_under_test", CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _core_counter(core, readings, cmax, rate):
    track, out = None, []
    for r in readings:
        res = core.counter_step(track, r["value"], ts(r["ts"]), cmax, rate)
        track = res.track
        ev = {"rollover": "counter_rollover"}.get(res.event, res.event)
        out.append((res.delta, ev, res.ignored))
    return out


def test_core_a1(core):
    res = _core_counter(core, A1["readings"], A1["tag"]["counter_max"], A1["derived"]["max_rate_per_s"])
    for r, (d, ev, ign) in zip(A1["readings"], res):
        exp_ev = {"rollover": "counter_rollover"}.get(r.get("expect_event"), r.get("expect_event"))
        assert (d, ev, ign) == (r["expect_delta"], exp_ev, r.get("expect_ignored")), r
    assert core.resolve_max_rate(None, A1["asset"]["machine_ideal_cycle_time_s"],
                                 A1["asset"]["cycles_per_part"], 10.0) == A1["derived"]["max_rate_per_s"]
    comp = A1X["component"]
    life, m = core.life_consumed(A1["nominal_life"], comp["usage_since_install"])
    assert close(life, comp["life_consumed_pct"]) and m == comp["life_measure"]
    r = core.rul(plant_today(A["evaluate_at"]), A1["nominal_life"], comp["usage_since_install"],
                 A1["window_14d_usage"], history_days=200)
    assert (r.days, r.measure, r.date.isoformat(), r.low_confidence) == (70, "cycles", comp["projected_end_date"], False)


def test_core_a2(core):
    track, secs, starts = None, defaultdict(float), defaultdict(int)
    for r in A2["readings"]:
        res = core.run_step(track, r["value"], ts(r["ts"]), CFG["machine_offline_timeout_s"], CFG["run_state_debounce_s"])
        track = res.track
        for a, b in res.intervals:
            for bucket, s in core.split_hourly(a, b):
                secs[zs(bucket)] += s
        if res.starts:
            starts[zs(core.hour_floor(ts(r["ts"])))] += res.starts
    assert close(sum(secs.values()), A2X["operating_seconds"])
    assert all(close(secs[k], v) for k, v in A2X["operating_seconds_by_hour_utc"].items())
    assert dict(starts) == A2X["starts_by_hour_utc"]
    comp = A2X["component"]
    life, m = core.life_consumed(A2["nominal_life"], comp["usage_since_install"])
    assert close(life, comp["life_consumed_pct"]) and m == "starts"
    r = core.rul(plant_today(A["evaluate_at"]), A2["nominal_life"], comp["usage_since_install"],
                 A2["window_14d_usage"], history_days=600)
    assert (r.days, r.measure, r.date.isoformat()) == (47, "starts", comp["projected_end_date"])
    assert core.life_consumed({}, comp["usage_since_install"]) == (None, None)


def test_core_b(core):
    by = defaultdict(lambda: defaultdict(int))
    for role in ("good", "scrap"):
        rs = B["readings"][role]
        for r, (d, ev, ign) in zip(rs, _core_counter(core, rs, B["tags"][role]["counter_max"],
                                                     B["derived"]["max_rate_per_s"])):
            assert (d, ev, ign) == (r["expect_delta"], None, r.get("expect_ignored")), r
            if d:
                by[zs(core.hour_floor(ts(r["ts"])))][role] += d
    got = {k: {"good": v["good"], "scrap": v["scrap"], "total": v["good"] + v["scrap"]} for k, v in by.items()}
    assert got == BX["by_hour_utc"]


def test_readme_b_table_matches_json():
    """The hour table in README.md (scenario B) must show the same numbers as the JSON."""
    import re
    text = (HERE / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (0[56])/10 (\d\d):00[^|]*\|(?:[^|]*\|){4} (\d+) \| (\d+) \| \*\*(\d+)\*\* \|$", text, re.M)
    got = {f"2026-10-{d} {h}:00": (int(g), int(s), int(t)) for d, h, g, s, t in rows}
    exp = {k: (v["good"], v["scrap"], v["total"]) for k, v in BX["by_hour_local"].items()}
    assert got == exp
