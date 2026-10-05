"""Golden scenario E (docs/scenarios/scenario_e_maintenance.json) through the API, exactly.

Seed: positions, components (with usage since install), usage accumulators/buckets consistent with the
scenario, failures (POST /api/failures), plans (POST /api/maintenance-plans with last execution), then every
work order is replayed through the API in chronological order — creation, transitions (with `at`), labor,
the OS-103 component swap — by the role that does it in maintenance_workflows.md. The clock is fixed at
`evaluate_at` (2026-12-01 12:00Z = 08:00 Manaus).

Checked: expected.plans (state, consumed, triggers, dates, next usage), components (life, RUL), after the
OS-103 swap, PMP / schedule compliance (+ exclusions and reasons) / backlog, late work orders, reliability by
position / component type / manufacturer and MTTF by manufacturer/model, every rejected transition (422 with
the allowed list), S4-4 (cancelling OS-116 re-creates the PL6 draft at once; never two)."""
from __future__ import annotations

from maint_helpers import (SCENARIO_E as E, api, close, make_maint_tenant, scenario_clock,  # noqa: F401
                           seed_scenario_e_assets, ts)

X = E["expected"]
WO = {w["id"]: w for w in E["work_orders"]}
PL = {p["id"]: p for p in E["plans"]}

STARTERS = {"in_progress", "on_hold", "completed"}


def _role_for(frm: str | None, to: str) -> str:
    if to in STARTERS or (frm == "on_hold" and to == "in_progress"):
        return "technician"
    if to == "closed":
        return "production_supervisor"
    return "maintenance_planner"


def _anchor(ctx, anchor: str) -> dict:
    if anchor.startswith("machine:"):
        return {"machine_id": ctx.machines[anchor.split(":", 1)[1]]}
    return {"position_id": ctx.positions[anchor]}


async def _create_plans(client, ctx) -> dict:
    ids = {}
    h = ctx.h("maintenance_planner")
    for p in E["plans"]:
        body = {"name": p["name"], "wo_type": p["wo_type"], "estimated_hours": p["estimated_hours"],
                "reset_on_replacement": p["reset_on_replacement"], "task_list": [{"text": "executar"}],
                **{f"interval_{m}": v for m, v in p["interval"].items()}, **_anchor(ctx, p["anchor"])}
        if p["id"] == "PL1":
            # reset again by the OS-103 swap during the replay (S4-2); active from the start
            body.update(last_done_at="2026-09-01T12:00:00Z", last_done_usage={"cycles": 7000000}, is_active=True)
        elif p["id"] == "PL5":
            # reset by OS-099 / OS-101 completions during the replay
            body.update(last_done_at="2026-10-01T12:00:00Z", is_active=False)
        else:
            body.update(last_done_at=p["last_reset"]["at"], last_done_usage=p["last_reset"]["position_usage"],
                        is_active=False)
        r = await api(client, "POST", "/api/maintenance-plans/", h, 201, json=body)
        ids[p["id"]] = r["id"]
    return ids


async def _create_failures(client, ctx) -> dict:
    ids = {}
    h = ctx.h("maintenance_planner")
    machine_of = {p["id"]: p["machine"] for p in E["positions"]}
    for f in E["failures"]:
        body = {"machine_id": ctx.machines[machine_of[f["position_id"]]], "position_id": ctx.positions[f["position_id"]],
                "failed_at": f["failed_at"], "restored_at": f["restored_at"], "severity": f["severity"],
                "failure_mode": f["failure_mode"], "detection_method": f["detection_method"], "note": f["note"]}
        if f["id"] != "F2":                       # F2: the server attributes the component installed (S4-9)
            body["component_id"] = ctx.components[f["component_id"]]
        r = await api(client, "POST", "/api/failures/", h, 201, json=body)
        assert r["component_id"] == ctx.components[f["component_id"]], f["id"]
        assert r["status"] == "draft"             # mechanism/cause still to be analysed
        ids[f["id"]] = r["id"]
    return ids


