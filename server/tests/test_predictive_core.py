"""Pure predictive core (services/predictive/core.py) against golden scenario F and docs/predictive.md.

No database: these tests prove the fold reproduces every `expected` number of scenario F (P-2 … P-10); the API
tests (test_predictive_api.py) prove the same through the database, the Influx pipeline and the HTTP API."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from predictive_helpers import (DET, F, ROOT, TZ, X, close, f1_hourly_usage, f1_reset_at, f1_windows,
                                f2_close_at, f2_hourly_usage, f2_reset_at, f2_windows, rules_of, ts)
from src.services.predictive import core
from src.services.predictive.defaults import DEFAULTS


def _cfg(case: str) -> core.MonitorCfg:
    c = F["cases"][case]
    fl = c["failure_limit"]
    return core.MonitorCfg(feature=c["feature"], direction=c["direction"], warmup_windows=c["warmup_windows"],
                           failure_limit=fl["value"], failure_limit_relative=fl["relative_to_baseline"],
                           rules=[core.RuleCfg(code=r["id"], type=r["type"], value=r["value"],
                                               sustained_for=r["sustained_for"], severity=r["severity"])
                                  for r in rules_of(case)],
                           label="Cilindro B3" if case == "F1" else "Motor B", unit=c["unit"])


def _wins(rows):
    return [core.WindowIn(i=w["i"], end=w["end"], x=w["x"], u=w["u"], start=w["start"]) for w in rows]


def run_f1():
    cfg = _cfg("F1")
    segs = core.fold_monitor(cfg, _wins(f1_windows()), [core.Reset(f1_reset_at(), True)], TZ,
                             rate_fn=core.make_rate_fn(f1_hourly_usage()))
    return cfg, segs


def run_f2():
    cfg = _cfg("F2")
    close_at = f2_close_at()
    first = core.fold_monitor(cfg, _wins(f2_windows()), [core.Reset(f2_reset_at(), True)], TZ,
                              rate_fn=core.make_rate_fn(f2_hourly_usage()))
    key = first[0][1].alerts[0]["key"]
    ev = [core.ExtEvent(at=close_at, alert_key=key, action="close", resolution="rebaseline")]
    segs = core.fold_monitor(cfg, _wins(f2_windows()),
                             [core.Reset(f2_reset_at(), True), core.Reset(close_at, False, "rebaseline")], TZ,
                             rate_fn=core.make_rate_fn(f2_hourly_usage()), events=ev)
    return cfg, segs


def _alert_view(a: dict) -> dict:
    v = {k: a[k] for k in ("opened_at", "opened_window", "first_triggers", "severity", "escalations",
                           "escalation_windows", "state", "triggers_seen")}
    if a["state"] == "closed":
        v.update(resolution=a["resolution"], closed_at=a["closed_at"])
    return v


def test_defaults_match_docs():
    text = (ROOT / "docs/predictive.md").read_text(encoding="utf-8")
    block = json.loads(text.split("```json predictive-defaults", 1)[1].split("```", 1)[0])
    assert block == DEFAULTS
    for k, v in DET.items():
        assert DEFAULTS[k] == v, k


def test_f1_baseline_alert_and_lead_times():
    _, segs = run_f1()
    (_, res), = segs
    e = X["F1"]
    b = res.baseline
    for k in ("from_i", "to_i", "established_at"):
        assert b[k] == e["baseline"][k]
    for k in ("m0", "mad", "sigma", "sigma_floor"):
        assert close(b[k], e["baseline"][k]), k
    assert len(res.alerts) == 1
    a = res.alerts[0]
    assert _alert_view(a) == e["alert"]
    failed = ts(F["cases"]["F1"]["failure"]["failed_at"])
    for lvl, hours in e["lead_time_h"].items():
        assert (failed - ts(a["escalations"][lvl])).total_seconds() / 3600 == hours
    for code, hours in e["lead_time_by_rule_h"].items():
        first = next(p for p in res.points if p["i"] == a["triggers_seen"][code])
        assert (failed - ts(first["end"])).total_seconds() / 3600 == hours, code


def test_f1_trace_and_spike():
    _, ((_, res),) = run_f1()
    pts = {p["i"]: p for p in res.points}
    for r in X["F1"]["trace"]:
        p = pts[r["i"]]
        for k in ("x", "z", "ewma", "cusum", "rolling_z", "slope"):
            assert close(p.get(k), r[k]), (r["i"], k, p.get(k), r[k])
        assert p.get("triggers", []) == r["triggers"], r["i"]
    spike = pts[25]
    assert spike["anomaly"] and close(spike["rolling_z"], 6.0) and close(spike["z"], 4.0)
    assert "triggers" not in spike
    assert pts[1]["phase"] == "warmup" and pts[2]["phase"] == "baseline" and pts[22]["phase"] == "monitoring"


@pytest.mark.parametrize("cp", X["F1"]["rul_checkpoints"], ids=lambda c: f"w{c['window']}")
def test_f1_rul_checkpoints(cp):
    _, ((_, res),) = run_f1()
    r = next(p for p in res.points if p["i"] == cp["window"])["rul"]
    for k in ("slope", "slope_se", "x_fit", "rul_usage", "rul_usage_low", "rul_usage_high", "rate_per_day",
              "rate_low", "rate_high", "failure_limit"):
        assert close(r[k], cp[k]), (k, r[k], cp[k])
    for k in ("today", "rul_days", "rul_days_low", "rul_days_high", "projected_date", "projected_date_low",
              "projected_date_high"):
        assert r[k] == cp[k], (k, r[k], cp[k])
    assert r["projected_date_low"] <= cp["true_failure_date"] <= r["projected_date_high"]


def test_f2_false_alarm_rebaseline_and_holiday():
    _, segs = run_f2()
    e = X["F2"]
    alerts = [a for _, r in segs for a in r.alerts]
    assert len(alerts) == 1
    assert _alert_view(alerts[0]) == e["alert"]
    bases = [r.baseline for _, r in segs]
    assert [{k: b[k] for k in ("from_i", "to_i", "established_at")} for b in bases] == e["baselines"]
    assert all(close(b["m0"], m) for b, m in zip(bases, e["baselines_m0"]))
    assert all(close(b["sigma"], s) for b, s in zip(bases, e["baselines_sigma"]))
    after = [p for p in segs[1][1].points if p["i"] > e["baselines"][1]["to_i"]]
    assert after and not any(p.get("triggers") for p in after)
    hol = next(p for p in segs[0][1].points if p["i"] == 47)
    assert hol["valid"] is False and "ewma" not in hol
    # the holiday neither advances EWMA nor breaks anything: EWMA at 48 continues from 46
    p46 = next(p for p in segs[0][1].points if p["i"] == 46)
    p48 = next(p for p in segs[0][1].points if p["i"] == 48)
    assert close(p48["ewma"], 0.2 * p48["z"] + 0.8 * p46["ewma"])


@pytest.mark.parametrize("cp", X["F2"]["rul_checkpoints"], ids=lambda c: f"d{c['window']}")
def test_f2_rul_checkpoints(cp):
    _, segs = run_f2()
    r = next(p for p in segs[0][1].points if p["i"] == cp["window"])["rul"]
    for k, v in cp.items():
        if k == "window":
            continue
        if isinstance(v, float):
            assert close(r.get(k), v), (k, r.get(k), v)
        else:
            assert r.get(k) == v, (k, r.get(k), v)


def test_fold_is_deterministic():
    a = run_f1()[1][0][1]
    b = run_f1()[1][0][1]
    assert a.points == b.points and a.alerts == b.alerts and a.baseline == b.baseline


def test_production_plan_projection():
    ex = F["production_plan_example"]
    plan = ex["plan"]
    cfg = _cfg("F1")

    def plan_fn(today, remaining, rate):
        days = {today + timedelta(days=k): (plan["weekday_usage"] if (today + timedelta(days=k)).weekday() < 5
                                            else plan["weekend_usage"]) for k in range(1, plan["horizon_days"] + 1)}
        return core.project_with_plan(today, remaining, days, today + timedelta(days=plan["horizon_days"]),
                                      rate)

    segs = core.fold_monitor(cfg, _wins(f1_windows()), [core.Reset(f1_reset_at(), True)], TZ,
                             rate_fn=core.make_rate_fn(f1_hourly_usage()), plan_fn=plan_fn)
    r = next(p for p in segs[0][1].points if p["i"] == ex["window"])["rul"]
    assert r["today"] == ex["expected"]["today"]
    assert close(r["rul_usage"], ex["expected"]["rul_usage"])
    assert r["projected_date"] == ex["expected"]["projected_date"]
    assert r["projected_date_history_rate"] == ex["expected"]["projected_date_history_rate"]
    assert r["rate_source"] == ex["expected"]["rate_source"]


def test_explanation_text_matches_spec_example():
    cfg, ((_, res),) = run_f1()
    p35 = next(p for p in res.points if p["i"] == 35)
    txt = core.explain(cfg, res.alerts[0], p35, res.baseline, TZ, "tempo de curso")
    assert txt.startswith("Cilindro B3 — tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01): +3,25 σ.")
    assert "EWMA 1,34 > 1,00" in txt and "CUSUM 6,0 > 5,0" in txt
    r = p35["rul"]
    assert f"Falha projetada em {r['rul_days']} dias ({r['rul_days_low']}–{r['rul_days_high']}) ao atingir 240 ms." in txt


def test_auto_clear_snooze_and_rule_hysteresis():
    """P-8 auto_cleared after 6 quiet valid windows (nobody acknowledged); P-8 7-day snooze after false_alarm;
    P-3 a rule disarms only after `sustained_for` valid windows without the condition."""
    cfg = core.MonitorCfg(feature="current", warmup_windows=0, failure_limit=None,
                          rules=[core.RuleCfg("THR", "threshold", 15.0, 2, "warning")],
                          # statistical detectors made insensitive: only the rule acts here
                          params={"baseline_min_windows": 3, "baseline_min_span_h": 0, "ewma_L": 1e6,
                                  "cusum_h": 1e6, "rolling_z_limit": 1e6})
    t0 = ts("2027-01-01T00:00:00Z")
    xs = [10, 10, 10, 16, 16, 14, 16, 14, 14, 10, 10, 10, 10, 10, 10, 10, 16, 16]
    wins = [core.WindowIn(i=k + 1, start=t0 + timedelta(hours=8 * k), end=t0 + timedelta(hours=8 * (k + 1)),
                          x=float(x), u=8.0 * (k + 1)) for k, x in enumerate(xs)]
    res = core.fold_segment(cfg, wins, warmup=False, tz=TZ)
    trig = {p["i"]: p.get("triggers", []) for p in res.points}
    assert "THR" in trig[5] and "THR" in trig[6]           # armed at 5; 6 is one miss → still armed
    assert "THR" in trig[7] and "THR" in trig[8]           # 7 hit; 8 first miss
    assert "THR" not in trig[9]                            # second consecutive miss → disarmed
    a = res.alerts[0]
    assert a["opened_window"] == 5 and a["state"] == "closed" and a["resolution"] == "auto_cleared"
    assert a["closed_at"] == core._iso(wins[13].end)        # windows 9..14 quiet → closes at end of 14 (6th)
    assert len(res.alerts) == 2 and res.alerts[1]["opened_window"] == 18
    # acknowledged alerts are never auto-cleared
    ev = [core.ExtEvent(at=wins[5].end, alert_key="w5", action="acknowledge")]
    res2 = core.fold_segment(cfg, wins, warmup=False, tz=TZ, events=ev)
    assert len(res2.alerts) == 1 and res2.alerts[0]["state"] == "acknowledged"
    # false_alarm closes and snoozes the same severity for 7 days
    ev = [core.ExtEvent(at=wins[5].end, alert_key="w5", action="close", resolution="false_alarm")]
    res3 = core.fold_segment(cfg, wins, warmup=False, tz=TZ, events=ev)
    assert len(res3.alerts) == 1 and res3.alerts[0]["resolution"] == "false_alarm"
    assert any(p.get("snoozed") for p in res3.points)


def test_direction_down_mirrors_up():
    up = core.MonitorCfg(feature="current", warmup_windows=0, failure_limit=8.0, direction="down",
                         params={"baseline_min_windows": 5, "baseline_min_span_h": 0})
    t0 = ts("2027-01-01T00:00:00Z")
    xs = [10.0, 10.1, 9.9, 10.0, 10.05] + [10.0 - 0.2 * k for k in range(1, 15)]
    wins = [core.WindowIn(i=k + 1, start=t0 + timedelta(hours=8 * k), end=t0 + timedelta(hours=8 * (k + 1)),
                          x=x, u=8.0 * (k + 1)) for k, x in enumerate(xs)]
    hourly = {t0 - timedelta(days=20) + timedelta(hours=h): 1.0 for h in range(24 * 40)}
    res = core.fold_segment(up, wins, warmup=False, tz=TZ, rate_fn=core.make_rate_fn(hourly))
    last = res.points[-1]
    assert last["z"] < -0 or last["z"] > 0
    assert last["z"] > 3.9 and last["slope"] < 0              # falling current = worse; z in "bad" direction
    assert res.alerts and "EWMA" in res.alerts[0]["triggers_seen"]
    assert last["rul"]["rul_days"] is not None and last["rul"]["x_fit"] < 10.0


def test_pmp_with_predictive_uses_maintenance_formula():
    """P-9: predictive WOs count as planned (also no_defect); superseded cancelled has no hours."""
    from datetime import datetime, timezone
    from src.services.maintenance import core as mcore
    p = F["pmp_with_predictive"]
    at = datetime(2027, 1, 15, tzinfo=timezone.utc)
    facts = [mcore.WoFacts(id=w["id"], label=w["id"], type=w["type"], urgency=w.get("urgency"), state=w["state"],
                           labor_hours=w["labor_hours"], estimated_hours=0.0, remaining_hours=None, scheduled_for=None,
                           scheduled_at=None, completed_at=at if w["state"] in ("completed", "closed") else None,
                           cancel_reason=w.get("cancel_reason"), plan_id=None) for w in p["work_orders"]]
    r = mcore.pmp(facts, at - timedelta(days=1), at + timedelta(days=1))
    assert close(r["pmp"], p["expected"]["pmp"])
    assert (r["planned_hours"], r["total_hours"]) == (p["expected"]["planned_hours"], p["expected"]["total_hours"])
    from src.services.predictive.evaluation import predictive_no_defect_hours
    assert predictive_no_defect_hours([(w["type"], w["state"], w.get("finding"), w["labor_hours"])
                                       for w in p["work_orders"]]) == p["expected"]["predictive_no_defect_hours"]
