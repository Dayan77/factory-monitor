"""docs/audit_kpis.md — executable part of the independent KPI audit (Sprint 6).

Two kinds of tests:

1. **Conformance** (`test_conformance_*`): every case of the `kpi-test-cases` block of docs/kpis.md §6 goes through
   the *server's own* pure functions (server/src/services/...). `server/tests/test_kpi_spec_cases.py` checks the same
   block against a reference implementation written inside the test, so a server function could drift without
   failing it (audit item A-17). Every case id must be mapped here or listed in `COVERED_ELSEWHERE`.

2. **Divergences** (`test_A<n>_*`): the concrete counter-examples of docs/audit_kpis.md. Until Sprint 6 each item
   had a `..._code_today` test (the number the code gave) and an `xfail(strict=True)` `..._spec` test. Sprint 7
   fixed every item (docs/kpis.md "Decisões Sprint 7"): only the `..._spec` tests remain and they must pass; the
   cases that need the database / InfluxDB live in server/tests/test_audit_s7.py.

Run:  .venv/bin/python -m pytest docs/scenarios/test_audit_kpis.py -q
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

try:
    from src.services.maintenance import core as mcore                      # noqa: E402
    from src.services.maintenance.kpis import attribute_component           # noqa: E402
    from src.services.oee import kpi                                        # noqa: E402
    from src.services.oee.calendar import Calendar, ExceptionDef, ShiftDef  # noqa: E402
    from src.services.oee.detection import classify_cycle_gaps              # noqa: E402
    from src.services.oee.engine import EventLite, MachineData, MachineInfo, ProductPri, components  # noqa: E402
    from src.services.predictive.core import make_rate_fn                   # noqa: E402
    from src.services.usage import core as ucore                            # noqa: E402
except Exception as exc:  # pragma: no cover - environment without server deps
    pytest.skip(f"server code not importable: {exc}", allow_module_level=True)

KPIS = (ROOT / "docs" / "kpis.md").read_text(encoding="utf-8")
CASES = {c["id"]: c for c in json.loads(re.search(r"```json kpi-test-cases\n(.*?)\n```", KPIS, re.S).group(1))}
AUDIT = (ROOT / "docs" / "audit_kpis.md").read_text(encoding="utf-8")

UTC = timezone.utc
TZ = ZoneInfo("America/Manaus")


def close(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def local(h: int, m: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=TZ).astimezone(UTC)


# ═══ 1. conformance: kpis.md §6 through the server's functions ═══════════════

def _shift(s: dict) -> kpi.Components:
    c = kpi.Components(pot_s=s["pot_min"] * 60, pdot_s=s["planned_downtime_min"] * 60,
                       adot_s=sum(s["unplanned_downtime_min"]) * 60)
    for p in s["products"]:
        c.produced += p["produced"]
        c.good += p["good"]
        c.scrap += p["produced"] - p["good"]
        c.ideal_produced_s += p["ideal_cycle_time_s"] * p["produced"]
        c.ideal_good_s += p["ideal_cycle_time_s"] * p["good"]
    return c


def _wo(i: int, **kw) -> mcore.WoFacts:
    base = dict(id=f"wo{i}", label=f"{i:09d}", type="preventive", urgency=None, state="completed", labor_hours=0.0,
                estimated_hours=0.0, remaining_hours=None, scheduled_for=None, scheduled_at=None, completed_at=None,
                cancel_reason=None)
    base.update(kw)
    return mcore.WoFacts(**base)


def check_oee_single_shift(c):
    r = kpi.oee(_shift(c["input"]))
    e = c["expected"]
    assert close(r["pbt_s"] / 60, e["pbt_min"]) and close(r["apt_s"] / 60, e["apt_min"])
    for k in ("availability", "performance", "quality", "oee"):
        assert close(r[k], e[k]), k


def check_oee_two_shifts_mix(c):
    parts = [_shift(s) for s in c["input"]["shifts"]]
    e = c["expected"]
    assert [round(kpi.oee(p)["oee"], 6) for p in parts] == e["shift_oee"]
    b = kpi.oee(parts[1])
    assert close(b["performance"], e["shift_b_performance"]) and close(b["quality"], e["shift_b_quality"])
    day = kpi.oee(kpi.total(parts))
    assert close(day["pbt_s"] / 60, e["pbt_min"]) and close(day["apt_s"] / 60, e["apt_min"])
    for k in ("availability", "performance", "quality", "oee"):
        assert close(day[k], e[k]), k
    assert not close(day["oee"], e["wrong_mean_of_shift_oee"])


def check_teep_day(c):
    i, e = c["input"], c["expected"]
    comp = kpi.Components(pot_s=i["pbt_min"] * 60, calendar_s=i["calendar_min"] * 60, produced=1, good=1,
                          ideal_produced_s=i["net_productive_min"] * 60, ideal_good_s=i["net_productive_min"] * 60)
    r = kpi.oee(comp)
    assert close(r["loading"], e["loading"]) and close(r["teep"], e["teep"])


def check_micro_stops_from_cycle_gaps(c):
    i, e = c["input"], c["expected"]
    g = classify_cycle_gaps(i["ideal_cycle_time_s"], i["micro_stop_threshold_s"], i["cycle_gaps_s"])
    assert (g.speed_loss_s, g.micro_stop_count, g.micro_stop_s, g.downtime_events_s) == \
        (e["speed_loss_s"], e["micro_stop_count"], e["micro_stop_s"], e["downtime_events_s"])


def check_cadence(c):
    i, e = c["input"], c["expected"]
    r = kpi.oee(kpi.Components(pot_s=i["pbt_min"] * 60, adot_s=(i["pbt_min"] - i["apt_min"]) * 60,
                               produced=i["produced"]))
    assert close(r["cadence_planned_per_hour"], e["parts_per_hour_planned"])
    assert close(r["cadence_running_per_hour"], e["parts_per_hour_running"])
    assert close(3600 / i["ideal_cycle_time_s"], e["parts_per_hour_ideal"])     # reference line (UI)


def check_loss_pareto(c):
    i, e = c["input"], c["expected"]
    pri = i["ideal_cycle_time_s"]
    ideal_produced_s = i["ideal_time_produced_min"] * 60
    comp = kpi.Components(pot_s=i["pbt_min"] * 60, adot_s=(i["pbt_min"] - i["apt_min"]) * 60,
                          ideal_produced_s=ideal_produced_s, ideal_good_s=ideal_produced_s - pri * i["scrap"],
                          produced=ideal_produced_s / pri, micro_s=i["micro_stop_min"] * 60, micro_count=1,
                          losses={k: [v * 60, 1] for k, v in i["downtime_by_reason_min"].items()})
    rows = kpi.pareto(comp, {k: (k.split("-")[0], k, False) for k in i["downtime_by_reason_min"]})
    got = [[r["reason_code"], r["duration_s"] / 60, r["pct"], r["cumulative_pct"]] for r in rows]
    assert close(sum(r["duration_s"] for r in rows) / 60, e["total_loss_min"])
    assert [g[0] for g in got] == [x[0] for x in e["items"]]
    for g, x in zip(got, e["items"]):
        assert close(g[1], x[1]) and abs(g[2] - x[2]) < 5e-7 and abs(g[3] - x[3]) < 5e-7, x[0]


def check_mtbf(c):
    i, e = c["input"], c["expected"]
    fl = [(f["severity"], None) for f in i["failures"]]
    r = kpi.reliability(i["operating_hours"] * 3600, fl)
    assert r["failures"] == e["n"] and r["incipient"] == e.get("incipient", r["incipient"])
    assert (r["mtbf_h"] is None) if e["mtbf_h"] is None else close(r["mtbf_h"], e["mtbf_h"])
    if "reason" in e:
        assert r["reason"] == e["reason"] and close(r["operating_h"], e["operating_hours"])
    facts = [mcore.FailureFacts(str(k), f["severity"], local(8), None, "p", None) for k, f in enumerate(i["failures"])]
    row = mcore.reliability_row(facts, i["operating_hours"], None)
    assert row["n"] == e["n"]
    assert (row["mtbf_h"] is None) if e["mtbf_h"] is None else close(row["mtbf_h"], e["mtbf_h"])


def check_mcbf(c):
    i, e = c["input"], c["expected"]
    facts = [mcore.FailureFacts(str(k), "critical", local(8), None, "p", None) for k in range(i["failures"])]
    assert close(mcore.reliability_row(facts, 100.0, i["cycles"])["mcbf_cycles"], e["mcbf_cycles"])


def check_mttr(c):
    i, e = c["input"], c["expected"]
    r = kpi.reliability(3600.0, [("critical", d * 60) for d in i["restore_durations_min"]])
    assert close(r["mttr_s"] / 60, e["mttr_min"])
    facts = [mcore.FailureFacts(str(k), "critical", local(8), local(8) + timedelta(minutes=d), "p", None)
             for k, d in enumerate(i["restore_durations_min"])]
    row = mcore.reliability_row(facts, 10.0, None)
    assert close(row["mttr_min"], e["mttr_min"]) and row["restored"] == len(i["restore_durations_min"])


def check_mttf(c):
    i, e = c["input"], c["expected"]
    life = [mcore.ComponentLife(f"f{k}", "SKF", "6205", "bearing", {"hours": 20000}, {"hours": a}, False, True,
                                "failure") for k, a in enumerate(i["failed_ages_h"])]
    life += [mcore.ComponentLife(f"s{k}", "SKF", "6205", "bearing", {"hours": 20000}, {"hours": a}, False, True,
                                 "preventive_replacement") for k, a in enumerate(i["suspended_ages_h"])]
    m = mcore.mttf_row(life)
    assert close(m["mttf_h"], e["mttf_h"]) and m["failures"] == e["failures"] and m["suspensions"] == e["suspensions"]


def check_failure_rate(c):
    i, e = c["input"], c["expected"]
    r = kpi.reliability(i["operating_hours"] * 3600, [("critical", None)] * i["failures"])
    assert abs(r["failure_rate_per_h"] - e["per_hour"]) < 1e-7
    assert abs(r["failure_rate_per_h"] * 1e6 - e["per_million_hours"]) < 1e-3
    # per_million_cycles: not exposed by the server → audit A-8 (test_A8_*)


def check_availability_inherent(c):
    i, e = c["input"], c["expected"]
    n = 4
    r = kpi.reliability(i["mtbf_h"] * n * 3600, [("critical", i["mttr_h"] * 3600)] * n)
    assert close(r["availability_inherent"], e["ai"])


def check_availability_operational(c):
    i, e = c["input"], c["expected"]
    comp = kpi.Components(pot_s=i["pot_h"] * 3600, ao_excluded_s=i["excluded_h"] * 3600,
                          ao_equipment_s=(i["failure_downtime_h"] + i["maintenance_downtime_h"]) * 3600)
    assert close(kpi.availability_operational(comp), e["ao"])
    # how the engine fills ao_equipment_s from the calendar: audit A-1 (test_A1_*)


def check_pmp(c):
    h, e = c["input"]["labor_hours"], c["expected"]
    kinds = {"preventive": ("preventive", None), "predictive": ("predictive", None),
             "improvement": ("improvement", None), "corrective_deferred": ("corrective", "deferred"),
             "corrective_emergency": ("corrective", "emergency")}
    wos = [_wo(k, type=kinds[name][0], urgency=kinds[name][1], labor_hours=v, completed_at=local(10))
           for k, (name, v) in enumerate(h.items())]
    p = mcore.pmp(wos, local(0), local(0, day=6))
    assert close(p["planned_hours"], e["planned_h"]) and close(p["total_hours"], e["total_h"])
    assert close(p["pmp"], e["pmp"])


def check_schedule_compliance(c):
    i, e = c["input"], c["expected"]
    d = date(2026, 10, 7)                                    # Wednesday; frozen Monday 05/10 00:00 local
    frozen = datetime(2026, 10, 2, 12, tzinfo=UTC)
    on_time = local(15, day=7)
    late = local(15, day=9)
    wos = [_wo(k, state="completed", scheduled_for=d, scheduled_at=frozen, completed_at=on_time)
           for k in range(i["on_time"])]
    wos += [_wo(100 + k, state="completed", scheduled_for=d, scheduled_at=frozen, completed_at=late)
            for k in range(i["late"])]
    wos += [_wo(200 + k, state="scheduled", scheduled_for=d, scheduled_at=frozen) for k in range(i["not_done"])]
    sc = mcore.schedule_compliance(wos, date(2026, 10, 5), date(2026, 10, 12), TZ)
    assert sc["scheduled"] == i["scheduled"] and close(sc["schedule_compliance"], e["compliance"])
    assert (sc["on_time"], sc["late"], sc["not_done"]) == (i["on_time"], i["late"], i["not_done"])


def check_backlog(c):
    i, e = c["input"], c["expected"]
    wos = [_wo(k, state="approved", estimated_hours=i["open_estimated_hours"] / 4) for k in range(4)]
    b = mcore.backlog(wos, date(2026, 10, 5), i["weekly_capacity_hours"])
    assert close(b["backlog_weeks"], e["weeks"])


def check_life_consumed(c):
    i, e = c["input"], c["expected"]
    life, measure = ucore.life_consumed(i["nominal"], i["usage_since_install"])
    assert close(life, e["life"]) and measure == e["measure"]


def check_rul(c):
    i, e = c["input"], c["expected"]
    r = ucore.rul(date.fromisoformat(i["today"]), i["nominal"], i["usage_since_install"], i["usage_last_14_days"])
    assert (r.days, r.date.isoformat(), r.measure) == (e["days"], e["date"], e["measure"])
    assert close(r.remaining, e["remaining"]) and close(r.rate_per_day, e["rate_per_day"])


def check_plan_due(c):
    i, e = c["input"], c["expected"]
    today = date.fromisoformat(i["today"])
    last_reset = datetime.combine(today - timedelta(days=i["since_maintenance"]["days"]),
                                  datetime.min.time(), tzinfo=TZ).replace(hour=12)
    used = {m: v for m, v in i["since_maintenance"].items() if m != "days"}
    window = {m: v * 14 for m, v in i["rate_per_day"].items()}
    st = mcore.compute_plan_status(i["interval"], last_reset, {m: 0.0 for m in used}, used, window, today, TZ)
    assert close(st.consumed, e["consumed"]) and st.state == e["state"]
    assert (st.due_trigger, st.days_to_due, st.next_due_date.isoformat()) == (e["trigger"], e["days"], e["date"])


def check_counter_delta(c):
    i, e = c["input"], c["expected"]
    got = [ucore.counter_delta(x["prev"], x["cur"], x["counter_max"], x.get("dt_s"), x.get("max_rate_per_s"))
           for x in i["cases"]]
    assert [g[0] for g in got] == e["deltas"] and [g[1] for g in got] == e["events"]


CHECKS = {
    "oee": check_oee_single_shift, "oee_aggregate": check_oee_two_shifts_mix, "teep": check_teep_day,
    "micro_stops": check_micro_stops_from_cycle_gaps, "cadence": check_cadence, "loss_pareto": check_loss_pareto,
    "mtbf": check_mtbf, "mcbf": check_mcbf, "mttr": check_mttr, "mttf": check_mttf, "failure_rate": check_failure_rate,
    "availability_inherent": check_availability_inherent, "availability_operational": check_availability_operational,
    "pmp": check_pmp, "schedule_compliance": check_schedule_compliance, "backlog": check_backlog,
    "life_consumed": check_life_consumed, "rul": check_rul, "plan_due": check_plan_due,
    "counter_delta": check_counter_delta,
}
# estimated cycles live in the usage topology (DB); tested by server/tests/test_usage_core.py
COVERED_ELSEWHERE = {"estimated_cycles": "server/tests/test_usage_core.py::test_estimated_cycles_example"}


def test_every_kpi_case_is_audited():
    missing = [cid for cid, c in CASES.items() if c["kpi"] not in CHECKS and c["kpi"] not in COVERED_ELSEWHERE]
    assert not missing, f"kpis.md §6 cases without a server conformance check: {missing}"


@pytest.mark.parametrize("cid", [cid for cid, c in CASES.items() if c["kpi"] in CHECKS])
def test_conformance(cid):
    CHECKS[CASES[cid]["kpi"]](CASES[cid])


# ═══ 2. divergences (docs/audit_kpis.md) ════════════════════════════════════

def _machine(cal: Calendar, events: list[EventLite]) -> MachineData:
    info = MachineInfo("m", "t", "Prensa P1", "l", "p", TZ, "running", 120, 30.0, {})
    return MachineData(info, cal, events, [(local(6), local(14))], [], {}, [], ProductPri(fallback_pri=30.0))


def _shift_a(breaks=(), exceptions=()) -> Calendar:
    return Calendar(TZ, [ShiftDef("A", "Turno A", "06:00", "14:00", tuple(range(7)), tuple(breaks))], list(exceptions))


def _a1_components() -> kpi.Components:
    """Turno A 06–14 com refeição PLN-01 12:00–12:30, preventiva PLN-03 no calendário 08:00–09:00 e uma falha
    EQP-03 de 20 min (10:00–10:20)."""
    cal = _shift_a([{"start_local": "12:00", "end_local": "12:30", "reason_code": "PLN-01"}],
                   [ExceptionDef(date(2026, 10, 5), "planned_stop", "A", "08:00", "09:00", "PLN-03")])
    md = _machine(cal, [EventLite("e1", local(10), local(10, 20), "EQP-03", False)])
    return components(md, local(6), local(14), local(23))


# A-1 corrigido (Sprint 6, correções pré-merge): a preventiva PLN-03 do calendário entra em D_eq.
def test_A1_ao_calendar_preventive_spec():
    # T_req = 480 − 30 (PLN-01) = 450; D_eq = 20 (EQP) + 60 (PLN-03) = 80 → Ao = 370/450
    assert close(kpi.availability_operational(_a1_components()), 370 / 450)


def _a14_components() -> kpi.Components:
    """Reunião PLN-02 declarada 10:00–10:30 sobre uma falha EQP-03 10:00–10:20 (S3-4: a planejada prevalece)."""
    md = _machine(_shift_a(), [EventLite("p1", local(10), local(10, 30), "PLN-02", True),
                               EventLite("e1", local(10), local(10, 20), "EQP-03", False)])
    return components(md, local(6), local(14), local(23))


# A-14 corrigido (Sprint 7): a parte de uma falha sob uma parada PLN-01/02/04/05 declarada sai de T_req e de D_eq.
def test_A14_ao_overlap_counted_twice_spec():
    c = _a14_components()
    assert c.adot_s == 0.0                                        # OEE: planned prevails (unchanged)
    assert close(kpi.availability_operational(c), 1.0)            # T_req 450, D_eq 0


def test_A14_ao_partial_overlap_spec():
    """Falha EQP-03 10:00–10:40 com DDS PLN-02 10:00–10:30: só os 10 min fora do DDS são D_eq → Ao = 440/450."""
    md = _machine(_shift_a(), [EventLite("p1", local(10), local(10, 30), "PLN-02", True),
                               EventLite("e1", local(10), local(10, 40), "EQP-03", False)])
    assert close(kpi.availability_operational(components(md, local(6), local(14), local(23))), 440 / 450)


# A-2 corrigido (Sprint 7, kpis.md S7-2): dias de histórico = min(14, desde a instalação, desde o 1º dado).
def test_A2_rul_rate_uses_the_days_with_data_spec():
    """Componente instalado há 400 dias, com dados só nos últimos 5 dias: 100 000 ciclos em 5 dias = 20 000/dia."""
    now = local(10, 30)
    start, end, days = ucore.rate_window(now, now - timedelta(days=400), now - timedelta(days=5))
    assert close(days, 5.0) and end == now.replace(minute=0) and start == end - timedelta(days=5)
    r = ucore.rul(date(2026, 10, 5), {"cycles": 5_000_000}, {"cycles": 3_600_000}, {"cycles": 100_000},
                  history_days=days)
    assert (r.days, r.date.isoformat(), r.low_confidence) == (70, "2026-12-14", True)
    # com 14 dias ou mais de dados vale a janela cheia (U3 inalterado)
    _, _, d14 = ucore.rate_window(now, now - timedelta(days=400), now - timedelta(days=60))
    assert d14 == 14.0
    r14 = ucore.rul(date(2026, 10, 5), {"cycles": 5_000_000}, {"cycles": 3_600_000}, {"cycles": 280_000}, d14)
    assert (r14.days, r14.low_confidence) == (70, False)
    # menos de 3 dias de dados: sem projeção
    _, _, d2 = ucore.rate_window(now, now - timedelta(days=400), now - timedelta(days=2))
    assert ucore.rul(date(2026, 10, 5), {"cycles": 5_000_000}, {"cycles": 3_600_000}, {"cycles": 40_000},
                     d2).reason == "insufficient_data"


def test_A3_plan_rate_uses_the_days_with_data_spec():
    """A-3 corrigido: U4 com a posição conectada há 5 dias (100 000 ciclos em 5 dias = 20 000/dia real). O serviço
    passa os dias com dados (maintenance/usage.py:data_days); a função pura expõe low_confidence."""
    st5 = mcore.compute_plan_status({"cycles": 2_000_000, "days": 180}, datetime(2026, 8, 6, 12, tzinfo=UTC),
                                    {"cycles": 0.0}, {"cycles": 1_700_000}, {"cycles": 100_000}, date(2026, 10, 5),
                                    TZ, window_days=5)
    assert (st5.days_to_due, st5.next_due_date.isoformat(), st5.low_confidence) == (15, "2026-10-20", True)
    st14 = mcore.compute_plan_status({"cycles": 2_000_000, "days": 180}, datetime(2026, 8, 6, 12, tzinfo=UTC),
                                     {"cycles": 0.0}, {"cycles": 1_700_000}, {"cycles": 280_000}, date(2026, 10, 5), TZ)
    assert st14.low_confidence is False
    # menos de 3 dias com dados: sem projeção por uso (insufficient_data), só o gatilho de calendário
    st2 = mcore.compute_plan_status({"cycles": 2_000_000}, datetime(2026, 8, 6, 12, tzinfo=UTC), {"cycles": 0.0},
                                    {"cycles": 1_700_000}, {"cycles": 40_000}, date(2026, 10, 5), TZ, window_days=2)
    assert st2.days_to_due is None and st2.reason == "insufficient_data" and st2.low_confidence
    import inspect
    from src.services.maintenance import service as msvc
    assert "window_days=days" in inspect.getsource(msvc.evaluate_plans)


# A-4 corrigido (Sprint 7, S7-1): falhas sem tempo de operação → MTBF/Ai null + reason.
def test_A4_mtbf_zero_without_operating_time_spec():
    r = kpi.reliability(0.0, [("critical", 600.0), ("degraded", 1200.0)])
    assert r["mtbf_h"] is None and r["availability_inherent"] is None and r["reason"] == "insufficient_data"
    assert r["failures"] == 2 and close(r["mttr_s"], 900.0) and r["failure_rate_per_h"] is None
    row = mcore.reliability_row([mcore.FailureFacts("f", "critical", local(8), local(8, 10), "p", None)], 0.0, None)
    assert row["mtbf_h"] is None and row["reason"] == "insufficient_data" and close(row["mttr_min"], 10.0)
    assert mcore.reliability_row([], 0.0, None)["reason"] == "no_failures"


# A-5 corrigido (Sprint 7): S3-1 — Pareto com PBT = 0 é lista vazia.
def test_A5_pareto_without_planned_time_spec():
    assert kpi.pareto(kpi.Components(pot_s=3600, pdot_s=3600), {}) == []
    assert kpi.pareto(kpi.Components(pot_s=3600, pdot_s=3600), {}, by="category") == []


# A-6 corrigido (Sprint 7): failures_outside_production nas duas APIs (DB: server/tests/test_audit_s7.py).
def test_A6_failures_outside_production_in_reliability_spec():
    r = kpi.reliability(6.5 * 3600, [("critical", 1200.0)] * 4, outside_production=3)
    assert r["failures_outside_production"] == 3
    facts = [mcore.FailureFacts(str(k), "critical", local(8), None, "p", None, outside_production=k > 0)
             for k in range(4)]
    assert mcore.reliability_row(facts, 6.5, None)["failures_outside_production"] == 3


# A-8 corrigido (Sprint 7): λc por 10⁶ ciclos.
def test_A8_failure_rate_per_million_cycles_spec():
    facts = [mcore.FailureFacts(str(k), "critical", local(8), None, "p", None) for k in range(3)]
    row = mcore.reliability_row(facts, 720.0, 2_400_000)
    assert close(row["failure_rate_per_million_cycles"], 1.25)
    assert mcore.reliability_row([], 720.0, 2_400_000)["failure_rate_per_million_cycles"] == 0.0
    assert mcore.reliability_row(facts, 720.0, None)["failure_rate_per_million_cycles"] is None


# A-9 corrigido (Sprint 7, S7-6): idade 0 fora do MTTF simples, contada à parte.
def test_A9_mttf_excludes_zero_age_failure_spec():
    life = [mcore.ComponentLife(str(k), "SKF", "6205", "bearing", {"hours": 20000}, {"hours": a}, False, True,
                                "failure") for k, a in enumerate([8200, 9600, 10400, 0])]
    m = mcore.mttf_row(life)
    assert m["mttf_h"] == 9400.0 and m["failures"] == 3 and m["excluded_no_usage"] == 1


# A-10 corrigido (Sprint 7, S7-5): ritmo pelas horas completas — ao vivo = recálculo = nominal.
def test_A10_condition_rate_live_equals_recompute_spec():
    """Uso uniforme de 20 000 ciclos/dia; avaliação às 10:30 (ao vivo o bucket das 10:00 tem só 30 min de uso)."""
    end = local(10, 30)
    per_h = 20000 / 24
    live, full, t = {}, {}, (end - timedelta(days=15)).replace(minute=0)
    while t < end:
        full[t] = per_h
        live[t] = per_h if t + timedelta(hours=1) <= end else per_h * (end - t).total_seconds() / 3600
        t += timedelta(hours=1)
    cond_live = make_rate_fn(live)(end)
    cond_recomputed = make_rate_fn(full)(end)
    ws, we, d = ucore.rate_window(end)
    nominal = sum(v for b, v in live.items() if ws <= b < we) / d
    assert close(cond_live[0], 20000.0) and close(cond_recomputed[0], 20000.0) and close(nominal, 20000.0)
    assert tuple(cond_live) == tuple(cond_recomputed)
    # 5 dias de dados: janela e divisor de 5 dias, low_confidence (A-2)
    r5 = make_rate_fn(full, data_since=end - timedelta(days=5))(end)
    assert close(r5[0], 20000.0) and close(r5.days, 5.0)
    assert make_rate_fn(full, data_since=end - timedelta(days=2))(end) is None


# A-11 corrigido (Sprint 7): concluída depois do fim do período = não feita.
def test_A11_schedule_compliance_completed_after_period_spec():
    wo = _wo(1, state="completed", scheduled_for=date(2026, 9, 30), scheduled_at=datetime(2026, 9, 25, tzinfo=UTC),
             completed_at=local(9, day=2))
    sc = mcore.schedule_compliance([wo], date(2026, 9, 1), date(2026, 10, 1), TZ)
    assert (sc["late"], sc["not_done"], sc["schedule_compliance"]) == (0, 1, 0.0)
    late_in = _wo(2, state="completed", scheduled_for=date(2026, 9, 28), scheduled_at=datetime(2026, 9, 20, tzinfo=UTC),
                  completed_at=datetime(2026, 9, 29, 15, tzinfo=TZ))
    sc2 = mcore.schedule_compliance([late_in], date(2026, 9, 1), date(2026, 10, 1), TZ)
    assert (sc2["late"], sc2["not_done"]) == (1, 0)


def _a12(order: int):
    t = local(10)
    old = SimpleNamespace(id="old", installed_at=t - timedelta(days=90), removed_at=t)
    new = SimpleNamespace(id="new", installed_at=t, removed_at=None)
    f = SimpleNamespace(component_id=None, position_id="p", failed_at=t)
    return attribute_component(f, {"p": [old, new] if order == 0 else [new, old]})


# A-12 corrigido (Sprint 7, S7-7): intervalo semiaberto [instalado, removido).
def test_A12_failure_at_replacement_instant_spec():
    assert _a12(0) == _a12(1) == "new"


# A-15 corrigido (Sprint 7, S7-3): reason no_running_time.
def test_A15_performance_null_without_reason_spec():
    r = kpi.oee(kpi.Components(pot_s=3600, adot_s=3600, produced=10, good=10, ideal_produced_s=300,
                               ideal_good_s=300))
    assert r["performance"] is None and r["reason"] == "no_running_time" and r["availability"] == 0.0


# A-13 corrigido (Sprint 7, S7-4): PRI da linha vindo da saída é sinalizado.
def test_A13_line_pri_fallback_flagged_spec():
    cal = _shift_a()
    info = MachineInfo("m", "t", "Saída", "l", "p", TZ, "running", 120, 30.0, {})
    md = MachineData(info, cal, [], [(local(6), local(14))], [], {local(8): {"total": 100.0, "good": 100.0,
                                                                            "scrap": 0.0}},
                     [("prodX", local(6), None)], ProductPri(pri={"prodX": 30.0}, fallback_pri=30.0))
    line_pri = ProductPri(pri={"prodX": 30.0}, fallback_pri=30.0, fallback_ids=frozenset({"prodX"}))
    c = components(md, local(6), local(14), local(23), line_pri)
    assert c.pri_fallback and kpi.oee(c)["line_pri_fallback"] is True
    assert kpi.oee(components(md, local(6), local(14), local(23)))["line_pri_fallback"] is False


# A-20 corrigido (Sprint 7): MART = média de repair_finished_at − repair_started_at.
def test_A20_mart_spec():
    facts = [mcore.FailureFacts("a", "critical", local(8), local(9), "p", None, repair_started_at=local(8, 20),
                                repair_finished_at=local(8, 50)),
             mcore.FailureFacts("b", "critical", local(10), local(11), "p", None, repair_started_at=local(10, 30),
                                repair_finished_at=local(10, 40)),
             mcore.FailureFacts("c", "critical", local(12), local(13), "p", None)]
    row = mcore.reliability_row(facts, 10.0, None)
    assert close(row["mart_min"], 20.0) and row["repairs_timed"] == 2 and close(row["mttr_min"], 60.0)
    assert mcore.reliability_row(facts[2:], 10.0, None)["mart_reason"] == "no_repair_times"
    r = kpi.reliability(3600.0, [("critical", 3600.0)] * 2, repair_s=[1800.0, 600.0])
    assert close(r["mart_s"], 1200.0) and r["repairs_timed"] == 2


# ═══ 3. the audit document itself ═══════════════════════════════════════════

def test_audit_lists_every_divergence_tested_here():
    ids = sorted({int(m) for m in re.findall(r"def test_A(\d+)_", Path(__file__).read_text(encoding="utf-8"))})
    for n in ids:
        assert re.search(rf"\bA-{n}\b", AUDIT), f"A-{n} is tested but not described in docs/audit_kpis.md"


def test_audit_file_references_exist():
    """Every `server/src/...py:func` reference of the audit points to an existing function."""
    refs = set(re.findall(r"`(server/src/[\w/]+\.py):([A-Za-z_][\w.]*)`", AUDIT))
    assert len(refs) >= 30
    for path, func in refs:
        src = (ROOT / path).read_text(encoding="utf-8")
        name = func.split(".")[-1]
        assert re.search(rf"^\s*(async\s+)?def {re.escape(name)}\b|^class {re.escape(name)}\b", src, re.M), \
            f"{path}:{func} not found"
