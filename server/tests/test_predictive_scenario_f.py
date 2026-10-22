"""Golden scenario F end to end through the database, the pipeline and the HTTP API (docs/predictive.md P-1…P-12).

* F1 (cylinder, stroke_time): the 70 blocks are stored as condition features (a 10 000-cycle block per 12 h — the
  raw-reading extraction of stroke blocks is covered in test_predictive_pipeline.py) and the pipeline runs block by
  block with the clock at each block end: alert instants, lead times, trace, RUL checkpoints, notifications, the
  failure closing the alert (`failed_before_action`), recompute determinism.
* F2 (motor, current): the raw run_state / current readings are written to InfluxDB and the pipeline extracts each
  daily window (holiday = invalid window), opens the `watch` false alarm, the planner closes it with `rebaseline`
  at the end of day 62 through the API and the new baseline (no warm-up) is learned from days 63–82.
* P-10 evaluation over both positions.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant
from predictive_helpers import (F, X, close, f1_hourly_usage, f1_reset_at, f1_windows, f2_close_at,
                                f2_hourly_usage, f2_reset_at, f2_windows, rules_of, ts)

ALERT_KEYS = ("opened_at", "opened_window", "first_triggers", "severity", "escalations", "escalation_windows",
              "state", "triggers_seen")


def _view(a: dict) -> dict:
    v = {k: a[k] for k in ALERT_KEYS}
    if a["state"] == "closed":
        v.update(resolution=a["resolution"], closed_at=a["closed_at"])
    return v


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(f1_reset_at())
    monkeypatch.setattr(clock, "now", c)
    return c


async def _position(ctx, machine: str, name: str, ctype: str) -> str:
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=ctx.machines[machine], name=name, component_type=ComponentType(ctype))
        s.add(p)
        await s.commit()
        return p.id


async def _hourly(ctx, position_id: str, measure: str, hourly: dict, scale: float = 1.0) -> None:
    from src.db import AsyncSessionLocal
    from src.models import UsageBucket
    async with AsyncSessionLocal() as s:
        for t, v in hourly.items():
            s.add(UsageBucket(scope="position", entity_id=position_id, measure=measure, bucket_start=t,
                              tenant_id=ctx.id, value=v * scale))
        await s.commit()


def _rules(case: str) -> list[dict]:
    return [{"code": r["id"], "type": r["type"], "value": r["value"], "sustained_for": r["sustained_for"],
             "severity": r["severity"]} for r in rules_of(case)]


async def _process(client, ctx, mid: str) -> dict:
    return await api(client, "POST", f"/api/predictive/monitors/{mid}/process", ctx.h("maintenance_planner"))


async def setup_f1(client, clk, ctx) -> tuple[str, str]:
    from src.db import AsyncSessionLocal
    from src.models import PredictiveMonitor
    from src.services.predictive import engine as pe, service as ps
    pos = await _position(ctx, "Estação de Fixação", "Cilindro B3", "pneumatic_cylinder")
    await _hourly(ctx, pos, "cycles", f1_hourly_usage())
    c = F["cases"]["F1"]
    clk.at = f1_reset_at() + timedelta(minutes=1)
    m = await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json={
        "position_id": pos, "feature": "stroke_time", "window_cycles": c["window"]["cycles"],
        "warmup_windows": c["warmup_windows"], "failure_limit": c["failure_limit"]["value"],
        "failure_limit_relative": True, "start_at": c["installed_at"], "rules": _rules("F1")})
    assert m["state"] == "learning" and m["learning"]["label"] == "aprendendo (0/20 janelas)"
    for w in f1_windows():
        async with AsyncSessionLocal() as s:
            mm = await s.get(PredictiveMonitor, m["id"])
            reset = await ps.latest_reset(s, mm)
            await pe.store_window(s, mm, reset, start=w["start"], end=w["end"], x=w["x"], n=w["n"], u=w["u"])
            await s.commit()
        clk.at = w["end"]
        await _process(client, ctx, m["id"])
    return pos, m["id"]


async def test_scenario_f_end_to_end(client, clk):
    ctx = await make_maint_tenant("PF")
    # ── F1 ────────────────────────────────────────────────────────────────────
    pos1, mid1 = await setup_f1(client, clk, ctx)
    h = ctx.h("maintenance_planner")
    e1 = X["F1"]
    mon = await api(client, "GET", f"/api/predictive/monitors/{mid1}", h)
    b = mon["baseline"]
    assert mon["state"] == "monitoring"
    assert (b["from_i"], b["to_i"], b["established_at"]) == (2, 21, e1["baseline"]["established_at"])
    for k in ("m0", "mad", "sigma", "sigma_floor"):
        assert close(b[k], e1["baseline"][k]), k
    assert close(mon["failure_limit_value"], 240.0)
    alerts = await api(client, "GET", f"/api/predictive/alerts?position_id={pos1}", h)
    assert len(alerts) == 1
    a1 = alerts[0]
    assert _view(a1) == e1["alert"]
    failed = ts(F["cases"]["F1"]["failure"]["failed_at"])
    for lvl, hours in e1["lead_time_h"].items():
        assert (failed - ts(a1["escalations"][lvl])).total_seconds() / 3600 == hours
    ev = {t["source"]: t for t in a1["triggers"]}
    assert set(ev) == set(e1["alert"]["triggers_seen"])
    assert ev["R-F1-THR"]["first_i"] == 62 and ev["R-F1-THR"]["limit"] == 230.0
    assert a1["explanation"].startswith("Cilindro B3 — tempo de curso")
    assert "Falha projetada em" in a1["explanation"]
    # trace and RUL checkpoints from the stored series
    feats = await api(client, "GET", f"/api/predictive/features?position_id={pos1}&feature=stroke_time", h)
    (ser,) = feats["series"]
    pts = {p["i"]: p for p in ser["points"]}
    assert pts[1]["phase"] == "warmup" and pts[2]["phase"] == "baseline" and pts[22]["phase"] == "monitoring"
    for r in e1["trace"]:
        p = pts[r["i"]]
        for k in ("x", "z", "ewma", "cusum", "rolling_z", "slope"):
            assert close(p[k], r[k]), (r["i"], k, p[k], r[k])
        assert p["triggers"] == r["triggers"], r["i"]
    assert pts[25]["anomaly"] is True and pts[25]["triggers"] == []
    for cp in e1["rul_checkpoints"]:
        r = pts[cp["window"]]["rul"]
        for k in ("slope", "slope_se", "x_fit", "rul_usage", "rul_usage_low", "rul_usage_high", "rate_per_day"):
            assert close(r[k], cp[k]), (cp["window"], k)
        for k in ("today", "rul_days", "rul_days_low", "rul_days_high", "projected_date", "projected_date_low",
                  "projected_date_high"):
            assert r[k] == cp[k], (cp["window"], k, r[k], cp[k])
    assert ser["bands"][0]["low"] == pytest.approx(194.0) and ser["bands"][0]["high"] == pytest.approx(206.0)
    # notifications: warning to planner+manager, critical push to planner and on-call technician (P-8)
    from sqlalchemy import select
    from src.db import AsyncSessionLocal
    from src.models import Notification
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Notification.kind, Notification.user_id).where(
            Notification.tenant_id == ctx.id))).all()
    kinds = {(k, u) for k, u in rows}
    assert ("predictive_warning", ctx.users["maintenance_planner"]["id"]) in kinds
    assert ("predictive_warning", ctx.users["manager"]["id"]) in kinds
    assert ("predictive_critical", ctx.users["technician"]["id"]) in kinds
    assert ("predictive_critical", ctx.users["maintenance_planner"]["id"]) in kinds
    assert not any(u == ctx.users["operator"]["id"] for _, u in rows)
    # recompute since the last baseline reproduces the same alerts (P-12)
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": mid1})
    assert rc["same"] is True, rc["monitors"][0]["differences"]
    again = await api(client, "GET", f"/api/predictive/alerts/{a1['id']}", h)
    assert _view(again) == e1["alert"]
    # operator sees only the badge
    bad = await api(client, "GET", f"/api/predictive/badges?machine_id={ctx.machines['Estação de Fixação']}",
                    ctx.h("operator"))
    assert bad[ctx.machines["Estação de Fixação"]] == {"severity": "critical", "label": "condição: crítico"}
    await api(client, "GET", "/api/predictive/alerts", ctx.h("operator"), 403)
    # the real failure (virtual plant: block 70) closes the alert failed_before_action (P-8)
    from src.models import FailureRecord
    fl = F["cases"]["F1"]["failure"]
    async with AsyncSessionLocal() as s:
        s.add(FailureRecord(tenant_id=ctx.id, machine_id=ctx.machines["Estação de Fixação"], position_id=pos1,
                            failed_at=failed, severity="critical", failure_mode=fl["failure_mode"],
                            mechanism=fl["failure_mechanism"], cause=fl["failure_cause"],
                            detection_method=fl["detection_method_if_unmonitored"], status="complete"))
        await s.commit()
    clk.at = failed + timedelta(hours=1)
    await _process(client, ctx, mid1)
    a1c = await api(client, "GET", f"/api/predictive/alerts/{a1['id']}", h)
    assert a1c["state"] == "closed" and a1c["resolution"] == "failed_before_action"
    assert a1c["closed_at"] == fl["failed_at"]
    assert a1c["history"][-1]["source"] == "failure"
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": mid1})
    assert rc["same"] is True, rc["monitors"][0]["differences"]

    # ── F2 (raw readings → Influx → daily windows) ────────────────────────────
    from src.models import Tag
    from src.models.tag import ProtocolType, SemanticRole
    from src.services.influx import influx
    pos2 = await _position(ctx, "Transportador de Saída", "Motor B", "electric_motor")
    mach2 = ctx.machines["Transportador de Saída"]
    async with AsyncSessionLocal() as s:
        run = Tag(tenant_id=ctx.id, machine_id=mach2, position_id=pos2, name="run motor B", protocol=ProtocolType.mqtt,
                  address=f"motb/run/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.run_state)
        cur = Tag(tenant_id=ctx.id, machine_id=mach2, position_id=pos2, name="corrente motor B", unit="A",
                  protocol=ProtocolType.mqtt, address=f"motb/i/{uuid.uuid4().hex[:6]}",
                  semantic_role=SemanticRole.current)
        s.add_all([run, cur])
        await s.commit()
        run_id, cur_id = run.id, cur.id
    c2 = F["cases"]["F2"]
    for w in f2_windows():
        start = w["start"]
        on = w["running_h"] > 0
        await influx.write(ctx.id, run_id, 1.0 if on else 0.0, machine_id=mach2, ts=start)
        if on:
            await influx.write(ctx.id, cur_id, w["x"], machine_id=mach2, unit="A", ts=start)
            await influx.write(ctx.id, run_id, 0.0, machine_id=mach2, ts=start + timedelta(hours=w["running_h"]))
    await _hourly(ctx, pos2, "operating_seconds", f2_hourly_usage(), 3600.0)
    clk.at = f2_reset_at() + timedelta(minutes=1)
    m2 = await api(client, "POST", "/api/predictive/monitors", h, 201, json={
        "position_id": pos2, "feature": "current", "tag_id": cur_id, "window_hours": c2["window"]["duration_h"],
        "warmup_windows": c2["warmup_windows"], "failure_limit": c2["failure_limit"]["value"],
        "failure_limit_relative": False, "start_at": f2_reset_at().isoformat(), "rules": _rules("F2"),
        "params": {"data_gap_s": 172800}})
    mid2 = m2["id"]
    close_at = f2_close_at()
    alert2 = None
    for w in f2_windows():
        clk.at = w["end"] + timedelta(minutes=3)
        r = await _process(client, ctx, mid2)
        assert r["windows_added"] == 1, (w["i"], r)
        if w["i"] == c2["actions"][0]["after_window"]:
            (alert2,) = await api(client, "GET", f"/api/predictive/alerts?position_id={pos2}", h)
            clk.at = close_at
            # a technician may acknowledge but not close (P-8 roles)
            await api(client, "POST", f"/api/predictive/alerts/{alert2['id']}/transition", ctx.h("technician"), 403,
                      json={"to": "closed", "resolution": "rebaseline", "comment": "x"})
            await api(client, "POST", f"/api/predictive/alerts/{alert2['id']}/transition", h, 422,
                      json={"to": "closed", "resolution": "rebaseline"})            # reason required
            await api(client, "POST", f"/api/predictive/alerts/{alert2['id']}/transition", h, 422,
                      json={"to": "closed", "resolution": "auto_cleared", "comment": "x"})
            act = c2["actions"][0]
            await api(client, "POST", f"/api/predictive/alerts/{alert2['id']}/transition", h, json={
                "to": "closed", "resolution": act["resolution"], "comment": act["comment"]})
    e2 = X["F2"]
    (a2,) = await api(client, "GET", f"/api/predictive/alerts?position_id={pos2}", h)
    assert _view(a2) == e2["alert"]
    mon2 = await api(client, "GET", f"/api/predictive/monitors/{mid2}", h)
    bases = [b for b in mon2["baselines"] if b["established_at"]]
    assert [{k: b[k] for k in ("from_i", "to_i", "established_at")} for b in bases] == e2["baselines"]
    assert [b["reset_kind"] for b in mon2["baselines"]] == ["initial", "alert_rebaseline"]
    assert mon2["baselines"][1]["warmup"] is False and mon2["baselines"][1]["reason"] == c2["actions"][0]["comment"]
    for b, m0, sg in zip(bases, e2["baselines_m0"], e2["baselines_sigma"]):
        assert close(b["m0"], m0) and close(b["sigma"], sg)
    feats2 = await api(client, "GET", f"/api/predictive/features?position_id={pos2}", h)
    pts2 = {p["i"]: p for p in feats2["series"][0]["points"]}
    assert pts2[47]["valid"] is False and pts2[47]["phase"] == "invalid" and pts2[47]["ewma"] is None
    assert close(pts2[47]["running_h"], 0.0)
    for i, w in ((1, 12.1), (46, 12.17), (90, 12.5)):
        assert close(pts2[i]["x"], w), i                                   # time-weighted mean while running
    assert all(not pts2[i]["triggers"] for i in range(83, 91))
    for cp in e2["rul_checkpoints"]:
        r = pts2[cp["window"]]["rul"]
        for k, v in cp.items():
            if k == "window":
                continue
            if isinstance(v, float):
                assert close(r.get(k), v), (cp["window"], k, r.get(k), v)
            else:
                assert r.get(k) == v, (cp["window"], k, r.get(k), v)
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": mid2})
    assert rc["same"] is True, rc["monitors"][0]["differences"]

    # ── P-10 evaluation over both positions ──────────────────────────────────
    clk.at = ts(c2["observed_until"])
    ev = await api(client, "GET", "/api/predictive/evaluation?start=2027-01-01T00:00:00Z&stop=2027-06-01T00:00:00Z",
                   ctx.h("manager"))
    ee = X["evaluation"]
    for k in ("true_positives", "false_alarms", "true_positives_warning", "false_alarms_warning", "failures_total",
              "failures_detected"):
        assert ev[k] == ee[k], k
    for k in ("precision", "precision_warning", "false_alarms_per_position_year",
              "false_alarms_per_position_year_warning", "lead_time_h_first_alert"):
        assert close(ev[k], ee[k]), (k, ev[k], ee[k])
    assert close(ev["monitored_days"], ee["monitored_days"]["F1"] + ee["monitored_days"]["F2"])
    assert close(ev["monitored_days_by_monitor"][f"{pos1}:stroke_time"], ee["monitored_days"]["F1"])
    assert close(ev["monitored_days_by_monitor"][f"{pos2}:current"], ee["monitored_days"]["F2"])
    assert ev["rul_band_coverage"] is not None and ev["rul_band_coverage"] >= 0.8
    await api(client, "GET", "/api/predictive/evaluation", ctx.h("technician"), 403)
