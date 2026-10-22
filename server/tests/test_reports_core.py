"""docs/reports.md — pure parts of the reports module (no database):

* `report-weibull-cases`: Weibull MLE with right censoring, Fisher 90 % CIs, derived quantities, conditional
  probability, Johnson/Bernard ranks — relative tolerance 1e-6 (values with 2 decimals: absolute 0.01);
* `report-schedule-cases`: ready_at / send_at of the engine (§4.1), plus "never before send_at" in due_periods;
* `report-kpi-cases`: daily_production_e2 and monthly_maintenance_m built by the real builders from API payloads
  computed by the server's own KPI functions, then displayed (PDF cell formatting) and written to XLSX;
* `report-catalog`: the catalog served by the API.
"""
from __future__ import annotations

import io
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.services.maintenance import core
from src.services.oee import kpi
from src.services.oee.calendar import Calendar, ShiftDef
from src.services.reports import builders as B, catalog, engine as eng, fmt, pdf, periods as P, weibull, xlsx
from src.services.reports.source import Forbidden

DOC = (Path(__file__).resolve().parents[2] / "docs" / "reports.md").read_text(encoding="utf-8")


def block(tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", DOC, re.S)
    assert m, tag
    return json.loads(m.group(1))


def close(got, exp, key=""):
    if exp is None:
        return got is None
    tol = 0.01 if (isinstance(exp, float) and round(exp, 2) == exp and abs(exp) >= 1000) else 0.0
    return math.isclose(got, exp, rel_tol=1e-6, abs_tol=max(tol, 1e-9))


# ── Weibull (§2) ─────────────────────────────────────────────────────────

WB = {c["id"]: c for c in block("report-weibull-cases")}


@pytest.mark.parametrize("cid", list(WB))
def test_weibull_cases(cid):
    c = WB[cid]
    exp = c["expected"]
    r = weibull.analyze(c["failures"], c["suspensions"], c.get("nominal_life"))
    assert r["n"] == exp["n"] and r["r"] == exp["r"]
    assert close(r["simple_mttf"], exp["simple_mttf"])
    if exp.get("beta") is None:
        assert r["beta"] is None and r["reason"] == exp["reason"]
        assert r["eta"] is None and r["b10"] is None and r["mttf"] is None
        return
    for k in ("beta", "eta", "se_beta", "se_eta", "beta_lo", "beta_hi", "eta_lo", "eta_hi", "mttf", "b10",
              "b10_lo", "b10_hi", "b10_over_nominal"):
        if k in exp:
            assert close(r[k], exp[k]), (k, r[k], exp[k])
    assert r["low_confidence"] is exp["low_confidence"]
    assert r["pattern"] == exp["pattern"]
    assert r["pattern_text"] == weibull.PATTERN_TEXT[exp["pattern"]]
    for cond in c.get("conditional", []):
        p = weibull.conditional_failure(cond["age"], cond["rate_per_day"], r["beta"], r["eta"], cond["days"])
        assert math.isclose(p["p_fail"], cond["p_fail"], abs_tol=5e-7), (cond, p)
    if "johnson_ranks" in exp:
        pts = weibull.plot_points(c["failures"], c["suspensions"])
        assert [round(p["rank"], 6) for p in pts] == pytest.approx(exp["johnson_ranks"], rel=1e-6)
        assert [p["f"] for p in pts] == pytest.approx(exp["bernard_f"], rel=1e-6, abs=5e-7)


def test_weibull_cylinders_below_nominal_and_text():
    c = WB["weibull_cylinders"]
    r = weibull.analyze(c["failures"], c["suspensions"], c["nominal_life"])
    assert r["below_nominal"] is True                      # B10 / nominal = 0.7255 < 0.8
    assert weibull.pattern(0.5, 0.9) == "infant_mortality"
    assert weibull.pattern(0.7, 1.4) == "random"
    assert weibull.pattern(1.1, 3.0) == "wear_out"


def test_weibull_edges():
    # all failures at the same age and no larger suspension → no sign change of g(β) → degenerate
    r = weibull.analyze([1000, 1000, 1000], [])
    assert r["beta"] is None and r["reason"] == "degenerate"
    # u = 0 → null with no_recent_usage (never 0)
    p = weibull.conditional_failure(1000, 0, 2.0, 5000)
    assert p["p_fail"] is None and p["reason"] == "no_recent_usage"
    # r = 9 → low_confidence; r = 10 → not
    many = [float(1000 + 37 * i * i) for i in range(10)]
    assert weibull.analyze(many[:9], [500.0])["low_confidence"] is True
    assert weibull.analyze(many, [500.0])["low_confidence"] is False


def test_weibull_ci_overlap_rule():
    a = {"eta_lo": 10.0, "eta_hi": 20.0}
    assert weibull.eta_ci_overlap(a, {"eta_lo": 19.0, "eta_hi": 30.0}) is True
    assert weibull.eta_ci_overlap(a, {"eta_lo": 21.0, "eta_hi": 30.0}) is False
    assert weibull.eta_ci_overlap(a, {"eta_lo": None, "eta_hi": 30.0}) is None


# ── scheduling (§4.1) ────────────────────────────────────────────────────

def _case_calendar(c) -> Calendar:
    tz = ZoneInfo(c["tz"])
    if c["report"] == "shift":
        _, s0, s1 = c["shift_instance"]
        return Calendar(tz, [ShiftDef("A", "Turno A", s0, s1, tuple(range(7)))])
    if not c["shifts"]:
        return Calendar(tz, [ShiftDef(None, "24 h", "00:00", "00:00", tuple(range(7)))], default=True)
    return Calendar(tz, [ShiftDef(str(i), f"T{i}", a, b, tuple(c["weekdays"])) for i, (a, b) in enumerate(c["shifts"])])


def _case_period(c, cal) -> P.Period:
    if c["report"] == "shift":
        inst = cal.day_instances(date.fromisoformat(c["shift_instance"][0]))[0]
        return P.shift_period(inst)
    first, last = (date.fromisoformat(x) for x in c["period"])
    if c["report"] == "weekly_oee":
        return P.week_period(cal, first)
    if c["report"] == "monthly_maintenance":
        return P.month_period(cal, first.year, first.month)
    assert first == last
    return P.day_period(cal, first)


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", block("report-schedule-cases"), ids=lambda c: c["id"])
def test_schedule_cases(case):
    cal = _case_calendar(case)
    per = _case_period(case, cal)
    ready = P.ready_at(per, case["report"], case["settle_min"])
    send = P.send_at(per, case["report"], cal.tz, case.get("deliver_at_local"), case["settle_min"])
    assert ready == _utc(case["expected"]["ready_at"])
    assert send == _utc(case["expected"]["send_at"])
    assert send >= ready
    assert P.settle_min(case["report"]) == case["settle_min"]

    # the engine: the period is due exactly from send_at, never before
    sched = SimpleNamespace(report_type=case["report"], params={}, deliver_at_local=case.get("deliver_at_local"),
                            created_at=send - timedelta(days=30))
    before = eng.due_periods(sched, cal, send - timedelta(minutes=1), 7)
    after = eng.due_periods(sched, cal, send, 7)
    assert per.key not in {p.key for p, _, _ in before}
    hit = [(p, r, s) for p, r, s in after if p.key == per.key]
    assert len(hit) == 1 and hit[0][1] == ready and hit[0][2] == send


def test_schedule_not_backfilled_before_creation_and_catchup_window():
    cal = Calendar(ZoneInfo("America/Manaus"), [ShiftDef("A", "A", "06:00", "14:00", tuple(range(7)))])
    per = P.day_period(cal, date(2026, 10, 5))
    send = P.send_at(per, "daily_production", cal.tz, "06:30")
    sched = SimpleNamespace(report_type="daily_production", params={}, deliver_at_local="06:30",
                            created_at=send + timedelta(minutes=5))
    assert per.key not in {p.key for p, _, _ in eng.due_periods(sched, cal, send + timedelta(hours=1), 7)}
    sched.created_at = send - timedelta(days=60)
    # 8 days late: outside the 7-day catch-up window → download only
    assert per.key not in {p.key for p, _, _ in eng.due_periods(sched, cal, send + timedelta(days=8), 7)}
    assert per.key in {p.key for p, _, _ in eng.due_periods(sched, cal, send + timedelta(days=6), 7)}


def test_dst_gap_and_fold():
    # America/Sao_Paulo had DST until 2019: 2018-11-04 00:00 did not exist (→ 01:00), 2019-02-16 23:00 repeated
    tz = ZoneInfo("America/Sao_Paulo")
    gap = P.local_instant(date(2018, 11, 4), "00:30", tz)
    assert gap.astimezone(tz).strftime("%H:%M") == "01:00"
    fold = P.local_instant(date(2019, 2, 16), "23:30", tz)
    assert fold == datetime(2019, 2, 17, 1, 30, tzinfo=timezone.utc)      # first occurrence (−02:00)


def test_revision_threshold():
    base = {"pmp": 0.80, "schedule_compliance": 0.9, "ao": 0.95, "backlog_weeks": 2.0, "mtbf_h": 100.0, "mttr_min": 30}
    assert not eng.revision_needed(base, {**base, "pmp": 0.804})
    assert eng.revision_needed(base, {**base, "pmp": 0.806})          # > 0.5 pp
    assert not eng.revision_needed(base, {**base, "mtbf_h": 100.9})
    assert eng.revision_needed(base, {**base, "mtbf_h": 101.5})       # > 1 %
    assert eng.revision_needed(base, {**base, "mttr_min": None})


# ── report-kpi-cases through the builders ─────────────────────────────────

class FakeSource:
    """API stand-in: payloads produced by the server's own KPI functions (what the routes return)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get_ok(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        fn = self.routes.get(path)
        if fn is None:
            return []
        out = fn(params or {})
        if isinstance(out, Exception):
            raise out
        return out


def _shift_components(s) -> kpi.Components:
    c = kpi.Components(pot_s=s["pot_min"] * 60, pdot_s=s["planned_downtime_min"] * 60, covered_s=s["pot_min"] * 60,
                       adot_s=sum(s["unplanned_downtime_min"]) * 60, calendar_s=s["pot_min"] * 60)
    for p in s["products"]:
        c.produced += p["produced"]
        c.good += p["good"]
        c.scrap += p["produced"] - p["good"]
        c.ideal_produced_s += p["ideal_cycle_time_s"] * p["produced"]
        c.ideal_good_s += p["ideal_cycle_time_s"] * p["good"]
    return c


def _oee_payload(c: kpi.Components, points=()) -> dict:
    return {**kpi.oee(c), "points": list(points), "calendar_default": False}


def _cell_texts(doc, tz) -> list[str]:
    out = []
    for t in doc.tables():
        for row in t.rows:
            out += [pdf.fmt_cell(col, row.get(col.key), tz).replace(fmt.NBSP, " ") for col in t.columns]
    return out


KPI = {c["id"]: c for c in block("report-kpi-cases")}
KPIS_MD = (Path(__file__).resolve().parents[2] / "docs" / "kpis.md").read_text(encoding="utf-8")
KPI_CASES = {c["id"]: c for c in json.loads(re.search(r"```json kpi-test-cases\n(.*?)\n```", KPIS_MD, re.S).group(1))}


async def test_daily_production_e2_displays_sum_never_mean():
    case = KPI["daily_production_e2"]
    shifts = KPI_CASES[case["from_kpi_case"]]["input"]["shifts"]
    tz = ZoneInfo("America/Manaus")
    cal = Calendar(tz, [ShiftDef("A", "Turno A", "06:00", "14:00", tuple(range(7))),
                        ShiftDef("B", "Turno B", "14:00", "22:00", tuple(range(7)))])
    per = P.day_period(cal, date(2026, 10, 5))
    ca, cb = _shift_components(shifts[0]), _shift_components(shifts[1])
    total = kpi.Components()
    total.add(ca)
    total.add(cb)
    points = [{"label": "Turno A 05/10", **kpi.oee(ca), "good": ca.good, "scrap": ca.scrap},
              {"label": "Turno B 05/10", **kpi.oee(cb), "good": cb.good, "scrap": cb.scrap}]

    def oee(params):
        if params["start"] == per.start:               # the day → shifts A + B (S3-8: total = Σ shifts)
            return _oee_payload(total, points)
        return _oee_payload(kpi.Components())          # 4 previous weeks: no planned time

    src = FakeSource({"/api/oee": oee, "/api/losses/pareto": lambda p: [],
                      "/api/failures/": lambda p: Forbidden("/api/failures/")})
    plant = {"id": "p1", "name": "Planta Manaus", "timezone": tz.key,
             "lines": [{"id": "l1", "name": "Linha 1", "machines": []}]}
    viewer = SimpleNamespace(line_ids=[], role_set={"production_supervisor"})
    ctx = B.BuildCtx(tenant_id="t", report_type="daily_production", scope_type="plant", scope_id="p1", period=per,
                     tz=tz, cal=cal, now=per.end + timedelta(days=1), viewer=viewer, plant=plant)
    doc = await B.build_daily_production(src, ctx)
    disp = case["expected_display"]
    row = doc.summary_table.rows[0]
    show = {c.key: pdf.fmt_cell(c, row.get(c.key), tz).replace(fmt.NBSP, " ") for c in doc.summary_table.columns}
    for k in ("oee", "availability", "performance", "quality"):
        assert show[k] == disp[k], (k, show[k])
    assert math.isclose(row["oee"], KPI_CASES[case["from_kpi_case"]]["expected"]["oee"], abs_tol=1e-6)
    line_sec = doc.sections[0]
    shift_tbl = line_sec.blocks[0]
    assert [pdf.fmt_cell(shift_tbl.columns[1], r["oee"], tz).replace(fmt.NBSP, " ")
            for r in shift_tbl.rows[:2]] == disp["shift_oee"]
    texts = _cell_texts(doc, tz)
    assert case["must_not_display"]["oee"] not in texts
    # undefined Δ vs 4 weeks: "—" with a reason, never 0
    assert row["delta_pp"] is None and row["_reason"]["delta_pp"]
    # operator/supervisor without maintenance.view → the section is omitted, never filled
    assert any("Manutenção do dia" in o for o in doc.omitted)
    assert not any(s.title == "Manutenção do dia" for s in doc.sections)
    # no target registered → no target column (not 0, not "—")
    assert "target_oee" not in {c.key for c in doc.summary_table.columns}

    doc.generated_at = doc.data_until = per.end
    data = pdf.render(doc)
    assert data[:5] == b"%PDF-" and len(data) < 2 * 1024 * 1024
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(xlsx.render(doc)))
    assert {"Resumo", "OEE_por_turno", "Pareto", "Paradas", "Leia-me"} <= set(wb.sheetnames)
    ws = wb["OEE_por_turno"]
    head = [c.value for c in ws[1]]
    assert "pbt_min" in head and "oee" in head and "motivo" in head
    oee_col = head.index("oee") + 1
    assert math.isclose(ws.cell(row=2, column=oee_col).value, points[0]["oee"], abs_tol=1e-12)
    assert ws.cell(row=2, column=oee_col).number_format == "0.0%"
    pbt_col = head.index("pbt_min") + 1
    assert math.isclose(ws.cell(row=2, column=pbt_col).value, points[0]["pbt_s"] / 60)
    assert not ws.merged_cells.ranges


async def test_monthly_maintenance_m_display_and_flags():
    case = KPI["monthly_maintenance_m"]
    tz = ZoneInfo("America/Sao_Paulo")
    cal = Calendar(tz, [ShiftDef("A", "A", "06:00", "14:00", tuple(range(7)))])
    per = P.month_period(cal, 2026, 9)
    t0 = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    # M1 — PMP: labor hours by type (kpis.md case pmp)
    hours = KPI_CASES["pmp"]["input"]["labor_hours"]
    kinds = {"preventive": ("preventive", None), "predictive": ("predictive", None), "improvement": ("improvement", None),
             "corrective_deferred": ("corrective", "deferred"), "corrective_emergency": ("corrective", "emergency")}
    facts = [core.WoFacts(f"w{k}", f"{i:09d}", kinds[k][0], kinds[k][1], "completed", h, h, 0.0, None, None, t0, None)
             for i, (k, h) in enumerate(hours.items())]
    pm = core.pmp(facts, *P.calendar_bounds(cal, per.first_day, per.last_day)[:2])
    # M2 — compliance 34 on time, 3 late, 3 not done (scheduled before the freeze)
    sc_in = KPI_CASES["schedule_compliance"]["input"]
    wos = []
    d = date(2026, 9, 15)
    frozen = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(sc_in["scheduled"]):
        done = (datetime(2026, 9, 15, 15, tzinfo=timezone.utc) if i < sc_in["on_time"] else
                datetime(2026, 9, 25, 15, tzinfo=timezone.utc) if i < sc_in["on_time"] + sc_in["late"] else None)
        wos.append(core.WoFacts(f"s{i}", f"{100 + i:09d}", "preventive", None, "completed" if done else "scheduled",
                                1.0, 1.0, None, d, frozen, done, None))
    sc = core.schedule_compliance(wos, date(2026, 9, 1), date(2026, 10, 1), tz)
    # M3 — backlog 260 h / 130 h per week
    bl_in = KPI_CASES["backlog"]["input"]
    bl = core.backlog([core.WoFacts("b", "000000999", "preventive", None, "approved", 0.0,
                                    bl_in["open_estimated_hours"], None, None, None, None, None)],
                      date(2026, 10, 1), bl_in["weekly_capacity_hours"])
    kp = {"pmp": pm["pmp"], "pmp_reason": pm["reason"], "schedule_compliance": sc["schedule_compliance"],
          "compliance_reason": sc["reason"], "backlog_weeks": bl["backlog_weeks"], "backlog_hours": bl["backlog_hours"],
          "backlog_weeks_reason": bl["backlog_weeks_reason"], "total_hours": pm["total_hours"],
          "planned_hours": pm["planned_hours"], "predictive_no_defect_hours": 0.0}
    # R6 — Ao, R1/R3 — MTBF, MTTR
    ao_in = KPI_CASES["availability_operational"]["input"]
    ao_c = kpi.Components(pot_s=ao_in["pot_h"] * 3600, covered_s=ao_in["pot_h"] * 3600,
                          ao_excluded_s=ao_in["excluded_h"] * 3600,
                          ao_equipment_s=(ao_in["failure_downtime_h"] + ao_in["maintenance_downtime_h"]) * 3600)
    restores = KPI_CASES["mttr"]["input"]["restore_durations_min"]
    fails = [(f["severity"], None) for f in KPI_CASES["mtbf"]["input"]["failures"]]
    counted = [i for i, (s, _) in enumerate(fails) if s in ("critical", "degraded")]
    for i, m in zip(counted, restores):
        fails[i] = (fails[i][0], m * 60.0)
    rel = kpi.reliability(KPI_CASES["mtbf"]["input"]["operating_hours"] * 3600, fails)
    src = FakeSource({"/api/maintenance/kpis": lambda p: kp, "/api/oee": lambda p: _oee_payload(ao_c),
                      "/api/reliability": lambda p: rel, "/api/maintenance/reliability": lambda p: {"rows": []},
                      "/api/predictive/evaluation": lambda p: Forbidden("x")})
    plant = {"id": "p1", "name": "Planta SP", "timezone": tz.key, "lines": []}
    ctx = B.BuildCtx(tenant_id="t", report_type="monthly_maintenance", scope_type="plant", scope_id="p1", period=per,
                     tz=tz, cal=cal, now=per.end + timedelta(days=1), viewer=SimpleNamespace(line_ids=[]), plant=plant)
    doc = await B.build_monthly_maintenance(src, ctx)
    row = doc.summary_table.rows[0]
    show = {c.key: pdf.fmt_cell(c, row.get(c.key), tz).replace(fmt.NBSP, " ") for c in doc.summary_table.columns}
    disp = case["expected_display"]
    assert show["pmp"] == disp["pmp"]
    assert show["schedule_compliance"] == disp["schedule_compliance"]
    assert show["backlog_weeks"] == disp["backlog"]
    assert show["mtbf_h"] == disp["mtbf"]
    assert show["mttr_min"] == disp["mttr"]
    assert show["ao"] == disp["ao"]
    for k, flag in case["flags"].items():
        key = {"backlog": "backlog_weeks"}.get(k, k)
        assert row["_flag"][key] == flag, (k, row["_flag"])
    # history months: backlog is a snapshot — "—" with a reason, never 0
    assert doc.sections[0].blocks[1].rows[0]["backlog_weeks"] is None
    assert doc.sections[0].blocks[1].rows[0]["_reason"]["backlog_weeks"] == "snapshot_only"
    # hours by type add up to the KPI total (same API data, additive)
    assert any("Preditiva" in o for o in doc.omitted)
    doc.generated_at = doc.data_until = per.end
    assert pdf.render(doc)[:5] == b"%PDF-"


async def test_monthly_without_kpi_permission_has_no_hours():
    tz = ZoneInfo("America/Sao_Paulo")
    cal = Calendar(tz, [])
    per = P.month_period(cal, 2026, 9)
    src = FakeSource({"/api/maintenance/kpis": lambda p: Forbidden("kpis")})
    ctx = B.BuildCtx(tenant_id="t", report_type="monthly_maintenance", scope_type="plant", scope_id="p1", period=per,
                     tz=tz, cal=cal, now=per.end, viewer=SimpleNamespace(line_ids=[]),
                     plant={"id": "p1", "name": "P", "timezone": tz.key, "lines": []})
    doc = await B.build_monthly_maintenance(src, ctx)
    assert any("maintenance.kpis" in o for o in doc.omitted)
    assert not [p for p, _ in src.calls if p == "/api/work-orders/"]      # hours never fetched


def test_catalog_block_matches_service():
    cat_doc = block("report-catalog")
    assert [c["code"] for c in cat_doc] == [c["code"] for c in catalog.CATALOG]
    for c in cat_doc:
        s = catalog.BY_CODE[c["code"]]
        assert s["formats"] == c["formats"] and s["settle_min"] == c["settle_min"]
        assert s["deliver_at_local"] == c["deliver_at_local"] and s["recipients"] == c["recipients"]
        assert s["period"] == c["period"]
        assert P.settle_min(c["code"]) == c["settle_min"]
        for k in ("weekday", "month_day"):
            if k in c:
                assert s[k] == c[k]


def test_pt_br_formatting():
    assert fmt.pct_plain(0.83659) == "83,7 %"
    assert fmt.pct_plain(0.84375) == "84,4 %"
    assert fmt.pct_plain(None) == "—"
    assert fmt.number(1733.333, 1) == "1.733,3"
    assert fmt.minutes(25 * 60).replace(fmt.NBSP, " ") == "25 min"
    assert fmt.minutes(90).replace(fmt.NBSP, " ") == "1,5 min"
    assert fmt.with_reason(fmt.pct(None), "no_planned_time") == "— (sem tempo planejado neste período)"


def test_productive_day_today_follows_the_night_shift():
    """StopsPage 'Exportar paradas do dia' sends productive_day='today': at 02:00 in Turno C (22:00–06:00) that is
    the productive day the shift started (C19/S3-8), not the civil date."""
    tz = ZoneInfo("America/Sao_Paulo")
    cal = Calendar(tz, [ShiftDef("A", "Turno A", "06:00", "14:00", tuple(range(7))),
                        ShiftDef("B", "Turno B", "14:00", "22:00", tuple(range(7))),
                        ShiftDef("C", "Turno C", "22:00", "06:00", tuple(range(7)))])
    at_2am = datetime(2026, 10, 7, 2, 0, tzinfo=tz).astimezone(timezone.utc)
    per = P.resolve_period("daily_production", cal, {"productive_day": "today"}, at_2am)
    assert per.first_day == date(2026, 10, 6)
    at_10am = datetime(2026, 10, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc)
    assert P.resolve_period("daily_production", cal, {"productive_day": "today"}, at_10am).first_day == date(2026, 10, 7)
    # an explicit ISO date is unchanged
    assert P.resolve_period("daily_production", cal, {"productive_day": "2026-10-01"}, at_2am).first_day == date(2026, 10, 1)