async def _replay(client, ctx, plan_ids, failure_ids) -> dict:
    events = []
    for order, w in enumerate(E["work_orders"]):
        if w["id"] == "OS-122":
            continue                              # created by the server when OS-116 is cancelled (S4-4)
        for i, (state, at) in enumerate(w["history"]):
            events.append((ts(at), order, i, w["id"], state))
    events.sort()
    # The scenario drafts OS-101 (PL5) on 28/10 while OS-099 (PL5) is still scheduled; the server keeps at most
    # one open WO per plan (S4-4), so a WO whose plan still has an open WO is replayed (with its own `at`
    # timestamps) right after that WO is completed.
    queue: list = []
    deferred: dict[str, list] = {}
    ids: dict[str, str] = {}
    state: dict[str, str] = {}

    def blocker(wid):
        plan = WO[wid]["plan_id"]
        for other, st in state.items():
            if other != wid and WO[other]["plan_id"] == plan and plan and st in (
                    "draft", "open", "approved", "scheduled", "in_progress", "on_hold"):
                return other
        return None

    def ordered():
        pending = list(events)
        while pending or queue:
            if queue:
                yield queue.pop(0)
                continue
            ev = pending.pop(0)
            wid = ev[3]
            if wid in deferred:
                deferred[wid].append(ev)
                continue
            if ev[2] == 0 and blocker(wid):
                deferred[wid] = [ev]
                continue
            yield ev

    pending_rejections = list(E["rejected_transitions"])
    tech = ctx.users["technician"]["id"]
    for at, _, i, wid, to in ordered():
        w = WO[wid]
        if i == 0:
            body = {"type": w["type"], "state": to, "title": w["title"], "estimated_hours": w["estimated_hours"],
                    "at": at.isoformat(), **_anchor(ctx, w["anchor"])}
            if w["urgency"]:
                body["urgency"] = w["urgency"]
            if w["plan_id"]:
                body["plan_id"] = plan_ids[w["plan_id"]]
            if w["failure_id"]:
                body["failure_id"] = failure_ids[w["failure_id"]]
            # emergency calls are opened by the line supervisor ("Chamar manutenção"), the rest by the planner
            role = "production_supervisor" if (w["urgency"] == "emergency") else "maintenance_planner"
            r = await api(client, "POST", "/api/work-orders/", ctx.h(role), 201, json=body)
            ids[wid] = r["id"]
            assert r["state"] == to and r["number"].startswith("OS-")
            if wid == "OS-116":
                # PL6 becomes active only now (its draft would otherwise pre-empt OS-116)
                await api(client, "PATCH", f"/api/maintenance-plans/{plan_ids['PL6']}", ctx.h("maintenance_planner"),
                          json={"is_active": True})
        else:
            frm = state[wid]
            body = {"to": to, "at": at.isoformat(), "note": f"cenário E: {frm} → {to}"}
            if to == "scheduled":
                body.update(scheduled_for=w["scheduled_for"], assignee_id=tech)
            if to == "on_hold":
                body["hold_reason"] = w.get("hold_reason") or "aguardando peça"
            if to in ("on_hold", "completed") and w["labor_hours"] and w["state"] in ("on_hold", "completed", "closed"):
                body["labor_hours"] = w["labor_hours"]
            if to == "cancelled":
                body["cancel_reason"] = w["cancel_reason"]
            if wid == "OS-103" and to == "completed":
                rep = w["component_replacement"]
                await api(client, "POST", f"/api/work-orders/{ids[wid]}/replacements", ctx.h("technician"), 201,
                          json={"position_id": ctx.positions["POS-A1"], "new_component_id": ctx.components[rep["installed"]],
                                "removal_reason": rep["removal_reason"]})
            r = await api(client, "POST", f"/api/work-orders/{ids[wid]}/transition", ctx.h(_role_for(frm, to)), json=body)
            assert r["state"] == to, (wid, r)
            if wid == "OS-103" and to == "completed":
                assert r["plans_reset"] == [plan_ids["PL1"]]
            if wid == "OS-116" and to == "cancelled":
                ids["OS-122"] = r["new_draft_id"]
                assert ids["OS-122"], "S4-4: cancelling the draft/WO of a plan ≥ 80 % re-creates a draft"
            if w["state"] == "in_progress" and to == "in_progress" and w["labor_hours"]:
                await api(client, "POST", f"/api/work-orders/{ids[wid]}/labor", ctx.h("technician"), 201,
                          json={"hours": w["labor_hours"], "at": (at.replace(minute=30)).isoformat(), "note": "parcial"})
        state[wid] = to
        if to in ("completed", "cancelled"):
            for other in [o for o, evs in deferred.items() if WO[o]["plan_id"] == w["plan_id"]]:
                queue.extend(deferred.pop(other))
        for rj in list(pending_rejections):
            if rj["wo"] in state and state[rj["wo"]] == rj["from"]:
                body = {"to": rj["to"], **(rj.get("body") or {})}
                role = "admin"                     # even the admin cannot break the state machine
                r = await client.post(f"/api/work-orders/{ids[rj['wo']]}/transition", headers=ctx.h(role), json=body)
                assert r.status_code == 422, (rj, r.text)
                detail = r.json()["detail"]
                assert detail["state"] == rj["from"] and rj["to"] not in detail["allowed"] or rj["to"] == "cancelled"
                pending_rejections.remove(rj)
    assert pending_rejections == [], pending_rejections
    return ids


