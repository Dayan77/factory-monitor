"""Sprint 5 fix loop — QA findings on the alert lifecycle:

* a failure whose failed_at is corrected after it closed an alert moves the closure (P-8 follows the current
  failure records); the alert opened meanwhile on the stale closure is superseded (`duplicate`), recompute
  reports the difference, and P-10 counts one true positive for one failure;
* a failure removed from the position reopens the alert it closed;
* right after a rebaseline the monitor shows the new segment (0/N windows), not the previous one's progress.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant
from test_predictive_lifecycle import T0, _alert, _position, feed, small_monitor


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T0)
    monkeypatch.setattr(clock, "now", c)
    return c


async def _alerts(client, ctx, pos) -> list[dict]:
    rows = await api(client, "GET", f"/api/predictive/alerts?position_id={pos}&state=open&state=acknowledged"
                                    f"&state=converted&state=closed", ctx.h("maintenance_planner"))
    return sorted(rows, key=lambda a: a["opened_at"])


async def test_corrected_failure_date_moves_the_closure_and_supersedes_the_reopened_alert(client, clk):
    ctx = await make_maint_tenant("FR1")
    pos = await _position(ctx)
    hp = ctx.h("maintenance_planner")
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])           # alert A opens at window 4
    a = await _alert(client, ctx, pos)
    # the failure is entered 24 h too early by mistake… (T = end of window 4 + 1 h)
    t_wrong = clk.at + timedelta(hours=1)
    clk.at = t_wrong + timedelta(minutes=1)
    f = await api(client, "POST", "/api/failures/", ctx.h("manager"), 201, json={
        "machine_id": ctx.machines["Transportador de Saída"], "position_id": pos, "failed_at": t_wrong.isoformat(),
        "severity": "critical", "failure_mode": "OHE"})
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    a1 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a1["state"] == "closed" and a1["resolution"] == "failed_before_action"
    # …the signal stays high: a second alert B opens on the (wrong) closure
    await feed(client, ctx, clk, m["id"], [16.0, 16.0, 16.0, 16.0])
    rows = await _alerts(client, ctx, pos)
    assert len(rows) == 2 and rows[1]["state"] != "closed"
    b_id = rows[1]["id"]
    # the date is corrected to 24 h later (still in the past)
    t_right = t_wrong + timedelta(hours=24)
    assert t_right < clk.at
    await api(client, "PATCH", f"/api/failures/{f['id']}", ctx.h("manager"), json={"failed_at": t_right.isoformat()})
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["same"] is False, rc
    a2 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a2["state"] == "closed" and a2["resolution"] == "failed_before_action"
    assert a2["closed_at"].startswith(t_right.isoformat()[:19]), a2["closed_at"]
    b2 = await api(client, "GET", f"/api/predictive/alerts/{b_id}", hp)
    assert b2["state"] == "closed" and b2["resolution"] == "duplicate"
    # P-10: one failure, one true positive
    ev = await api(client, "GET", f"/api/predictive/evaluation?start={(T0 - timedelta(days=1)).isoformat()}"
                                  f"&stop={(clk.at + timedelta(hours=1)).isoformat()}".replace("+", "%2B"), hp)
    assert ev["true_positives"] == 1 and ev["failures_total"] == 1, ev
    # stable: a second recompute changes nothing, nor does the next engine run
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["same"] is True, rc
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    rows = await _alerts(client, ctx, pos)
    assert [(r["id"], r["state"], r["resolution"]) for r in rows[:2]] == [
        (a["id"], "closed", "failed_before_action"), (b_id, "closed", "duplicate")]
    # the signal is still high after the (corrected) failure: a new alert opens after it, as it would have had the
    # failure been entered right the first time
    assert len(rows) == 3 and rows[2]["state"] == "open" and rows[2]["opened_at"] > t_right.isoformat()[:19]


async def test_failure_moved_off_the_position_reopens_the_alert(client, clk):
    ctx = await make_maint_tenant("FR2")
    pos = await _position(ctx)
    other = await _position(ctx, name="Motor R")
    hp = ctx.h("maintenance_planner")
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    a = await _alert(client, ctx, pos)
    await api(client, "POST", f"/api/predictive/alerts/{a['id']}/transition", hp, json={"to": "acknowledged"})
    f = await api(client, "POST", "/api/failures/", ctx.h("manager"), 201, json={
        "machine_id": ctx.machines["Transportador de Saída"], "position_id": pos, "failed_at": clk.at.isoformat(),
        "severity": "critical", "failure_mode": "OHE"})
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    assert (await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp))["state"] == "closed"
    # it was the other motor
    await api(client, "PATCH", f"/api/failures/{f['id']}", ctx.h("manager"), json={"position_id": other})
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    a2 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a2["state"] == "acknowledged" and a2["resolution"] is None and a2["closed_at"] is None


async def test_monitor_right_after_rebaseline_close_shows_the_new_segment(client, clk):
    ctx = await make_maint_tenant("FR3")
    pos = await _position(ctx)
    hp = ctx.h("maintenance_planner")
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    a = await _alert(client, ctx, pos)
    clk.at += timedelta(minutes=5)
    await api(client, "POST", f"/api/predictive/alerts/{a['id']}/transition", hp,
              json={"to": "closed", "resolution": "rebaseline", "comment": "novo regime de carga"})
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)    # before any engine run
    assert mon["state"] == "learning"
    assert mon["learning"]["windows"] == 0 and mon["learning"]["label"] == "aprendendo (0/3 janelas)", mon["learning"]
    assert mon["last"] is None
