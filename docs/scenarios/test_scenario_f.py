"""Golden scenario F (docs/scenarios/scenario_f_predictive.json) — oracle for the Sprint 5 predictive module
(condition features, baseline, EWMA/CUSUM/rolling robust z, condition rules, alert lifecycle, condition-based
RUL, evaluation metrics, PMP with predictive work orders).

The server has no predictive module yet: this file recomputes every `expected` value from the scenario's inputs
following docs/predictive.md (rules P-1 … P-12). When Sprint 5 lands, its API/engine tests load the same JSON
(see README.md, "Cenário F").

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
F = json.loads((HERE / "scenario_f_predictive.json").read_text(encoding="utf-8"))
X = F["expected"]
TZ = ZoneInfo(F["plant"]["timezone"])
DET = F["detectors"]
EPS = 1e-9


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


# ── generic, spec-following oracle (docs/predictive.md) ───────────────────────────────

def mad(values: list[float]) -> float:
    m = median(values)
    return median([abs(v - m) for v in values])


def robust_sigma(values: list[float], floor: float) -> float:
    """P-2: σ = max(1.4826 · MAD, floor)."""
    return max(1.4826 * mad(values), floor)


def ols(us: list[float], xs: list[float]) -> tuple[float, float, float]:
    """Least squares x = a + b·u → (a, b, se_b)  (P-5)."""
    n = len(us)
    mu, mx = sum(us) / n, sum(xs) / n
    suu = sum((u - mu) ** 2 for u in us)
    b = sum((u - mu) * (x - mx) for u, x in zip(us, xs)) / suu
    a = mx - b * mu
    sse = sum((x - (a + b * u)) ** 2 for u, x in zip(us, xs))
    se = math.sqrt(sse / (n - 2) / suu)
    return a, b, se


class Monitor:
    """One (position, feature) monitor following P-1 … P-8. `windows` are dicts with
    i, end (datetime), x (float|None = invalid window), u (usage at window end), day_usage (usage in that
    local day, for the rate) — produced by the scenario adapters below."""

    def __init__(self, cfg: dict, windows: list[dict], usage_by_day: dict[date, float],
                 actions: list[dict] | None = None):
        self.cfg = cfg
        self.w = windows
        self.usage_by_day = usage_by_day
        self.actions = {a["after_window"]: a for a in (actions or [])}
        self.points: list[dict] = []
        self.alerts: list[dict] = []
        self.baselines: list[dict] = []

    # P-7: rate of usage per calendar day over the 14 × 24 h ending at the evaluation instant (kpis.md §4.2,
    # C17), plus the band from the two 7-day halves. Daily usage is spread uniformly over its local day.
    def usage_between(self, a: datetime, b: datetime) -> float:
        tot, d = 0.0, a.astimezone(TZ).date()
        while True:
            ds = datetime(d.year, d.month, d.day, tzinfo=TZ)
            de = ds + timedelta(days=1)
            if ds >= b:
                return tot
            ov = (min(de, b) - max(ds, a)).total_seconds()
            if ov > 0:
                tot += self.usage_by_day.get(d, 0.0) * ov / 86400
            d += timedelta(days=1)

    def rates(self, end: datetime) -> tuple[float, float, float]:
        mid, start = end - timedelta(days=7), end - timedelta(days=14)
        r = self.usage_between(start, end) / 14
        w1, w2 = self.usage_between(start, mid) / 7, self.usage_between(mid, end) / 7
        return r, min(w1, w2), max(w1, w2)

    def run(self) -> None:
        c = self.cfg
        lam, L, k, h, clip = DET["ewma_lambda"], DET["ewma_L"], DET["cusum_k"], DET["cusum_h"], DET["z_clip"]
        ewma_limit = L * math.sqrt(lam / (2 - lam))
        warm = c["warmup_windows"]
        nbase = DET["baseline_min_windows"]
        state = "learning"
        learn: list[dict] = []
        since_reset: list[dict] = []          # valid windows since the last baseline reset (after warm-up)
        skip = warm
        m0 = sig = None
        E = S = 0.0
        streak = {r["id"]: 0 for r in c["rules"]}
        rate_streak = 0
        open_alert = None
        for wi in self.w:
            if wi["x"] is None:                 # P-1: invalid window — neither counts nor breaks streaks
                self.points.append({"i": wi["i"], "valid": False})
                continue
            if skip > 0:
                skip -= 1
                self.points.append({"i": wi["i"], "valid": True, "phase": "warmup"})
                continue
            x = wi["x"]
            pt = {"i": wi["i"], "valid": True, "x": x}
            trig: list[str] = []
            # absolute threshold rules are active even while learning (P-3)
            for r in c["rules"]:
                if r["type"] == "threshold":
                    hit = x > r["value"]
                    streak[r["id"]] = streak[r["id"]] + 1 if hit else 0
                    if streak[r["id"]] >= r["sustained_for"]:
                        trig.append(r["id"])
            if state == "learning":
                learn.append(wi)
                since_reset.append(wi)
                pt["phase"] = "baseline"
                if len(learn) == nbase:
                    vals = [q["x"] for q in learn]
                    m0 = median(vals)
                    floor = DET["sigma_floor_frac"] * abs(m0)
                    sig = robust_sigma(vals, floor)
                    self.baselines.append({"from_i": learn[0]["i"], "to_i": learn[-1]["i"], "m0": m0,
                                           "mad": mad(vals), "sigma": sig, "sigma_floor": floor,
                                           "established_at": iso(wi["end"])})
                    state = "monitoring"
                    E = S = 0.0
                self.points.append(pt)
                if trig and open_alert is None:
                    open_alert = self._open(wi, trig, "warning")
                continue
            # monitoring
            z = max(-clip, min(clip, (x - m0) / sig))
            E = lam * z + (1 - lam) * E
            S = max(0.0, S + z - k)
            pt.update(z=z, ewma=E, cusum=S)
            if E > ewma_limit + EPS:
                trig.append("EWMA")
            if S > h + EPS:
                trig.append("CUSUM")
            # rolling robust z over the previous N valid windows since the last reset (P-4)
            prev = [q["x"] for q in since_reset[-DET["rolling_window"]:]]
            if len(prev) == DET["rolling_window"]:
                rs = max(1.4826 * mad(prev), DET["sigma_floor_frac"] * abs(m0))
                rz = (x - median(prev)) / rs
                pt["rolling_z"] = rz
                pt["anomaly"] = rz > DET["rolling_z_limit"]
            since_reset.append(wi)
            recent = [p for p in self.points if p.get("valid") and "rolling_z" in p][-(DET["anomaly_of"] - 1):]
            if pt.get("anomaly") and sum(1 for p in recent if p.get("anomaly")) + 1 >= DET["anomaly_min"]:
                trig.append("ROLLING_Z")
            for r in c["rules"]:
                if r["type"] == "pct_of_baseline":
                    hit = x > r["value"] * m0
                elif r["type"] == "rate_of_change":
                    last = since_reset[-DET["trend_windows"]:]
                    hit = False
                    if len(last) == DET["trend_windows"]:
                        _, b, _ = ols([q["u"] for q in last], [q["x"] for q in last])
                        pt["slope"] = b
                        hit = b > r["value"]
                else:
                    continue
                streak[r["id"]] = streak[r["id"]] + 1 if hit else 0
                if streak[r["id"]] >= r["sustained_for"]:
                    trig.append(r["id"])
            # P-6 condition RUL
            rul = self.rul(wi, since_reset, m0)
            pt["rul"] = rul
            if rul.get("rul_days") is not None and rul["rul_days"] <= DET["rul_warning_days"]:
                trig.append("RUL_WARNING")
            if rul.get("rul_days_low") is not None and rul["rul_days_low"] <= DET["rul_critical_days"]:
                trig.append("RUL_CRITICAL")
            sev = self.severity(trig)
            if trig:
                pt["triggers"] = trig
                if open_alert is None:
                    open_alert = self._open(wi, trig, sev)
                else:
                    self._escalate(open_alert, wi, trig, sev)
            self.points.append(pt)
            act = self.actions.get(wi["i"])
            if act and open_alert is not None:
                open_alert["state"] = "closed"
                open_alert["resolution"] = act["resolution"]
                open_alert["closed_at"] = iso(wi["end"])
                open_alert = None
                if act["resolution"] == "rebaseline":   # P-2: new baseline from the next N valid windows
                    state, learn, since_reset, skip = "learning", [], [], 0
                    E = S = 0.0
                    streak = {r["id"]: 0 for r in c["rules"]}

    def severity(self, trig: list[str]) -> str:
        sev = "watch"
        for t in trig:
            if t == "RUL_CRITICAL":
                return "critical"
            if t == "RUL_WARNING":
                sev = "warning"
            rule = next((r for r in self.cfg["rules"] if r["id"] == t), None)
            if rule is not None:
                if rule["severity"] == "critical":
                    return "critical"
                sev = "warning"
        return sev

    def _open(self, wi, trig, sev) -> dict:
        a = {"opened_at": iso(wi["end"]), "opened_window": wi["i"], "first_triggers": sorted(trig),
             "severity": sev, "escalations": {sev: iso(wi["end"])}, "escalation_windows": {sev: wi["i"]},
             "state": "open", "triggers_seen": {t: wi["i"] for t in trig}}
        self.alerts.append(a)
        return a

    def _escalate(self, a, wi, trig, sev) -> None:
        for t in trig:
            a["triggers_seen"].setdefault(t, wi["i"])
        order = ["watch", "warning", "critical"]
        if order.index(sev) > order.index(a["severity"]):   # P-8: never de-escalates automatically
            a["severity"] = sev
            a["escalations"][sev] = iso(wi["end"])
            a["escalation_windows"][sev] = wi["i"]

    def rul(self, wi, since_reset, m0) -> dict:
        c = self.cfg
        lf = c["failure_limit"]["value"] * (m0 if c["failure_limit"]["relative_to_baseline"] else 1.0)
        last = since_reset[-DET["trend_windows"]:]
        if len(last) < DET["trend_windows"]:
            return {"rul_days": None, "reason": "insufficient_data", "failure_limit": lf}
        a, b, se = ols([q["u"] for q in last], [q["x"] for q in last])
        u_now = last[-1]["u"]
        xhat = a + b * u_now
        out = {"failure_limit": lf, "slope": b, "slope_se": se, "x_fit": xhat}
        if xhat >= lf:
            return {**out, "rul_usage": 0.0, "rul_days": 0, "rul_days_low": 0, "rul_days_high": 0}
        if b <= 0 or b / se <= DET["trend_t_min"]:
            return {**out, "rul_days": None, "reason": "no_significant_trend"}
        rem = lf - xhat
        ru, rl, rh = rem / b, rem / (b + 2 * se), rem / (b - 2 * se)
        today = wi["end"].astimezone(TZ).date()
        r, r_lo, r_hi = self.rates(wi["end"])
        d = math.ceil(ru / r - EPS)
        d_lo = math.floor(rl / r_hi + EPS)
        d_hi = math.ceil(rh / r_lo - EPS)
        return {**out, "rul_usage": ru, "rul_usage_low": rl, "rul_usage_high": rh, "rate_per_day": r,
                "rate_low": r_lo, "rate_high": r_hi, "rul_days": d, "rul_days_low": d_lo, "rul_days_high": d_hi,
                "today": today.isoformat(), "projected_date": (today + timedelta(days=d)).isoformat(),
                "projected_date_low": (today + timedelta(days=d_lo)).isoformat(),
                "projected_date_high": (today + timedelta(days=d_hi)).isoformat()}


# ── scenario adapters ─────────────────────────────────────────────────────────────────

def f1_series() -> tuple[list[dict], dict[date, float]]:
    c = F["cases"]["F1"]
    g = c["generator"]
    t0 = ts(c["installed_at"])
    out = []
    for row in c["windows"]:
        i = row["i"]
        out.append({"i": i, "x": row["x"], "u": i * c["window"]["cycles"],
                    "end": t0 + timedelta(hours=g["hours_per_window"] * i)})
    # usage per local day: 20 000 cycles/day, continuous production (assumed before install too: the position
    # kept producing with the previous cylinder; the rate is the position's, kpis.md §4.2)
    by_day: dict[date, float] = {}
    d0 = t0.astimezone(TZ).date() - timedelta(days=30)
    for k in range(120):
        by_day[d0 + timedelta(days=k)] = g["cycles_per_day"]
    return out, by_day


def f2_series() -> tuple[list[dict], dict[date, float]]:
    c = F["cases"]["F2"]
    d1 = date.fromisoformat(c["first_day"])
    out, by_day, u = [], {}, 0.0
    for k in range(1, 31):                      # 30 days before the maintenance at the same rhythm
        by_day[d1 - timedelta(days=k)] = c["generator"]["running_hours_per_day"]
    for row in c["windows"]:
        d = d1 + timedelta(days=row["i"] - 1)
        u += row["running_h"]
        by_day[d] = row["running_h"]
        end = datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(days=1)
        out.append({"i": row["i"], "x": row["x"], "u": u, "end": end, "day": d})
    return out, by_day


def run(case: str) -> Monitor:
    series, by_day = f1_series() if case == "F1" else f2_series()
    c = F["cases"][case]
    m = Monitor(c, series, by_day, c.get("actions"))
    m.run()
    return m


M1, M2 = run("F1"), run("F2")


# ── tests: inputs are what the generator says ─────────────────────────────────────────

def test_f1_series_matches_generator():
    c = F["cases"]["F1"]
    g = c["generator"]
    for row in c["windows"]:
        i = row["i"]
        x = g["base"] + g["noise"][i % 10] + g["drift_per_window"] * max(0, i - g["drift_after_window"])
        if str(i) in g["overrides"]:
            x = g["overrides"][str(i)]
        assert close(row["x"], round(x, 3)), i
    assert [r["i"] for r in c["windows"]] == list(range(1, g["last_window"] + 1))


def test_f2_series_matches_generator():
    c = F["cases"]["F2"]
    g = c["generator"]
    for row in c["windows"]:
        d = row["i"]
        if d in g["invalid_days"]:
            assert row["x"] is None and row["running_h"] == 0
            continue
        x = g["base"] + g["noise"][d % 10] + g["drift_per_day"] * min(max(0, d - g["drift_after_day"]),
                                                                      g["drift_days"])
        assert close(row["x"], round(x, 4)), d
        assert row["running_h"] == g["running_hours_per_day"]


# ── hand-checkable constants (README "Cenário F") ─────────────────────────────────────

def test_hand_constants():
    lam, L = DET["ewma_lambda"], DET["ewma_L"]
    assert close(L * math.sqrt(lam / (2 - lam)), 1.0)          # 3·√(0,2/1,8) = 1,0
    b1 = M1.baselines[0]
    assert (b1["from_i"], b1["to_i"], b1["m0"], b1["mad"]) == (2, 21, 200.0, 1.0)
    assert close(b1["sigma"], 2.0) and close(b1["sigma_floor"], 2.0)   # floor 1 % de 200 > 1,4826
    b2 = M2.baselines[0]
    assert (b2["from_i"], b2["to_i"]) == (2, 21) and close(b2["m0"], 12.0) and close(b2["mad"], 0.1)
    assert close(b2["sigma"], 0.14826)                                 # 1,4826·0,1 > floor 0,12


def test_spike_is_anomaly_point_but_not_alert():
    p = next(p for p in M1.points if p["i"] == 25)
    assert p["anomaly"] and close(p["rolling_z"], 6.0)                 # (212 − 200)/2
    assert close(p["z"], 4.0)                                          # recortado em ±4 antes de EWMA/CUSUM
    assert p["ewma"] < 1.0 and p["cusum"] < 5.0
    assert "triggers" not in p


# ── tests: expected block matches the oracle ──────────────────────────────────────────

def _alert_view(a: dict) -> dict:
    return {k: a[k] for k in ("opened_at", "opened_window", "first_triggers", "severity", "escalations",
                              "escalation_windows", "state", "triggers_seen")} | (
        {"resolution": a["resolution"], "closed_at": a["closed_at"]} if a["state"] == "closed" else {})


def test_f1_baseline_and_alert():
    e = X["F1"]
    b = M1.baselines[0]
    assert b["established_at"] == e["baseline"]["established_at"]
    assert close(b["m0"], e["baseline"]["m0"]) and close(b["sigma"], e["baseline"]["sigma"])
    assert len(M1.alerts) == 1
    assert _alert_view(M1.alerts[0]) == e["alert"]


def test_f1_detector_trace():
    exp = {r["i"]: r for r in X["F1"]["trace"]}
    for p in M1.points:
        if p["i"] in exp:
            r = exp[p["i"]]
            for k in ("x", "z", "ewma", "cusum", "rolling_z", "slope"):
                if k in r:
                    assert close(p.get(k), r[k]), (p["i"], k, p.get(k), r[k])
            assert sorted(p.get("triggers", [])) == r["triggers"], p["i"]


def test_f1_lead_times():
    e = X["F1"]
    failed = ts(F["cases"]["F1"]["failure"]["failed_at"])
    a = M1.alerts[0]
    for lvl, hours in e["lead_time_h"].items():
        assert (failed - ts(a["escalations"][lvl])).total_seconds() / 3600 == hours, lvl
    # which rules tripped (never before the alert), and the absolute threshold is the last to know
    seen = a["triggers_seen"]
    assert seen["R-F1-THR"] > seen["R-F1-PCT"] > a["opened_window"]


@pytest.mark.parametrize("cp", X["F1"]["rul_checkpoints"], ids=lambda c: f"w{c['window']}")
def test_f1_rul_checkpoints(cp):
    p = next(p for p in M1.points if p["i"] == cp["window"])
    r = p["rul"]
    for k in ("slope", "slope_se", "x_fit", "rul_usage", "rul_usage_low", "rul_usage_high", "rate_per_day"):
        assert close(r[k], cp[k]), (k, r[k], cp[k])
    for k in ("today", "rul_days", "rul_days_low", "rul_days_high", "projected_date", "projected_date_low",
              "projected_date_high"):
        assert r[k] == cp[k], (k, r[k], cp[k])
    # ground truth from the virtual plant: the band must contain the true failure date
    assert cp["projected_date_low"] <= cp["true_failure_date"] <= cp["projected_date_high"]
    true_rem = (F["cases"]["F1"]["failure"]["window"] - cp["window"]) * F["cases"]["F1"]["window"]["cycles"]
    assert true_rem == cp["true_rul_usage"]


def test_f2_false_alarm_and_rebaseline():
    e = X["F2"]
    assert len(M2.alerts) == 1
    assert _alert_view(M2.alerts[0]) == e["alert"]
    assert M2.alerts[0]["severity"] == "watch"            # nenhuma regra de engenharia nem RUL ≤ 30 d
    assert [{k: b[k] for k in ("from_i", "to_i", "established_at")} for b in M2.baselines] == e["baselines"]
    assert all(close(M2.baselines[i]["m0"], e["baselines_m0"][i]) for i in range(len(M2.baselines)))
    assert all(close(M2.baselines[i]["sigma"], e["baselines_sigma"][i]) for i in range(len(M2.baselines)))
    after = [p for p in M2.points if p["i"] > e["baselines"][1]["to_i"]]
    assert after and not any(p.get("triggers") for p in after)
    hol = next(p for p in M2.points if p["i"] == F["cases"]["F2"]["generator"]["invalid_days"][0])
    assert hol == {"i": hol["i"], "valid": False}


@pytest.mark.parametrize("cp", X["F2"]["rul_checkpoints"], ids=lambda c: f"d{c['window']}")
def test_f2_rul_checkpoints(cp):
    p = next(p for p in M2.points if p["i"] == cp["window"])
    r = p["rul"]
    for k, v in cp.items():
        if k == "window":
            continue
        if isinstance(v, float):
            assert close(r.get(k), v), (k, r.get(k), v)
        else:
            assert r.get(k) == v, (k, r.get(k), v)


def test_evaluation_metrics():
    """P-10: alert outcomes against the virtual plant's ground truth."""
    e = X["evaluation"]
    H = timedelta(days=DET["evaluation_horizon_days"])
    failures = {"F1": [ts(F["cases"]["F1"]["failure"]["failed_at"])], "F2": []}
    tp = fp = 0
    tp_w = fp_w = 0
    for case, m in (("F1", M1), ("F2", M2)):
        for a in m.alerts:
            o = ts(a["opened_at"])
            hit = any(o < f <= o + H for f in failures[case])
            tp += hit
            fp += not hit
            if "warning" in a["escalations"]:
                w = ts(a["escalations"]["warning"])
                h2 = any(w < f <= w + H for f in failures[case])
                tp_w += h2
                fp_w += not h2
    assert (tp, fp, tp_w, fp_w) == (e["true_positives"], e["false_alarms"], e["true_positives_warning"],
                                    e["false_alarms_warning"])
    # detected failures: an alert opened at least min_lead before the failure
    min_lead = timedelta(hours=DET["min_useful_lead_h"])
    det = sum(1 for f in failures["F1"] if any(ts(a["opened_at"]) <= f - min_lead for a in M1.alerts))
    assert det == e["failures_detected"] and e["failures_total"] == 1
    assert close(e["precision"], tp / (tp + fp)) and close(e["precision_warning"], tp_w / (tp_w + fp_w))
    # monitored time = after a baseline is established, until failure / end of data, excluding re-learning
    f1_days = (ts(F["cases"]["F1"]["failure"]["failed_at"]) - ts(M1.baselines[0]["established_at"])
               ).total_seconds() / 86400
    relearn = (ts(M2.baselines[1]["established_at"]) - ts(M2.alerts[0]["closed_at"])).total_seconds() / 86400
    f2_days = (M2.w[-1]["end"] - ts(M2.baselines[0]["established_at"])).total_seconds() / 86400 - relearn
    assert (f1_days, f2_days) == (e["monitored_days"]["F1"], e["monitored_days"]["F2"])
    far = fp / (f1_days + f2_days) * 365
    assert close(far, e["false_alarms_per_position_year"])
    far_w = fp_w / (f1_days + f2_days) * 365
    assert close(far_w, e["false_alarms_per_position_year_warning"])


