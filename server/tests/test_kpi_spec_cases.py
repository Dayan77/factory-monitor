"""Spec consistency: every worked example in docs/kpis.md (`kpi-test-cases` block) must match an
independent reference implementation of the formulas the doc defines.

This guards the KPI contract before the production KPI engine exists (Fase 2). When the real engine
lands, add a second test that runs the same cases through it; this file stays as the oracle.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "docs" / "kpis.md"
TEXT = DOC.read_text(encoding="utf-8")
_m = re.search(r"```json kpi-test-cases\n(.*?)\n```", TEXT, re.S)
assert _m, "docs/kpis.md has no ```json kpi-test-cases block"
CASES = json.loads(_m.group(1))


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, (str, bool)) or isinstance(b, (str, bool)):
        return a == b
    return abs(a - b) <= 1e-6 * max(1.0, abs(b))


def _assert_match(cid, key, got, exp):
    if isinstance(exp, list):
        assert isinstance(got, list) and len(got) == len(exp), f"{cid}.{key}: {got!r} != {exp!r}"
        for i, (g, e) in enumerate(zip(got, exp)):
            _assert_match(cid, f"{key}[{i}]", g, e)
        return
    assert _close(got, exp), f"{cid}.{key}: computed {got!r}, doc says {exp!r}"


def _shift_parts(s):
    pbt = s["pot_min"] - s["planned_downtime_min"]
    apt = pbt - sum(s["unplanned_downtime_min"])
    ip = sum(p["ideal_cycle_time_s"] / 60 * p["produced"] for p in s["products"])
    ig = sum(p["ideal_cycle_time_s"] / 60 * p["good"] for p in s["products"])
    return pbt, apt, ip, ig


def _ratios(pbt, apt, ip, ig):
    return dict(availability=apt / pbt, performance=ip / apt, quality=ig / ip, oee=ig / pbt)


def _counter_delta(prev, cur, cmax, dt=None, rate=None):
    if cur >= prev:
        d, ev = cur - prev, None
    elif cmax is not None and prev >= 0.9 * cmax:
        d, ev = (cmax - prev) + cur + 1, "rollover"
    else:
        d, ev = cur, "counter_reset"
    if dt is not None and rate is not None and d > rate * dt * 1.5 + 10:
        return 0, "counter_glitch"
    return d, ev


def _compute(case):
    """Returns (computed, expected) dicts for one case."""
    k, i, e = case["kpi"], case["input"], dict(case["expected"])
    if k == "oee":
        pbt, apt, ip, ig = _shift_parts(i)
        return dict(pbt_min=pbt, apt_min=apt, **_ratios(pbt, apt, ip, ig)), e
    if k == "oee_aggregate":
        parts = [_shift_parts(s) for s in i["shifts"]]
        tot = [sum(x) for x in zip(*parts)]
        rb = _ratios(*parts[1])
        so = [_ratios(*p)["oee"] for p in parts]
        got = dict(shift_oee=so, shift_b_performance=rb["performance"], shift_b_quality=rb["quality"],
                   pbt_min=tot[0], apt_min=tot[1], wrong_mean_of_shift_oee=sum(so) / len(so), **_ratios(*tot))
        assert not _close(got["oee"], got["wrong_mean_of_shift_oee"]), "aggregate example must differ from mean"
        return got, e
    if k == "teep":
        return dict(loading=i["pbt_min"] / i["calendar_min"], teep=i["net_productive_min"] / i["calendar_min"]), e
    if k == "micro_stops":
        ct, th = i["ideal_cycle_time_s"], i["micro_stop_threshold_s"]
        sl = ms = n = 0
        ev = []
        for g in i["cycle_gaps_s"]:
            if g > 2 * ct:
                d = g - ct
                if d < th:
                    ms += d
                    n += 1
                else:
                    ev.append(d)
            elif g > ct:
                sl += g - ct
        return dict(speed_loss_s=sl, micro_stop_count=n, micro_stop_s=ms, downtime_events_s=ev), e
    if k == "cadence":
        return dict(parts_per_hour_planned=i["produced"] / (i["pbt_min"] / 60),
                    parts_per_hour_running=i["produced"] / (i["apt_min"] / 60),
                    parts_per_hour_ideal=3600 / i["ideal_cycle_time_s"]), e
    if k == "loss_pareto":
        speed = i["apt_min"] - i["ideal_time_produced_min"] - i["micro_stop_min"]
        qual = i["ideal_cycle_time_s"] / 60 * i["scrap"]
        items = dict(i["downtime_by_reason_min"])
        items.update(speed_loss=speed, micro_stops=i["micro_stop_min"], quality_loss=qual)
        total = sum(items.values())
        assert _close(total, i["pbt_min"] - (i["ideal_time_produced_min"] - qual)), \
            "losses must sum to PBT - net productive time"
        acc, rows = 0.0, []
        for name, v in sorted(items.items(), key=lambda kv: -kv[1]):
            acc += v
            rows.append([name, v, v / total, acc / total])
        return dict(total_loss_min=total, items=rows), e
    if k == "mtbf":
        n = sum(1 for f in i["failures"] if f["severity"] in ("critical", "degraded"))
        inc = sum(1 for f in i["failures"] if f["severity"] == "incipient")
        return dict(n=n, mtbf_h=(i["operating_hours"] / n) if n else None, incipient=inc,
                    reason=None if n else "no_failures", operating_hours=i["operating_hours"]), e
    if k == "mcbf":
        return dict(mcbf_cycles=i["cycles"] / i["failures"]), e
    if k == "mttr":
        d = i["restore_durations_min"]
        return dict(mttr_min=sum(d) / len(d)), e
    if k == "mttf":
        f = i["failed_ages_h"]
        return dict(mttf_h=sum(f) / len(f), failures=len(f), suspensions=len(i["suspended_ages_h"])), e
    if k == "failure_rate":
        lam = i["failures"] / i["operating_hours"]
        got = dict(per_hour=lam, per_million_hours=lam * 1e6,
                   per_million_cycles=i["cycle_failures"] / i["cycles"] * 1e6)
        # the doc rounds these two for display
        assert abs(got.pop("per_hour") - e.pop("per_hour")) < 1e-7
        assert abs(got.pop("per_million_hours") - e.pop("per_million_hours")) < 1e-3
        return got, e
    if k == "availability_inherent":
        return dict(ai=i["mtbf_h"] / (i["mtbf_h"] + i["mttr_h"])), e
    if k == "availability_operational":
        req = i["pot_h"] - i["excluded_h"]
        deq = i["failure_downtime_h"] + i["maintenance_downtime_h"]
        return dict(required_h=req, ao=(req - deq) / req), e
    if k == "pmp":
        h = i["labor_hours"]
        planned = sum(v for kk, v in h.items() if kk != "corrective_emergency")
        return dict(planned_h=planned, total_h=sum(h.values()), pmp=planned / sum(h.values())), e
    if k == "schedule_compliance":
        assert i["on_time"] + i["late"] + i["not_done"] == i["scheduled"]
        return dict(compliance=i["on_time"] / i["scheduled"]), e
    if k == "backlog":
        return dict(weeks=i["open_estimated_hours"] / i["weekly_capacity_hours"]), e
    if k == "life_consumed":
        lv = {mm: i["usage_since_install"][mm] / L for mm, L in i["nominal"].items()}
        mm = max(lv, key=lv.get)
        return dict(life=lv[mm], measure=mm), e
    if k == "rul":
        today = date.fromisoformat(i["today"])
        best = None
        for mm, L in i["nominal"].items():
            rem = L - i["usage_since_install"][mm]
            rate = i["usage_last_14_days"][mm] / 14
            days = math.ceil(rem / rate)
            if best is None or days < best[3]:
                best = (mm, rem, rate, days)
        return dict(remaining=best[1], rate_per_day=best[2], days=best[3],
                    date=(today + timedelta(days=best[3])).isoformat(), measure=best[0]), e
    if k == "plan_due":
        today = date.fromisoformat(i["today"])
        iv, sm = i["interval"], i["since_maintenance"]
        consumed = max(sm[t] / iv[t] for t in iv)
        state = "ok" if consumed < 0.8 else "due_soon" if consumed < 1 else "due"
        opts = {t: (iv[t] - sm[t]) if t == "days" else math.ceil((iv[t] - sm[t]) / i["rate_per_day"][t])
                for t in iv}
        trig = min(opts, key=opts.get)
        return dict(consumed=consumed, state=state, trigger=trig, days=opts[trig],
                    date=(today + timedelta(days=opts[trig])).isoformat()), e
    if k == "counter_delta":
        res = [_counter_delta(x["prev"], x["cur"], x["counter_max"], x.get("dt_s"), x.get("max_rate_per_s"))
               for x in i["cases"]]
        return dict(deltas=[r[0] for r in res], events=[r[1] for r in res]), e
    if k == "estimated_cycles":
        return dict(cycles=i["parts"] * i["cycles_per_part"], estimated=True), e
    pytest.fail(f"no reference implementation for kpi {k!r} (case {case['id']})")


def test_spec_has_worked_examples():
    assert len(CASES) >= 20
    assert len({c["id"] for c in CASES}) == len(CASES), "duplicate case ids"
    assert (DOC.parent / "domain.md").exists()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_kpi_case_matches_reference(case):
    got, exp = _compute(case)
    for key, val in exp.items():
        assert key in got, f"{case['id']}: reference does not compute {key!r}"
        _assert_match(case["id"], key, got[key], val)
