"""docs/reports.md + docs/kpis.md "Decisões Sprint 5" — independent oracle for the worked examples.

1. kpis.md `kpi-s5-cases` (S5-1 break fault): rebuilds events, PDOT/ADOT, restoration, OEE, Ao, MTBF and MTTR from
   the fault/run_state intervals by interval arithmetic; checks the wrong answers never coincide with the right ones;
   conformance with the server's pure KPI functions (skipped if server code does not import).
2. reports.md `report-weibull-cases`: Weibull MLE with right censoring computed here by a *different* route than the
   spec text (golden-section maximisation of the profile log-likelihood + finite-difference Hessian), so a typo in
   the spec formulas or numbers fails; Johnson/Bernard ranks; conditional failure probability.
3. reports.md `report-schedule-cases`: ready_at / send_at from the calendar rules (§4.1), in the plant time zone.
4. reports.md `report-kpi-cases` / `report-catalog`: displayed values come from kpis.md `kpi-test-cases` with the
   S3-5 rounding; recipients are real roles.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KPIS = (ROOT / "docs" / "kpis.md").read_text(encoding="utf-8")
REPORTS = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")


def block(text: str, tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", text, re.S)
    assert m, f"block {tag} not found"
    return json.loads(m.group(1))


def close(a, b, rel=1e-6, abs_=1e-6):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


# ── 1. kpis S5-1: break fault ──────────────────────────────────────────────

def _m(hhmm: str) -> float:
    parts = [int(x) for x in hhmm.split(":")]
    return parts[0] * 60 + parts[1] + (parts[2] / 60 if len(parts) == 3 else 0)


def _overlap(a, b, c, d) -> float:
    return max(0.0, min(b, d) - max(a, c))


def _productive(t, shift, breaks) -> bool:
    return shift[0] <= t < shift[1] and not any(b0 <= t < b1 for b0, b1 in breaks)


def _subtract(iv, cuts):
    out = [iv]
    for c0, c1 in cuts:
        nxt = []
        for a, b in out:
            if c1 <= a or c0 >= b:
                nxt.append((a, b))
                continue
            if a < c0:
                nxt.append((a, c0))
            if c1 < b:
                nxt.append((c1, b))
        out = nxt
    return out


def break_fault_oracle(inp):
    shift = (_m(inp["shift"][0]), _m(inp["shift"][1]))
    breaks = [(_m(a), _m(b)) for a, b in inp["breaks"]]
    pot = shift[1] - shift[0]
    pdot = sum(_overlap(*b, *shift) for b in breaks)
    pbt = pot - pdot
    events, restored, adot, outside = [], {}, 0.0, 0
    for f in inp["faults"]:
        f0, f1 = _m(f["fault"][0]), _m(f["fault"][1])
        r0, r1 = _m(f["run_zero"][0]), _m(f["run_zero"][1])
        # productive part of the stop = stop ∩ shift − breaks (S5-1: a fault stop starting in a break starts at its end)
        parts = [p for p in _subtract((max(r0, shift[0]), min(r1, shift[1])), breaks) if p[1] > p[0]] \
            if min(r1, shift[1]) > max(r0, shift[0]) else []
        for a, b in parts:
            events.append((f["id"], a, b))
            adot += b - a
        # restoration: fault clears outside productive time → when it clears; else end of the stop (D1)
        restored[f["id"]] = f1 if not _productive(f1, shift, breaks) else r1
        if not _productive(f0, shift, breaks):      # failed_at outside productive time (S5-1c item 6)
            outside += 1
    apt = pbt - adot
    pri = inp["ideal_cycle_time_s"] / 60
    ideal_p, ideal_g = pri * inp["produced"], pri * inp["good"]
    restore_min = [restored[f["id"]] - _m(f["fault"][0]) for f in inp["faults"]]
    operating_h = apt / 60          # Σ run_state = 1 (no micro-stops; machine stopped off-shift)
    n = len(inp["faults"])
    return {"pot_min": pot, "pdot_min": pdot, "pbt_min": pbt, "adot_min": adot, "apt_min": apt,
            "events": events, "restored": restored, "availability": apt / pbt, "performance": ideal_p / apt,
            "quality": inp["good"] / inp["produced"], "oee": ideal_g / pbt, "ao": (pbt - adot) / pbt,
            "n": n, "operating_h": operating_h, "mtbf_h": operating_h / n, "mttr_min": sum(restore_min) / n,
            "failures_outside_production": outside, "restore_min": restore_min}


def _hm(x: float) -> str:
    return f"{int(x // 60):02d}:{int(round(x % 60)):02d}"


S5 = {c["id"]: c for c in block(KPIS, "kpi-s5-cases")}


def test_break_fault_shift_oracle():
    c = S5["break_fault_shift"]
    got, exp = break_fault_oracle(c["input"]), c["expected"]
    for k in ("pot_min", "pdot_min", "pbt_min", "adot_min", "apt_min", "availability", "performance", "quality",
              "oee", "ao", "n", "operating_h", "mtbf_h", "mttr_min", "failures_outside_production"):
        assert close(got[k], exp[k]), (k, got[k], exp[k])
    assert [[i, _hm(a), _hm(b)] for i, a, b in got["events"]] == exp["events"]
    assert {k: _hm(v) for k, v in got["restored"].items()} == exp["restored"]


def test_break_fault_wrong_answers_differ():
    c = S5["break_fault_shift"]
    exp, wrong = c["expected"], c["wrong_answers"]
    # recompute each wrong interpretation so the table in kpis.md is right too
    assert close(wrong["sprint4_mttr_min"], (20 + 25) / 2) and close(wrong["sprint4_mtbf_h"], 6.5 / 2)
    assert close(wrong["break_as_adot_availability"], (435 - 55) / 435)
    assert close(wrong["mttr_by_event_min"], (20 + 25 + 10 + 30) / 4)
    assert close(wrong["mttr_restored_next_shift_min"], (20 + 35 + 10 + (24 * 60 + 6 * 60 - (14 * 60 + 30))) / 4)
    assert not close(wrong["sprint4_mttr_min"], exp["mttr_min"])
    assert not close(wrong["mttr_by_event_min"], exp["mttr_min"])
    assert not close(wrong["mttr_restored_next_shift_min"], exp["mttr_min"])
    assert not close(wrong["break_as_adot_availability"], exp["availability"])
    assert not close(wrong["sprint4_mtbf_h"], exp["mtbf_h"])


def test_break_short_fault_rule():
    c = S5["break_short_fault"]
    thr = c["input"]["micro_stop_threshold_s"]
    for f in c["input"]["faults_s"]:
        dur = (_m(f["end"]) - _m(f["start"])) * 60
        exp = c["expected"][f["id"]]
        is_failure = dur >= thr                      # C22: threshold inclusive
        assert is_failure == exp["failure"]
        if is_failure:
            assert close(dur / 60, exp["mttr_min"])
            assert f["end"] == exp["restored"]
        else:
            assert exp["short_fault"] is True


def _server():
    srv = ROOT / "server"
    if str(srv) not in sys.path:
        sys.path.insert(0, str(srv))
    try:
        from src.services.oee import kpi                        # noqa: WPS433
        from src.services.maintenance import core                # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"server code not importable: {exc}")
    return kpi, core


def test_break_fault_conformance_with_server_kpis():
    kpi, core = _server()
    c = S5["break_fault_shift"]
    inp, exp = c["input"], c["expected"]
    pri = inp["ideal_cycle_time_s"]
    comp = kpi.Components(pot_s=480 * 60, pdot_s=45 * 60, covered_s=480 * 60, adot_s=45 * 60, calendar_s=480 * 60,
                          produced=inp["produced"], good=inp["good"], scrap=inp["produced"] - inp["good"],
                          ideal_produced_s=pri * inp["produced"], ideal_good_s=pri * inp["good"],
                          ao_excluded_s=45 * 60, ao_equipment_s=45 * 60)
    r = kpi.oee(comp)
    for k in ("availability", "performance", "quality", "oee"):
        assert close(r[k], exp[k]), k
    assert close(r["availability_operational"], exp["ao"])
    orc = break_fault_oracle(inp)
    rel = kpi.reliability(exp["operating_h"] * 3600, [("critical", m * 60) for m in orc["restore_min"]])
    assert rel["failures"] == exp["n"] and close(rel["mtbf_h"], exp["mtbf_h"]) and close(rel["mttr_s"] / 60, exp["mttr_min"])
    t0 = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    facts = [core.FailureFacts(str(i), "critical", t0, t0 + timedelta(minutes=m), None, None, "m")
             for i, m in enumerate(orc["restore_min"])]
    row = core.reliability_row(facts, exp["operating_h"], None)
    assert row["n"] == 4 and row["restored"] == 4 and close(row["mttr_min"], exp["mttr_min"])


# ── 2. reports.md: Weibull with right censoring ───────────────────────────

Z90 = 1.6448536269514722


def _loglik(beta, eta, fails, alls):
    return (sum(math.log(beta) - beta * math.log(eta) + (beta - 1) * math.log(t) for t in fails)
            - sum((t / eta) ** beta for t in alls))


def _profile_eta(beta, fails, alls):
    return (sum(t ** beta for t in alls) / len(fails)) ** (1 / beta)


def weibull_oracle(fails, susp):
    """Maximise the profile log-likelihood by golden-section search (not the score equation of the spec)."""
    alls = fails + susp
    s = max(alls)
    f_, a_ = [t / s for t in fails], [t / s for t in alls]
    prof = lambda b: _loglik(b, _profile_eta(b, f_, a_), f_, a_)   # noqa: E731
    lo, hi, gr = 0.05, 50.0, (math.sqrt(5) - 1) / 2
    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    for _ in range(300):
        if prof(c) > prof(d):
            hi = d
        else:
            lo = c
        c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    beta = (lo + hi) / 2
    eta = _profile_eta(beta, f_, a_) * s
    # finite-difference Hessian of ln L in (β, η) at the MLE (original scale)
    hb, he = beta * 1e-4, eta * 1e-4
    L = lambda b, e: _loglik(b, e, fails, alls)   # noqa: E731
    Hbb = (L(beta + hb, eta) - 2 * L(beta, eta) + L(beta - hb, eta)) / hb ** 2
    Hee = (L(beta, eta + he) - 2 * L(beta, eta) + L(beta, eta - he)) / he ** 2
    Hbe = (L(beta + hb, eta + he) - L(beta + hb, eta - he) - L(beta - hb, eta + he) + L(beta - hb, eta - he)) / (4 * hb * he)
    a, b_, dd = -Hbb, -Hbe, -Hee
    det = a * dd - b_ * b_
    vb, ve, cov = dd / det, a / det, -b_ / det
    k = math.log(-math.log(0.9))
    b10 = eta * math.exp(k / beta)
    vl = ve / eta ** 2 + (k / beta ** 2) ** 2 * vb - 2 * (k / beta ** 2) * cov / eta
    R = lambda t: math.exp(-((t / eta) ** beta))   # noqa: E731
    sb, se = math.sqrt(vb), math.sqrt(ve)
    blo, bhi = beta * math.exp(-Z90 * sb / beta), beta * math.exp(Z90 * sb / beta)
    pattern = "infant_mortality" if bhi < 1 else ("wear_out" if blo > 1 else "random")
    return {"beta": beta, "eta": eta, "se_beta": sb, "se_eta": se, "beta_lo": blo, "beta_hi": bhi,
            "eta_lo": eta * math.exp(-Z90 * se / eta), "eta_hi": eta * math.exp(Z90 * se / eta),
            "mttf": eta * math.gamma(1 + 1 / beta), "b10": b10,
            "b10_lo": b10 * math.exp(-Z90 * math.sqrt(vl)), "b10_hi": b10 * math.exp(Z90 * math.sqrt(vl)),
            "R": R, "pattern": pattern}


def johnson_bernard(fails, susp):
    items = sorted([(t, 0) for t in fails] + [(t, 1) for t in susp])   # tie: failure (0) before suspension (1)
    n, prev, ranks, fs = len(items), 0.0, [], []
    for i, (_, is_susp) in enumerate(items):
        if is_susp:
            continue
        prev += (n + 1 - prev) / (1 + (n - i))
        ranks.append(prev)
        fs.append((prev - 0.3) / (n + 0.4))
    return ranks, fs


WB = {c["id"]: c for c in block(REPORTS, "report-weibull-cases")}


@pytest.mark.parametrize("cid", [k for k, c in WB.items() if c["expected"].get("beta") is not None])
def test_weibull_mle_matches_spec(cid):
    c = WB[cid]
    exp = c["expected"]
    fails, susp = c["failures"], c["suspensions"]
    assert exp["n"] == len(fails) + len(susp) and exp["r"] == len(fails)
    assert exp["low_confidence"] is (3 <= len(fails) < 10)
    assert close(exp["simple_mttf"], sum(fails) / len(fails))
    got = weibull_oracle(fails, susp)
    for k, v in exp.items():
        if k in got and k not in ("R", "pattern"):
            tol = 1e-6 if abs(v) < 1e4 else 0.0
            assert math.isclose(got[k], v, rel_tol=1e-5 if k.startswith(("se_", "beta_", "eta_", "b10_")) else 1e-6,
                                abs_tol=max(tol, 0.01 if abs(v) >= 1e4 else 1e-6)), (k, got[k], v)
    assert got["pattern"] == exp["pattern"]
    # the spec's score equation g(β) is ~0 at the documented β (cross-check of the formula as written)
    beta, alls = exp["beta"], fails + susp
    s = max(alls)
    g = (sum((t / s) ** beta * math.log(t) for t in alls) / sum((t / s) ** beta for t in alls)
         - 1 / beta - sum(math.log(t) for t in fails) / len(fails))
    assert abs(g) < 1e-6
    eta = (sum((t / s) ** beta for t in alls) / len(fails)) ** (1 / beta) * s
    assert math.isclose(eta, exp["eta"], rel_tol=1e-6)
    if "nominal_life" in c:
        assert close(exp["b10_over_nominal"], exp["b10"] / c["nominal_life"])
    for cond in c.get("conditional", []):
        a, a2 = cond["age"], cond["age"] + cond["rate_per_day"] * cond["days"]
        assert close(1 - got["R"](a2) / got["R"](a), cond["p_fail"], abs_=5e-6)
    if "johnson_ranks" in exp:
        ranks, fs = johnson_bernard(fails, susp)
        assert all(close(x, y) for x, y in zip(ranks, exp["johnson_ranks"], strict=True))
        assert all(close(x, y) for x, y in zip(fs, exp["bernard_f"], strict=True))


def test_weibull_spec_hessian_formulas():
    """The analytic Hessian written in reports.md §2.3 equals the finite-difference one at the MLE."""
    c = WB["weibull_cylinders"]
    fails, susp = c["failures"], c["suspensions"]
    alls, r = fails + susp, len(fails)
    o = weibull_oracle(fails, susp)
    b, e = o["beta"], o["eta"]
    z = [(t / e) ** b for t in alls]
    lg = [math.log(t / e) for t in alls]
    hbb = -r / b ** 2 - sum(zi * li * li for zi, li in zip(z, lg))
    hee = r * b / e ** 2 - (b / e ** 2) * sum(z) - (b ** 2 / e ** 2) * sum(z)
    hbe = -r / e + sum(z) / e + (b / e) * sum(zi * li for zi, li in zip(z, lg))
    det = hbb * hee - hbe * hbe
    assert math.isclose(math.sqrt(-hee / det), o["se_beta"], rel_tol=1e-4)
    assert math.isclose(math.sqrt(-hbb / det), o["se_eta"], rel_tol=1e-4)


def test_weibull_insufficient_failures():
    c = WB["weibull_insufficient"]
    assert len(c["failures"]) < 3
    assert c["expected"]["beta"] is None and c["expected"]["reason"] == "insufficient_failures"
    assert close(c["expected"]["simple_mttf"], sum(c["failures"]) / len(c["failures"]))


# ── 3. reports.md: scheduling ─────────────────────────────────────────────

def _t(s):
    h, m = map(int, s.split(":"))
    return time(h, m)


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def schedule_oracle(c):
    tz = ZoneInfo(c["tz"])
    settle = timedelta(minutes=c["settle_min"])
    if c["report"] == "shift":
        d, s0, s1 = c["shift_instance"]
        day = date.fromisoformat(d)
        end_day = day + timedelta(days=1) if _t(s1) <= _t(s0) else day
        ready = datetime.combine(end_day, _t(s1), tz) + settle
        return ready, ready
    first, last = (date.fromisoformat(x) for x in c["period"])
    ends = []
    d = first
    while d <= last:
        if not c["shifts"]:
            ends.append(datetime.combine(d + timedelta(days=1), time(0), tz))
        elif d.weekday() in c["weekdays"]:
            for s0, s1 in c["shifts"]:
                e_day = d + timedelta(days=1) if _t(s1) <= _t(s0) else d
                ends.append(datetime.combine(e_day, _t(s1), tz))
        d += timedelta(days=1)
    ready = max(ends) + settle
    deliver = datetime.combine(last + timedelta(days=1), _t(c["deliver_at_local"]), tz)
    return ready, max(ready, deliver)


@pytest.mark.parametrize("case", block(REPORTS, "report-schedule-cases"), ids=lambda c: c["id"])
def test_schedule_cases(case):
    ready, send = schedule_oracle(case)
    assert ready == _utc(case["expected"]["ready_at"]), (ready.astimezone(timezone.utc), case["expected"])
    assert send == _utc(case["expected"]["send_at"])
    assert send >= ready


# ── 4. reports.md: displayed values and catalog ───────────────────────────

KPI_CASES = {c["id"]: c for c in block(KPIS, "kpi-test-cases")}


def pct(x: float) -> str:
    """S3-5: 1 decimal, half-up of the raw value, pt-BR."""
    v = math.floor(x * 1000 + 0.5) / 10
    return f"{v:.1f}".replace(".", ",") + " %"


def num1(x: float) -> str:
    v = math.floor(x * 10 + 0.5) / 10
    return f"{v:.1f}".replace(".", ",")


def test_report_display_values_come_from_kpi_cases():
    cases = {c["id"]: c for c in block(REPORTS, "report-kpi-cases")}
    d = cases["daily_production_e2"]
    e = KPI_CASES[d["from_kpi_case"]]["expected"]
    disp = d["expected_display"]
    for k in ("oee", "availability", "performance", "quality"):
        assert pct(e[k]) == disp[k], k
    assert [pct(x) for x in e["shift_oee"]] == disp["shift_oee"]
    assert pct(e["wrong_mean_of_shift_oee"]) == d["must_not_display"]["oee"] != disp["oee"]

    m = cases["monthly_maintenance_m"]
    src = {k: KPI_CASES[v]["expected"] for k, v in m["from_kpi_cases"].items()}
    md = m["expected_display"]
    assert pct(src["pmp"]["pmp"]) == md["pmp"]
    assert pct(src["schedule_compliance"]["compliance"]) == md["schedule_compliance"]
    assert num1(src["backlog"]["weeks"]) + " semanas" == md["backlog"]
    assert num1(src["mtbf"]["mtbf_h"]) + " h" == md["mtbf"]
    assert num1(src["mttr"]["mttr_min"]) + " min" == md["mttr"]
    assert pct(src["ao"]["ao"]) == md["ao"]
    # reference marks of §1.4: PMP ≥ 0.85, compliance ≥ 0.90, backlog 2–4 weeks
    assert m["flags"]["pmp"] == ("green" if src["pmp"]["pmp"] >= 0.85 else "amber")
    assert m["flags"]["schedule_compliance"] == ("green" if src["schedule_compliance"]["compliance"] >= 0.90 else "amber")
    assert m["flags"]["backlog"] == ("green" if 2 <= src["backlog"]["weeks"] <= 4 else "amber")


def test_catalog_roles_and_formats():
    roles = set(re.findall(r'^\s+(\w+)\s*=\s*"\w+"', (ROOT / "server" / "src" / "models" / "user.py")
                           .read_text(encoding="utf-8"), re.M))
    assert {"operator", "manager", "production_supervisor", "maintenance_planner"} <= roles
    cat = block(REPORTS, "report-catalog")
    assert {c["code"] for c in cat} == {"daily_production", "shift", "weekly_oee", "monthly_maintenance",
                                        "reliability_life"}
    for c in cat:
        assert set(c["formats"]) <= {"pdf", "xlsx"} and "pdf" in c["formats"]
        assert set(c["recipients"]) <= roles, c["code"]
        assert "operator" not in c["recipients"] and "technician" not in c["recipients"]
        # every catalog code is described in the catalog table of the document
        assert f"| `{c['code']}` |" in REPORTS