def test_pmp_with_predictive():
    """P-9 / kpis.md §3.1: predictive WOs are planned, including the ones that found no defect;
    a predictive WO cancelled as superseded by the failure has no hours and does not enter."""
    p = F["pmp_with_predictive"]
    planned = total = no_def = 0.0
    for wo in p["work_orders"]:
        if wo["state"] not in ("completed", "closed"):
            continue
        total += wo["labor_hours"]
        if wo["type"] in ("preventive", "predictive", "improvement") or (
                wo["type"] == "corrective" and wo.get("urgency") == "deferred"):
            planned += wo["labor_hours"]
        if wo["type"] == "predictive" and wo.get("finding") == "no_defect":
            no_def += wo["labor_hours"]
    e = p["expected"]
    assert close(planned / total, e["pmp"]) and (planned, total) == (e["planned_hours"], e["total_hours"])
    assert no_def == e["predictive_no_defect_hours"]
    assert p["wrong_answers"]["pmp_excluding_no_defect_predictive"] != e["pmp"]


def test_production_plan_projection():
    """P-7: with a production plan, the date is the first day whose cumulative planned usage ≥ remaining usage."""
    ex = F["production_plan_example"]
    e = ex["expected"]
    p = next(p for p in M1.points if p["i"] == ex["window"])["rul"]
    assert p["today"] == e["today"] and date.fromisoformat(e["today"]).weekday() == 4      # sexta
    assert close(p["rul_usage"], e["rul_usage"]) and p["projected_date"] == e["projected_date_history_rate"]
    d, acc = date.fromisoformat(p["today"]), 0.0
    for _ in range(ex["plan"]["horizon_days"]):
        d += timedelta(days=1)
        acc += ex["plan"]["weekday_usage"] if d.weekday() < 5 else ex["plan"]["weekend_usage"]
        if acc >= p["rul_usage"] - EPS:
            break
    assert d.isoformat() == e["projected_date"]


def test_detectors_match_documented_defaults():
    """docs/predictive.md `predictive-defaults` block == the scenario's detector parameters (except the
    declared overrides of window size and warm-up)."""
    text = (HERE.parent / "predictive.md").read_text(encoding="utf-8")
    block = text.split("```json predictive-defaults", 1)[1].split("```", 1)[0]
    d = json.loads(block)
    for k, v in DET.items():
        assert d[k] == v, k
    assert d["stroke_time_failure_limit_of_baseline"] == F["cases"]["F1"]["failure_limit"]["value"]
    for case, ov in F["detector_overrides_vs_defaults"].items():
        assert F["cases"][case]["warmup_windows"] == ov["warmup_windows"]
    assert F["cases"]["F1"]["window"]["cycles"] == F["detector_overrides_vs_defaults"]["F1"]["window"]["cycles"]
    assert all(r["sustained_for"] == d["sustained_for"] for c in F["cases"].values() for r in c["rules"])