async def test_scenario_e_end_to_end(client, scenario_clock):
    ctx = await make_maint_tenant("E")
    await seed_scenario_e_assets(ctx)
    plan_ids = await _create_plans(client, ctx)
    failure_ids = await _create_failures(client, ctx)
    wo_ids = await _replay(client, ctx, plan_ids, failure_ids)
    num = {}
    for wid, i in wo_ids.items():
        num[i] = wid
    planner = ctx.h("maintenance_planner")

    # the remaining plans become active: no duplicate drafts (S4-4)
    for pid in ("PL2", "PL3", "PL4", "PL5"):
        await api(client, "PATCH", f"/api/maintenance-plans/{plan_ids[pid]}", planner, json={"is_active": True})
    await api(client, "POST", "/api/maintenance-plans/recompute", planner)

    # ── every WO matches its scenario history ───────────────────────────────
    for wid, w in WO.items():
        d = await api(client, "GET", f"/api/work-orders/{wo_ids[wid]}", planner)
        assert d["state"] == w["state"], wid
        hist = [(h["to_state"], ts(h["at"])) for h in d["history"]]
        assert hist == [(s, ts(a)) for s, a in w["history"]], wid
        assert all(h["by"] for h in d["history"])                      # audit trail: who
        assert close(d["labor_hours"], w["labor_hours"]), wid
        assert d["scheduled_for"] == w["scheduled_for"], wid
        if w["scheduled_at"]:
            assert ts(d["scheduled_at"]) == ts(w["scheduled_at"]), wid
        assert d["cancel_reason"] == w["cancel_reason"], wid
        assert d["plan_id"] == (plan_ids[w["plan_id"]] if w["plan_id"] else None), wid
    os122 = await api(client, "GET", f"/api/work-orders/{wo_ids['OS-122']}", planner)
    assert os122["state"] == "draft" and os122["plan_id"] == plan_ids["PL6"]
    assert ts(os122["history"][0]["at"]) == ts(WO["OS-116"]["history"][-1][1])

    # ── plans (Próximas manutenções) ────────────────────────────────────────
    rows = await api(client, "GET", "/api/maintenance-plans/status", planner, params={"plant_id": ctx.plant_id})
    by_plan = {r["plan_id"]: r for r in rows}
    assert len(rows) == 6
    for pid, exp in X["plans"].items():
        r = by_plan[plan_ids[pid]]
        assert r["state"] == exp["state"], pid
        assert close(r["consumed"], exp["consumed"]), pid
        assert r["determining_trigger"] == exp["consumed_trigger"], pid
        assert r["due_trigger"] == exp["due_trigger"], pid
        assert r["since_maintenance"] == exp["since_maintenance"], (pid, r["since_maintenance"])
        assert r["consumed_by_trigger"].keys() == exp["consumed_by_trigger"].keys()
        assert all(close(r["consumed_by_trigger"][m], v) for m, v in exp["consumed_by_trigger"].items()), pid
        assert r["days_by_trigger"] == exp["days_by_trigger"], pid
        assert r["days_to_due"] == exp["days_to_due"], pid
        assert r["projected_date"] == r["next_due_date"] == exp["next_due_date"], pid
        assert r["calendar_due_date"] == exp["calendar_due_date"], pid
        assert r["next_due_usage_by_measure"] == exp["next_due_usage"], pid
        assert r["past_due"] == exp["past_due"], pid
        assert {t["measure"] for t in r["triggers"]} == set(exp["consumed_by_trigger"]), pid
        if exp["next_due_usage"]:
            assert r["next_due_usage"]["value"] == exp["next_due_usage"][r["next_due_usage"]["measure"]]
        else:
            assert r["next_due_usage"] is None
        # S4-4: exactly one open WO for every plan ≥ 80 %
        if exp["consumed"] >= 0.8:
            assert r["open_work_order_id"] is not None, pid
    assert by_plan[plan_ids["PL3"]]["next_due_date"] == "2027-01-10"           # not 2027-01-15
    assert by_plan[plan_ids["PL4"]]["open_work_order_id"] == wo_ids["OS-110"]
    assert by_plan[plan_ids["PL6"]]["open_work_order_id"] == wo_ids["OS-122"]
    assert by_plan[plan_ids["PL3"]]["open_work_order_id"] == wo_ids["OS-111"]
    assert by_plan[plan_ids["PL5"]]["open_work_order_id"] == wo_ids["OS-123"]
    # ordered by days_to_due
    days = [r["days_to_due"] for r in rows]
    assert days == sorted(days)
    only = await api(client, "GET", "/api/maintenance-plans/status", planner, params={"state": ["overdue"]})
    assert [r["plan_id"] for r in only] == [plan_ids["PL6"]]
    # the plan objects record the reset reference (OS-101 for PL5, OS-103 swap for PL1)
    pl1 = await api(client, "GET", f"/api/maintenance-plans/{plan_ids['PL1']}", planner)
    assert ts(pl1["last_reset_at"]) == ts(PL["PL1"]["last_reset"]["at"])
    assert pl1["last_reset_usage"]["cycles"] == PL["PL1"]["last_reset"]["position_usage"]["cycles"]
    assert "OS-" in pl1["last_reset_by"]
    pl5 = await api(client, "GET", f"/api/maintenance-plans/{plan_ids['PL5']}", planner)
    assert ts(pl5["last_reset_at"]) == ts(PL["PL5"]["last_reset"]["at"])

    # ── after the OS-103 swap (§5.4, S4-2) ──────────────────────────────────
    a = X["after_replacement_os103"]
    old = await api(client, "GET", f"/api/components/{ctx.components['C-A1-2']}", planner)
    assert ts(old["removed_at"]) == ts(a["removed"]["removed_at"]) and old["position_id"] is None
    assert old["removal_reason"] == "failure" and old["state"] == "removed"
    assert old["usage_since_install"]["cycles"] == a["removed"]["usage_since_install"]["cycles"]
    assert close(old["usage_since_install"]["hours"], a["removed"]["usage_since_install"]["hours"])
    new = await api(client, "GET", f"/api/components/{ctx.components['C-A1-3']}", planner)
    assert ts(new["installed_at"]) == ts(a["installed"]["installed_at"]) and new["position_id"] == ctx.positions["POS-A1"]
    for pid in a["plans_not_reset"]:
        p = await api(client, "GET", f"/api/maintenance-plans/{plan_ids[pid]}", planner)
        assert ts(p["last_reset_at"]) != ts(a["removed"]["removed_at"])
    os103 = await api(client, "GET", f"/api/work-orders/{wo_ids['OS-103']}", planner)
    assert os103["replacements"][0]["removed_component_id"] == ctx.components["C-A1-2"]
    assert os103["replacements"][0]["installed_component_id"] == ctx.components["C-A1-3"]
    assert os103["failure_id"] == failure_ids["F4"]
    f4 = await api(client, "GET", f"/api/failures/{failure_ids['F4']}", planner)
    assert f4["work_order_id"] == wo_ids["OS-103"]

    # ── components by % of life (S4-12) ─────────────────────────────────────
    comps = {c["component_id"]: c for c in await api(client, "GET", "/api/maintenance/components", planner)}
    for cid, exp in X["components"].items():
        c = comps[ctx.components[cid]]
        assert close(c["life_consumed_pct"], exp["life_consumed_pct"]), cid
        assert c["life_measure"] == exp["life_measure"], cid
        assert all(close(c["life_by_measure"][m], v) for m, v in exp["life_by_measure"].items()), cid
        assert (c["rul_days"], c["rul_measure"], c["projected_end_date"]) == \
            (exp["rul_days"], exp["rul_measure"], exp["projected_end_date"]), cid
        assert c["alert"] == exp["alert"], cid

    # ── late work orders ────────────────────────────────────────────────────
    late = await api(client, "GET", "/api/work-orders/", planner, params={"overdue": "true"})
    assert sorted(num[w["id"]] for w in late) == X["late_work_orders"]

    # ── PMP / schedule compliance / backlog (November) ──────────────────────
    await api(client, "POST", "/api/maintenance/absences", planner, 201,
              json={"user_id": ctx.users["technician"]["id"], "date": "2026-12-02", "hours": 4})
    k = await api(client, "GET", "/api/maintenance/kpis", ctx.h("manager"),
                  params={"plant_id": ctx.plant_id, "start": E["report_period"]["start_utc"],
                          "stop": E["report_period"]["stop_utc"]})
    p = X["pmp"]
    assert close(k["pmp"], p["pmp"]) and close(k["planned_hours"], p["planned_h"]) and close(k["total_hours"], p["total_h"])
    assert close(k["unplanned_hours"], p["unplanned_h"])
    assert sorted(num[i] for i in k["pmp_work_order_ids"]) == p["work_orders"]
    sc = X["schedule_compliance"]
    assert close(k["schedule_compliance"], sc["compliance"])
    assert (k["scheduled_count"], k["on_time_count"], k["late_count"], k["not_done_count"]) == \
        (sc["scheduled"], sc["on_time"], sc["late"], sc["not_done"])
    assert sorted(num[i] for i in k["compliance_denominator_ids"]) == sc["denominator"]
    assert sorted(num[i] for i in k["compliance_on_time_ids"]) == sc["on_time_ids"]
    assert [num[i] for i in k["compliance_late_ids"]] == sc["late_ids"]
    assert sorted(num[i] for i in k["compliance_not_done_ids"]) == sc["not_done_ids"]
    assert [[num[e["wo_id"]], e["reason"]] for e in k["compliance_excluded"]] == sc["excluded"]
    assert all(e["wo_number"] for e in k["compliance_excluded"])
    b = X["backlog"]
    assert close(k["backlog_hours"], b["hours"]) and k["backlog_count"] == b["count"]
    assert {num[i]: v for i, v in k["backlog_remaining_by_wo"].items()} == b["remaining_by_wo"]
    assert close(k["crew_capacity_hours_per_week"], b["weekly_capacity_hours"]) and k["technicians"] == 1
    assert close(k["backlog_weeks"], b["weeks"])
    assert k["overdue_count"] == b["overdue_count"] and close(k["overdue_hours"], b["overdue_hours"])
    assert sorted(num[i] for i in k["overdue_ids"]) == b["overdue_ids"]
    assert sorted(num[i] for i in k["drafts_not_in_backlog"]) == b["drafts_not_in_backlog"]
    assert k["requests_pending"] == []
    w = X["wrong_answers"]
    assert not close(k["pmp"], w["pmp_counting_open_wo_labor"]["value"])
    assert not close(k["schedule_compliance"], w["compliance_counting_break_in"]["value"])

    # ── reliability (12-month window) ───────────────────────────────────────
    win = {"start": E["reliability_window"]["start_utc"], "stop": E["reliability_window"]["stop_utc"]}
    # default window = 12 months ending today 00:00 local = the scenario window
    dflt = await api(client, "GET", "/api/maintenance/reliability", planner, params={"group_by": "position"})
    assert ts(dflt["period_start"]) == ts(win["start"]) and ts(dflt["period_end"]) == ts(win["stop"])

    def _cmp(row, exp, key):
        assert row["n"] == exp["n"] and row["restored"] == exp["restored"], key
        assert close(row["operating_hours"], exp["operating_hours"]), key
        assert close(row["cycles"], exp["cycles"]), (key, row["cycles"], exp["cycles"])
        assert close(row["mtbf_h"], exp["mtbf_h"]), key
        assert close(row["mcbf"], exp["mcbf_cycles"]) and close(row["mcbf_cycles"], exp["mcbf_cycles"]), key
        assert close(row["mttr_min"], exp["mttr_min"]), key
        assert close(row["mttr_h"], exp["mttr_min"] / 60), key
        assert row["reason"] == exp["reason"] == row["null_reason"], key
        if "incipient" in exp:
            assert row["incipient"] == exp["incipient"], key

    rel = await api(client, "GET", "/api/maintenance/reliability", planner, params={"group_by": "position", **win})
    rows = {r["position_id"]: r for r in rel["rows"]}
    for pid, exp in X["reliability_by_position"].items():
        _cmp(rows[ctx.positions[pid]], exp, pid)
    assert rows[ctx.positions["POS-MOT"]]["hours_source"] == "position"
    assert rows[ctx.positions["POS-A1"]]["hours_source"] == "machine"
    assert not close(rows[ctx.positions["POS-A1"]]["mtbf_h"], w["mtbf_a1_with_incipient"]["value"])
    assert not close(rows[ctx.positions["POS-AX1"]]["mttr_min"], w["mttr_ax1_with_unrestored_as_zero"]["value"])

    rel = await api(client, "GET", "/api/maintenance/reliability", planner, params={"group_by": "component_type", **win})
    rows = {r["key"]: r for r in rel["rows"]}
    for key, exp in X["reliability_by_component_type"].items():
        _cmp(rows[key], exp, key)

    rel = await api(client, "GET", "/api/maintenance/reliability", planner, params={"group_by": "manufacturer", **win})
    rows = {r["key"]: r for r in rel["rows"]}
    for key, exp in X["reliability_by_manufacturer"].items():
        _cmp(rows[key], exp, key)
    pc = X["reliability_by_component_type"]["pneumatic_cylinder"]
    parts = [r for k2, r in rows.items() if k2.startswith("pneumatic_cylinder|")]
    assert sum(r["n"] for r in parts) == pc["n"] and sum(r["cycles"] or 0 for r in parts) == pc["cycles"]

    rel = await api(client, "GET", "/api/maintenance/reliability", planner,
                    params={"group_by": "manufacturer_model", **win})
    rows = {r["key"]: r for r in rel["rows"]}
    for key, exp in X["mttf_by_manufacturer_model"].items():
        r = rows[key]
        assert r["mttf_measure"] == exp["measure"], key
        assert (r["failures_removed"], r["suspensions"], r["in_service"]) == \
            (exp["failures"], exp["suspensions"], exp["in_service"]), key
        assert close(r["mttf"], exp["mttf"]), key
        assert r["mttf_reason"] == exp["reason"], key
    assert not close(rows["Festo|DSBC-50-100-PPVA-N3"]["mttf"], w["mttf_festo_counting_suspensions"]["value"])
    assert rows["WEG|W22 2 cv + redutor"]["small_sample"] is True

    # ── notifications produced along the way ────────────────────────────────
    n = await api(client, "GET", "/api/notifications/", ctx.h("technician"))
    kinds = {i["kind"] for i in n["items"]}
    assert "emergency_call" in kinds and "wo_assigned" in kinds
    n = await api(client, "GET", "/api/notifications/", ctx.h("production_supervisor"))
    assert "wo_awaiting_close" in {i["kind"] for i in n["items"]}
    n = await api(client, "GET", "/api/notifications/", planner)
    kinds = {i["kind"] for i in n["items"]}
    assert {"plan_overdue", "wo_on_hold"} <= kinds
