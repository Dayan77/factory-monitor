"""Sprint 7 re-verification of the KPI audit (docs/audit_kpis.md, section "Verificação Sprint 7").

Written by the reliability-engineer role after the backend fixes of A-1 … A-20. The fixes have their own tests
(docs/scenarios/test_audit_kpis.py, server/tests/test_audit_s7.py); this file adds the **independent probes** of the
verification — cases that the fix tests did not cover and that try to break each fix:

* `test_V*` — probes that the fixed code passes (combinations of A-1/A-14, open failures, attribution, nulls).
* `test_A19b_*` — the counter-example still open: the live cadence of the map (S7-13) applies the delta rules 1–3
  of domain.md §3.3 but not rule 4 (plausibility filter), so one corrupted counter reading inflates the cadence.
  `..._code_today` pins today's number; `..._spec` is `xfail(strict=True)`. When it is fixed both fail on purpose:
  mark A-19b as fixed in docs/audit_kpis.md, delete `..._code_today` and drop the `xfail`.

Run:  .venv/bin/python -m pytest docs/scenarios/test_audit_verification_s7.py -q
"""
from __future__ import annotations

import asyncio
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
    from src.services.layout.service import live_parts                      # noqa: E402
    from src.services.maintenance import core as mcore                      # noqa: E402
    from src.services.maintenance.kpis import attribute_component           # noqa: E402
    from src.services.oee import kpi                                        # noqa: E402
    from src.services.oee.calendar import Calendar, ExceptionDef, ShiftDef  # noqa: E402
    from src.services.oee.engine import EventLite, MachineData, MachineInfo, ProductPri, components  # noqa: E402
    from src.services.usage import core as ucore                            # noqa: E402
except Exception as exc:  # pragma: no cover - environment without server deps
    pytest.skip(f"server code not importable: {exc}", allow_module_level=True)

AUDIT = (ROOT / "docs" / "audit_kpis.md").read_text(encoding="utf-8")
UTC = timezone.utc
TZ = ZoneInfo("America/Manaus")
LUNCH = [{"start_local": "12:00", "end_local": "12:30", "reason_code": "PLN-01"}]


