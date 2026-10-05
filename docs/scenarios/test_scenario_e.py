"""Golden scenario E (docs/scenarios/scenario_e_maintenance.json) — oracle for the Sprint 4 maintenance
module (plans, work orders, component replacement, reliability by position/type/manufacturer).

The server has no maintenance module yet: this file recomputes every `expected` value from the scenario's
inputs following docs/domain.md §5 + "Esclarecimentos de manutenção (Sprint 4)" and docs/kpis.md §2–§4.
When Sprint 4 lands, its API tests load the same JSON (see README.md, "Cenário E").

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
E = json.loads((HERE / "scenario_e_maintenance.json").read_text(encoding="utf-8"))
X = E["expected"]
TZ = ZoneInfo(E["plant"]["timezone"])
UTC = timezone.utc


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_date(s: str) -> date:
    return ts(s).astimezone(TZ).date()


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


TODAY = local_date(E["evaluate_at"])
POS = {p["id"]: p for p in E["positions"]}
COMP = {c["id"]: c for c in E["components"]}
WO = {w["id"]: w for w in E["work_orders"]}
CFG = E["config"]


def test_today_is_plant_date():
    assert TODAY.isoformat() == E["today_plant"] == "2026-12-01"


# ── plans (domain.md §5.1, kpis.md §4.2, S4-1..S4-4) ──────────────────────────

def oracle_plan(pl: dict) -> dict:
    ref = local_date(pl["last_reset"]["at"])
    pos = POS.get(pl["anchor"])
    cons, days, nxt, since = {}, {}, {}, {}
    for m, iv in pl["interval"].items():
        if m == "days":
            used = (TODAY - ref).days                         # S4-1: dias corridos entre datas locais
            days[m] = max((ref + timedelta(days=iv) - TODAY).days, 0)
        else:
            used = pos["usage_total_now"][m] - pl["last_reset"]["position_usage"][m]
            rate = pos["usage_last_14_days"][m] / 14
            rem = iv - used
            days[m] = 0 if rem <= 0 else math.ceil(rem / rate - 1e-9)       # C18
            nxt[m] = pl["last_reset"]["position_usage"][m] + iv            # S4-3
        since[m] = used
        cons[m] = used / iv
    c = max(cons.values())
    tol = CFG["overdue_tolerance"]
    state = "overdue" if c >= 1 + tol else "due" if c >= 1 else "due_soon" if c >= CFG["due_soon_at"] else "ok"
    d = min(days.values())
    trig = min(days, key=lambda k: (days[k], -cons[k]))                  # S4-3: earliest date; tie → larger %
    return {"since_maintenance": since, "consumed_by_trigger": cons, "consumed": c,
            "consumed_trigger": max(cons, key=cons.get), "state": state, "days_by_trigger": days,
            "days_to_due": d, "due_trigger": trig, "next_due_date": (TODAY + timedelta(days=d)).isoformat(),
            "next_due_usage": nxt, "past_due": d == 0 and c >= 1}


@pytest.mark.parametrize("pl", E["plans"], ids=lambda p: p["id"])
def test_plan_due(pl):
    got, exp = oracle_plan(pl), X["plans"][pl["id"]]
    for k in ("consumed", ):
        assert close(got[k], exp[k]), k
    for k in ("consumed_by_trigger",):
        assert got[k].keys() == exp[k].keys() and all(close(got[k][m], exp[k][m]) for m in got[k])
    for k in ("since_maintenance", "consumed_trigger", "state", "days_by_trigger", "days_to_due", "due_trigger",
              "next_due_date", "next_due_usage", "past_due"):
        assert got[k] == exp[k], (k, got[k], exp[k])


def test_plan_states_cover_every_case():
    states = {X["plans"][p]["state"] for p in X["plans"]}
    assert states == {"ok", "due_soon", "due", "overdue"}
    triggers = {m for p in E["plans"] for m in p["interval"]}
    assert triggers == {"cycles", "hours", "starts", "days"}
    # PL3: the trigger with the largest % (starts) is not the one that comes due first (hours)
    pl3 = X["plans"]["PL3"]
    assert pl3["consumed_trigger"] == "starts" and pl3["due_trigger"] == "hours"
    assert X["wrong_answers"]["pl3_trigger_by_max_consumed_date"]["date"] != pl3["next_due_date"]


def test_replacement_resets_only_plans_with_flag():
    """OS-103 swaps C-A1-2 → C-A1-3: PL1 (reset_on_replacement) restarts from the swap; the new
    component's age equals PL1's usage since maintenance; the position total keeps counting (§5.4)."""
    r = X["after_replacement_os103"]
    os103 = WO["OS-103"]
    rep = os103["component_replacement"]
    done = next(t for s, t in os103["history"] if s == "completed")
    assert rep["at"] == done == COMP["C-A1-2"]["removed_at"] == COMP["C-A1-3"]["installed_at"] == r["installed"]["installed_at"]
    assert COMP["C-A1-2"]["removal_reason"] == "failure" and COMP["C-A1-2"]["usage_since_install"] == r["removed"]["usage_since_install"]
    pl1 = next(p for p in E["plans"] if p["id"] == "PL1")
    assert pl1["reset_on_replacement"] and pl1["last_reset"]["at"] == done
    assert pl1["last_reset"]["position_usage"]["cycles"] == r["position_usage_total_cycles"]
    since = POS["POS-A1"]["usage_total_now"]["cycles"] - r["position_usage_total_cycles"]
    assert since == COMP["C-A1-3"]["usage_since_install"]["cycles"] == X["plans"]["PL1"]["since_maintenance"]["cycles"]
    for pid in r["plans_not_reset"]:
        p = next(p for p in E["plans"] if p["id"] == pid)
        assert not p["anchor"] == "POS-A1" or not p["reset_on_replacement"]
        assert p["last_reset"]["at"] != done


def test_plan_wo_completion_is_reset_point():
    """§5.4: completing a plan's WO resets it (PL5 ← OS-101); WOs not completed don't (PL4 ← OS-110)."""
    os101_done = next(t for s, t in WO["OS-101"]["history"] if s == "completed")
    assert next(p for p in E["plans"] if p["id"] == "PL5")["last_reset"]["at"] == os101_done
    assert WO["OS-110"]["state"] == "scheduled" and X["plans"]["PL4"]["state"] == "due"


# ── components: life and RUL (kpis.md §4.1–4.2, S4-12) ──────────────────────────────

def test_component_life_and_rul():
    for cid, exp in X["components"].items():
        c = COMP[cid]
        assert c["removed_at"] is None
        p = POS[c["position_id"]]
        lifes = {m: c["usage_since_install"][m] / L for m, L in c["nominal_life"].items()}
        days = {m: math.ceil((L - c["usage_since_install"][m]) / (p["usage_last_14_days"][m] / 14) - 1e-9)
                for m, L in c["nominal_life"].items()}
        best = min(days, key=lambda m: (days[m], -lifes[m]))
        life = max(lifes.values())
        assert close(life, exp["life_consumed_pct"]) and max(lifes, key=lifes.get) == exp["life_measure"]
        assert (days[best], best) == (exp["rul_days"], exp["rul_measure"])
        assert (TODAY + timedelta(days=days[best])).isoformat() == exp["projected_end_date"]
        assert exp["alert"] == ("nominal_reached" if life >= 1 else "attention" if life >= 0.8 else None)


def test_component_window_usage_adds_up_to_position():
    """The usage of each position in the 12-month window is split among the components installed
    during it (the base of MTBF/MCBF by manufacturer)."""
    by_pos = defaultdict(lambda: defaultdict(float))
    for c in E["components"]:
        for m, v in (c["usage_in_window_12m"] or {}).items():
            by_pos[c["position_id"]][m] += v
    for pid in ("POS-A1", "POS-A2"):
        assert by_pos[pid]["hours"] == POS[pid]["window_12m"]["hours"]
        assert by_pos[pid]["cycles"] == POS[pid]["window_12m"]["cycles"]
    # one installed component per position at any time
    for pid in POS:
        ivs = sorted((ts(c["installed_at"]), ts(c["removed_at"]) if c["removed_at"] else ts(E["evaluate_at"]))
                     for c in E["components"] if c["position_id"] == pid)
        assert all(a[1] <= b[0] for a, b in zip(ivs, ivs[1:])), pid
    # every failure points to the component installed at failed_at
    for f in E["failures"]:
        c = COMP[f["component_id"]]
        t = ts(f["failed_at"])
        assert c["position_id"] == f["position_id"] and ts(c["installed_at"]) <= t
        assert c["removed_at"] is None or t <= ts(c["removed_at"])


# ── reliability (kpis.md §2.1–2.4, S4-8..S4-10) ───────────────────────────────

W0, W1 = ts(E["reliability_window"]["start_utc"]), ts(E["reliability_window"]["stop_utc"])
IN_W = [f for f in E["failures"] if W0 <= ts(f["failed_at"]) < W1]
COUNTED = [f for f in IN_W if f["severity"] in ("critical", "degraded")]


def _restore_min(f):
    return (ts(f["restored_at"]) - ts(f["failed_at"])).total_seconds() / 60 if f["restored_at"] else None


def oracle_rel(fs, hours, cycles):
    n = len(fs)
    r = [x for x in map(_restore_min, fs) if x is not None]
    return {"n": n, "mtbf_h": hours / n if n else None, "mcbf_cycles": cycles / n if n and cycles else None,
            "mttr_min": sum(r) / len(r) if r else None, "restored": len(r)}


def _cmp_rel(got, exp):
    for k in ("n", "restored"):
        assert got[k] == exp[k], k
    for k in ("mtbf_h", "mcbf_cycles", "mttr_min"):
        assert close(got[k], exp[k]), (k, got[k], exp[k])


def test_reliability_by_position():
    for pid, exp in X["reliability_by_position"].items():
        p = POS[pid]
        got = oracle_rel([f for f in COUNTED if f["position_id"] == pid], p["window_12m"]["hours"],
                         p["window_12m"]["cycles"])
        _cmp_rel(got, exp)
        assert exp["incipient"] == sum(1 for f in IN_W if f["position_id"] == pid and f["severity"] == "incipient")
    assert "F0" not in {f["id"] for f in IN_W}                 # before the window
    w = X["wrong_answers"]
    assert not close(w["mtbf_a1_with_incipient"]["value"], X["reliability_by_position"]["POS-A1"]["mtbf_h"])
    assert not close(w["mttr_ax1_with_unrestored_as_zero"]["value"], X["reliability_by_position"]["POS-AX1"]["mttr_min"])


def test_reliability_by_type_and_manufacturer():
    for t, exp in X["reliability_by_component_type"].items():
        ps = [p for p in E["positions"] if p["component_type"] == t]
        ids = {p["id"] for p in ps}
        cyc = sum(p["window_12m"]["cycles"] or 0 for p in ps) or None
        _cmp_rel(oracle_rel([f for f in COUNTED if f["position_id"] in ids],
                            sum(p["window_12m"]["hours"] for p in ps), cyc), exp)
    for key, exp in X["reliability_by_manufacturer"].items():
        t, mfr = key.split("|")
        cs = [c for c in E["components"] if c["component_type"] == t and c["manufacturer"] == mfr
              and c["usage_in_window_12m"]]
        fs = [f for f in COUNTED if COMP[f["component_id"]]["manufacturer"] == mfr
              and COMP[f["component_id"]]["component_type"] == t]           # S4-9: component installed at failure
        _cmp_rel(oracle_rel(fs, sum(c["usage_in_window_12m"]["hours"] for c in cs),
                            sum(c["usage_in_window_12m"]["cycles"] for c in cs)), exp)
    # the manufacturer split sums back to the component type
    pc = X["reliability_by_component_type"]["pneumatic_cylinder"]
    parts = [v for k, v in X["reliability_by_manufacturer"].items() if k.startswith("pneumatic_cylinder|")]
    assert sum(v["n"] for v in parts) == pc["n"] and sum(v["cycles"] for v in parts) == pc["cycles"]


def test_mttf_by_manufacturer_model():  # S4-11
    for key, exp in X["mttf_by_manufacturer_model"].items():
        mfr, model = key.split("|", 1)
        cs = [c for c in E["components"] if c["manufacturer"] == mfr and c["model"] == model]
        m = exp["measure"]
        failed = [c["usage_since_install"][m] for c in cs if c["removal_reason"] == "failure"]
        susp = [c for c in cs if c["removed_at"] and c["removal_reason"] != "failure"]
        assert (len(failed), len(susp)) == (exp["failures"], exp["suspensions"])
        assert close(sum(failed) / len(failed) if failed else None, exp["mttf"])
        assert exp["reason"] == (None if failed else "no_failures")
    w = X["wrong_answers"]["mttf_festo_counting_suspensions"]["value"]
    assert not close(w, X["mttf_by_manufacturer_model"]["Festo|DSBC-50-100-PPVA-N3"]["mttf"])


# ── work orders: lifecycle (domain.md §5.3, S4-5) ─────────────────────────────

ALLOWED = {("draft", "open"), ("open", "approved"), ("approved", "scheduled"), ("scheduled", "in_progress"),
           ("in_progress", "on_hold"), ("on_hold", "in_progress"), ("on_hold", "scheduled"),
           ("in_progress", "completed"), ("completed", "closed")}
BEFORE_COMPLETED = {"draft", "open", "approved", "scheduled", "in_progress", "on_hold"}


def allowed(wo: dict, a: str, b: str, body: dict | None = None) -> bool:
    if b == "cancelled":
        return a in BEFORE_COMPLETED and bool((body or {"cancel_reason": wo.get("cancel_reason")}).get("cancel_reason"))
    if (a, b) == ("open", "in_progress"):
        return wo["type"] == "corrective" and wo["urgency"] == "emergency"
    return (a, b) in ALLOWED


def test_work_order_histories_are_valid():
    for w in E["work_orders"]:
        h = w["history"]
        assert h[-1][0] == w["state"], w["id"]
        assert all(ts(a[1]) <= ts(b[1]) for a, b in zip(h, h[1:])), w["id"]
        for (a, _), (b, _) in zip(h, h[1:]):
            assert allowed(w, a, b), (w["id"], a, b)
        if w["state"] == "cancelled":
            assert w["cancel_reason"]
        if w["scheduled_for"]:
            assert any(s == "scheduled" for s, _ in h) and w["scheduled_at"] == next(t for s, t in h if s == "scheduled")
        if w["plan_id"]:
            assert w["type"] in ("preventive", "predictive")
        if w["failure_id"]:
            assert w["type"] == "corrective"


def test_rejected_transitions():
    for r in E["rejected_transitions"]:
        w = WO[r["wo"]]
        assert r["http"] == 422 and not allowed(w, r["from"], r["to"], r.get("body")), r


def test_late_work_orders():
    late = sorted(w["id"] for w in E["work_orders"] if w["scheduled_for"] and w["state"] in BEFORE_COMPLETED
                  and date.fromisoformat(w["scheduled_for"]) < TODAY)
    assert late == X["late_work_orders"]


# ── PMP, schedule compliance, backlog (kpis.md §3, S4-6, S4-7) ────────────────

P0, P1 = ts(E["report_period"]["start_utc"]), ts(E["report_period"]["stop_utc"])


def _at(w, state):
    return next((ts(t) for s, t in w["history"] if s == state), None)


def test_pmp():
    done = [w for w in E["work_orders"] if _at(w, "completed") and P0 <= _at(w, "completed") < P1]
    planned = [w for w in done if w["type"] in ("preventive", "predictive", "improvement")
               or (w["type"] == "corrective" and w["urgency"] == "deferred")]
    ph, th = sum(w["labor_hours"] for w in planned), sum(w["labor_hours"] for w in done)
    exp = X["pmp"]
    assert sorted(w["id"] for w in done) == exp["work_orders"]
    assert (ph, th) == (exp["planned_h"], exp["total_h"]) and close(ph / th, exp["pmp"])
    assert "OS-099" not in exp["work_orders"]                 # completed in October
    assert not close(X["wrong_answers"]["pmp_counting_open_wo_labor"]["value"], exp["pmp"])


def test_schedule_compliance():
    den, ok, late, nd, exc = [], [], [], [], []
    for w in E["work_orders"]:
        if not w["scheduled_for"]:
            continue
        d = date.fromisoformat(w["scheduled_for"])
        if not (P0.astimezone(TZ).date() <= d < P1.astimezone(TZ).date()):
            continue
        monday = d - timedelta(days=d.weekday())
        freeze = datetime(monday.year, monday.month, monday.day, tzinfo=TZ)
        if ts(w["scheduled_at"]) > freeze:
            exc.append([w["id"], "scheduled_after_freeze"])
            continue
        if w["state"] == "cancelled" and w["cancel_reason"] == "superseded":
            exc.append([w["id"], "cancelled_superseded"])
            continue
        den.append(w["id"])
        c = _at(w, "completed")
        tol = CFG["schedule_tolerance_days"]
        if c and c.astimezone(TZ).date() <= d + timedelta(days=tol):
            ok.append(w["id"])
        elif c:
            late.append(w["id"])
        else:
            nd.append(w["id"])
    exp = X["schedule_compliance"]
    assert (den, ok, late, nd, exc) == (exp["denominator"], exp["on_time_ids"], exp["late_ids"], exp["not_done_ids"],
                                         exp["excluded"])
    assert close(len(ok) / len(den), exp["compliance"])
    for k in ("compliance_counting_break_in", "compliance_counting_superseded"):
        assert not close(X["wrong_answers"][k]["value"], exp["compliance"]), k


def test_backlog():
    bl = [w for w in E["work_orders"] if w["state"] in ("approved", "scheduled", "on_hold", "in_progress")]
    rem = {w["id"]: (w["remaining_hours"] if w["remaining_hours"] is not None
                     else max(w["estimated_hours"] - w["labor_hours"], 0)) for w in bl}
    exp = X["backlog"]
    assert rem == exp["remaining_by_wo"] and sum(rem.values()) == exp["hours"] and len(bl) == exp["count"]
    cap = E["maintenance_capacity"]
    assert cap["weekly_capacity_hours"] == cap["technicians"] * cap["hours_per_week_each"] - cap["planned_absence_hours"]
    assert close(exp["hours"] / cap["weekly_capacity_hours"], exp["weeks"])
    od = [w["id"] for w in bl if w["scheduled_for"] and date.fromisoformat(w["scheduled_for"]) < TODAY]
    assert od == exp["overdue_ids"] and sum(rem[i] for i in od) == exp["overdue_hours"]
    assert exp["drafts_not_in_backlog"] == [w["id"] for w in E["work_orders"] if w["state"] == "draft"]
    w = X["wrong_answers"]
    assert w["backlog_with_estimates_only"]["hours"] != exp["hours"] and w["backlog_with_drafts"]["hours"] != exp["hours"]


def test_every_overdue_or_due_plan_has_an_open_or_draft_wo():
    """S4-4: a plan at ≥ 80 % always has one WO in draft..in_progress (a cancelled one is replaced)."""
    open_states = BEFORE_COMPLETED
    for pid, p in X["plans"].items():
        open_wos = [w["id"] for w in E["work_orders"] if w["plan_id"] == pid and w["state"] in open_states]
        if p["consumed"] >= CFG["due_soon_at"]:
            assert len(open_wos) == 1, (pid, open_wos)          # exactly one, never duplicates
    # the cancelled PL6 WO (OS-116) is replaced at the instant of cancellation (OS-122)
    cancelled = next(t for s, t in WO["OS-116"]["history"] if s == "cancelled")
    assert WO["OS-122"]["plan_id"] == "PL6" and WO["OS-122"]["history"][0] == ["draft", cancelled]


def test_readme_lists_e():
    text = (HERE / "README.md").read_text(encoding="utf-8")
    assert "scenario_e_maintenance.json" in text and "Cenário E" in text
    assert f"{X['pmp']['pmp']:.6f}".replace(".", ",") in text
    assert X["plans"]["PL3"]["next_due_date"].split("-")[2] in text


# ── docs consistency: clarifications and daily workflows ─────────────────────

DOCS = HERE.parent


def test_domain_has_sprint4_clarifications():
    text = (DOCS / "domain.md").read_text(encoding="utf-8")
    sec = text.split("## Esclarecimentos de manutenção (Sprint 4)", 1)
    assert len(sec) == 2, "domain.md must have the Sprint 4 maintenance clarifications section"
    for i in range(1, 13):
        assert f"### S4-{i} " in sec[1], f"S4-{i} missing"


def test_kpis_has_sprint3_clarifications():
    text = (DOCS / "kpis.md").read_text(encoding="utf-8")
    sec = text.split("## Esclarecimentos Sprint 3", 1)[1]
    for i, topic in enumerate(("PBT = 0", "Desempenho > 100", "Produto desconhecido", "PLN-", "Arredondamento"), 1):
        head = next(l for l in sec.splitlines() if l.startswith(f"### S3-{i} "))
        assert topic in head, (i, head)


def test_maintenance_workflows_doc_matches_scenario_e():
    text = (DOCS / "maintenance_workflows.md").read_text(encoding="utf-8")
    for i, persona in enumerate(("Planejador de manutenção", "Técnico de manutenção", "Supervisor de produção"), 2):
        assert f"## {i}. {persona}" in text, persona
    assert "## 5. Notificações" in text and "## 6. Telas do Sprint 4" in text
    # numbers quoted in the doc are the scenario's expected values
    assert f"{X['pmp']['pmp']:.6f}".replace(".", ",") in text
    assert f"{X['schedule_compliance']['compliance']:.6f}".replace(".", ",") in text
    assert f"{X['backlog']['hours']:.1f}".replace(".", ",") + " h" in text
    assert f"{X['backlog']['weeks']:.2f}".replace(".", ",") + " semana" in text
    assert f"{round(X['pmp']['pmp'] * 100, 1):.1f}".replace(".", ",") + " %" in text
    pl6 = X["plans"]["PL6"]["consumed"]
    assert f"{int(pl6 * 100)} %" in text and X["plans"]["PL6"]["state"] == "overdue"
    assert "rejected_transitions" in text and "expected.plans" in text