def close(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def local(h: int, m: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=TZ).astimezone(UTC)


def _shift_a(breaks=(), exceptions=()) -> Calendar:
    return Calendar(TZ, [ShiftDef("A", "Turno A", "06:00", "14:00", tuple(range(7)), tuple(breaks))], list(exceptions))


def _ao(cal: Calendar, events: list[EventLite], now: datetime | None = None) -> tuple[float, kpi.Components]:
    info = MachineInfo("m", "t", "Prensa P1", "l", "p", TZ, "running", 120, 30.0, {})
    md = MachineData(info, cal, events, [(local(6), local(14))], [], {}, [], ProductPri(fallback_pri=30.0))
    c = components(md, local(6), local(14), now or local(23))
    return kpi.availability_operational(c), c


# ═══ probes that the fixes pass ══════════════════════════════════════════════

def test_V1_calendar_preventive_and_overlapping_failure_counted_once():
    """A-1 × union: preventiva PLN-03 no calendário 08:00–09:00 e falha EQP-03 08:30–09:30 → D_eq = 90 min (não 120).
    T_req = 480 − 30 (refeição) = 450 → Ao = 360/450 = 0,8."""
    ao, c = _ao(_shift_a(LUNCH, [ExceptionDef(date(2026, 10, 5), "planned_stop", "A", "08:00", "09:00", "PLN-03")]),
                [EventLite("e", local(8, 30), local(9, 30), "EQP-03", False)])
    assert close(c.ao_equipment_s, 90 * 60) and close(ao, 0.8)


def test_V2_declared_preventive_and_overlapping_failure_counted_once():
    """Mesmo caso com a preventiva declarada como evento PLN-03 (não no calendário) → mesmo Ao = 0,8."""
    ao, _ = _ao(_shift_a(LUNCH), [EventLite("p", local(8), local(9), "PLN-03", True),
                                 EventLite("e", local(8, 30), local(9, 30), "EQP-03", False)])
    assert close(ao, 0.8)


def test_V3_failure_across_calendar_meal_break():
    """A-14 com pausa do calendário: falha 11:50–12:20 atravessando a refeição PLN-01 12:00–12:30 → só 10 min em
    D_eq (os 20 min dentro da refeição estão fora de T_req) → Ao = 440/450."""
    ao, c = _ao(_shift_a(LUNCH), [EventLite("e", local(11, 50), local(12, 20), "EQP-03", False)])
    assert close(c.ao_equipment_s, 600) and close(ao, 440 / 450)


def test_V4_planned_stop_without_code_is_excluded_not_equipment():
    """Exceção planned_stop sem código cai no padrão PLN-04 (fora de T_req, não é D_eq): Ao = 1,0, não 0,87."""
    ao, c = _ao(_shift_a(LUNCH, [ExceptionDef(date(2026, 10, 5), "planned_stop", "A", "08:00", "09:00", None)]), [])
    assert close(c.ao_excluded_s, 90 * 60) and c.ao_equipment_s == 0 and close(ao, 1.0)


def test_V5_open_failure_counts_until_now():
    """Falha aberta desde 13:00, cálculo às 13:30: T_req = 450 − 30 (refeição) = 420 até agora; D_eq = 30 →
    Ao = 390/420."""
    ao, c = _ao(_shift_a(LUNCH), [EventLite("e", local(13), None, "EQP-03", False)], now=local(13, 30))
    assert close(c.ao_equipment_s, 1800) and close(ao, 390 / 420)


def test_V6_reliability_nulls_are_consistent():
    """A-4/S7-1 sem contradição entre campos: n = 0 com tempo → λ = 0 e MTBF null (no_failures); n = 0 sem tempo →
    λ null; n > 0 sem tempo → MTBF, Ai e λ null juntos (insufficient_data)."""
    r = kpi.reliability(3600.0, [])
    assert r["failure_rate_per_h"] == 0.0 and r["mtbf_h"] is None and r["reason"] == "no_failures"
    assert kpi.reliability(0.0, [])["failure_rate_per_h"] is None
    r = kpi.reliability(0.0, [("critical", 600.0)])
    assert (r["mtbf_h"], r["availability_inherent"], r["failure_rate_per_h"]) == (None, None, None)
    row = mcore.reliability_row([], 10.0, 1000)
    assert row["failure_rate_per_million_cycles"] == 0.0 and row["mcbf"] is None and row["reason"] == "no_failures"


def test_V7_mttf_all_excluded_is_null_with_reason():
    """A-9/S7-6: todos os componentes com idade 0 → MTTF null, reason insufficient_data, excluded_no_usage = 1."""
    life = [mcore.ComponentLife("1", "SKF", "6205", "bearing", {"hours": 20000}, {"hours": 0}, False, True, "failure")]
    m = mcore.mttf_row(life)
    assert m["mttf_h"] is None and m["reason"] == "insufficient_data" and m["excluded_no_usage"] == 1


def test_V8_attribution_at_removal_without_successor_is_unattributed():
    """A-12/S7-7 (intervalo semiaberto): falha registrada no instante exato da remoção de um componente **sem**
    sucessor na posição fica sem componente (None) — não é atribuída ao removido. Comportamento documentado na
    verificação (observação O-2); a falha continua contando para a posição/máquina."""
    t = local(10)
    old = SimpleNamespace(id="old", installed_at=t - timedelta(days=90), removed_at=t)
    f = SimpleNamespace(component_id=None, position_id="p", failed_at=t)
    assert attribute_component(f, {"p": [old]}) is None
    assert attribute_component(SimpleNamespace(component_id=None, position_id="p", failed_at=t - timedelta(seconds=1)),
                               {"p": [old]}) == "old"


def test_V9_rate_window_never_exceeds_14_days_nor_the_data():
    """S7-2/S7-5: sem início de dados conhecido → 14 dias de horas completas; instalado há 60 h → 2,5 dias."""
    now = local(10, 30)
    ws, we, d = ucore.rate_window(now)
    assert d == 14.0 and we == now.replace(minute=0) and ws == we - timedelta(days=14)
    _, _, d = ucore.rate_window(now, now - timedelta(hours=60), None)
    assert close(d, 2.5)


# ═══ A-19b — still open: live cadence without the plausibility filter ═══════

def _glitch_live_parts() -> float:
    """1 peça/min nos últimos 15 min (16 leituras de 1 000 a 1 015) com UMA leitura corrompida (60 000) no minuto 7.
    Máquina com PRI 30 s → max_rate = 2/30 peça/s; Δt = 60 s → limite do filtro (domain.md §3.3 regra 4) =
    2/30 × 60 × 1,5 + 10 = 16 peças: o salto de ~59 000 é glitch e deve ser descartado, mantendo `prev`."""
    now = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    vals = [(now - timedelta(minutes=15 - k), 1000.0 + k) for k in range(16)]
    vals[7] = (vals[7][0], 60000.0)

    async def fetch(_tenant, _tags, _a, _b):
        return [("t1", ts, v) for ts, v in vals]

    return asyncio.run(live_parts("T", ["m"], [("t1", "m", "part_counter_total", 65535)], now, fetch))["m"]


def test_A19b_live_cadence_glitch_code_today():
    # 1000→…→1006→60000 (+58 994) → 1008 (60000 ≥ 0,9 × 65 535: lido como rollover, +6 544) → … → 1015
    assert _glitch_live_parts() == 65551.0          # × 4 = 262 204 pç/h no mapa


@pytest.mark.xfail(strict=True, reason="A-19b: live_parts não aplica o filtro de plausibilidade (domain.md §3.3 regra 4)")
def test_A19b_live_cadence_glitch_spec():
    # glitch descartado, prev mantido em 1006 → 1006→1008 = 2 → total 15 peças → 60 pç/h
    assert _glitch_live_parts() == 15.0


# ═══ the verification section itself ════════════════════════════════════════

def test_verification_section_present_and_lists_open_items():
    sec = AUDIT.split("## Verificação Sprint 7", 1)
    assert len(sec) == 2, "docs/audit_kpis.md must have the 'Verificação Sprint 7' section"
    body = sec[1]
    for n in range(1, 21):
        assert re.search(rf"\bA-{n}\b", body), f"A-{n} not covered by the verification table"
    assert "A-19b" in body and "test_A19b_live_cadence_glitch_spec" in body
