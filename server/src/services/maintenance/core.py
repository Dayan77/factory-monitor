"""
Pure maintenance rules (no I/O) — domain.md §5 + "Esclarecimentos de manutenção (Sprint 4)" S4-1…S4-11
and kpis.md §2–§4. The API layer only loads rows and calls these functions; the golden scenario E
(docs/scenarios/scenario_e_maintenance.json) is checked against them through the API.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

USAGE_MEASURES = ("cycles", "hours", "starts")
TRIGGERS = ("cycles", "hours", "starts", "days")
RATE_WINDOW_DAYS = 14          # kpis.md §4.2 / C17
RATE_MIN_DAYS = 3              # kpis.md §4.2: fewer days of data → no usage projection
EPS = 1e-9                     # C18: an integer quotient does not round up
PROJECTION_HORIZON_DAYS = 3650  # projections beyond ~10 years are not dated (reason beyond_horizon)

# ── plans (domain.md §5.1, kpis.md §4.2, S4-1, S4-3) ─────────────────────────


@dataclass
class TriggerStatus:
    measure: str
    interval: float
    usage: float                 # since maintenance (days: calendar days used)
    consumed: float
    days: Optional[int]          # days to due from today (None: no projection — rate 0)
    projected_date: Optional[date]
    next_due_usage: Optional[float] = None   # absolute counter value (usage triggers only)
    rate_per_day: Optional[float] = None


@dataclass
class PlanStatus:
    state: str
    consumed: float
    consumed_trigger: str
    triggers: list[TriggerStatus]
    days_to_due: Optional[int]
    due_trigger: Optional[str]
    next_due_date: Optional[date]
    calendar_due_date: Optional[date]
    past_due: bool
    reason: Optional[str] = None
    # kpis.md §4.2 (audit A-3): the usage pace came from fewer than 14 days of data
    low_confidence: bool = False

    @property
    def since_maintenance(self) -> dict:
        return {t.measure: t.usage for t in self.triggers}

    @property
    def consumed_by_trigger(self) -> dict:
        return {t.measure: t.consumed for t in self.triggers}

    @property
    def days_by_trigger(self) -> dict:
        return {t.measure: t.days for t in self.triggers}

    @property
    def next_due_usage(self) -> dict:
        return {t.measure: t.next_due_usage for t in self.triggers if t.next_due_usage is not None}


def plan_state(consumed: float, tolerance: float = 0.10, due_soon_at: float = 0.8) -> str:
    """S4-3: ok < due_soon_at ≤ due_soon < 1 ≤ due < 1 + tolerance ≤ overdue."""
    if consumed >= 1.0 + tolerance:
        return "overdue"
    if consumed >= 1.0:
        return "due"
    if consumed >= due_soon_at:
        return "due_soon"
    return "ok"


def local_date(ts: datetime, tz: ZoneInfo) -> date:
    return ts.astimezone(tz).date()


def compute_plan_status(intervals: Mapping[str, Optional[float]], last_reset_at: datetime,
                        last_reset_usage: Mapping[str, float], usage_now: Mapping[str, float],
                        usage_window: Mapping[str, float], today: date, tz: ZoneInfo,
                        tolerance: float = 0.10, due_soon_at: float = 0.8,
                        window_days: float = RATE_WINDOW_DAYS) -> Optional[PlanStatus]:
    """Whichever-comes-first status of a plan. `intervals`: {cycles|hours|starts|days: value}; usage
    since maintenance = usage_now − last_reset_usage (S4-2, never the component counter).
    `window_days`: days of data behind `usage_window` (kpis.md §4.2, audit A-3): min(14, days with data); below
    RATE_MIN_DAYS the usage triggers get no projected date (`insufficient_data`); below 14 → `low_confidence`."""
    window_days = min(float(window_days), float(RATE_WINDOW_DAYS))
    low_conf = window_days < RATE_WINDOW_DAYS
    too_little = window_days < RATE_MIN_DAYS
    ref = local_date(last_reset_at, tz)
    trig: list[TriggerStatus] = []
    calendar_due: Optional[date] = None
    beyond_horizon = False
    for m in TRIGGERS:
        iv = intervals.get(m)
        if iv is None or iv <= 0:
            continue
        if m == "days":
            iv_days = int(iv)
            used = (today - ref).days                                  # S4-1: local calendar days
            remaining = iv_days - used
            if remaining > PROJECTION_HORIZON_DAYS:                    # no date overflow on absurd intervals
                cal_days: Optional[int] = None
                beyond_horizon = True
            else:
                calendar_due = ref + timedelta(days=iv_days)
                cal_days = max((calendar_due - today).days, 0)
            trig.append(TriggerStatus(m, float(iv_days), float(used), used / iv_days, cal_days,
                                      today + timedelta(days=cal_days) if cal_days is not None else None))
            continue
        base = float(last_reset_usage.get(m, 0.0) or 0.0)
        used = float(usage_now.get(m, 0.0) or 0.0) - base
        rate = 0.0 if too_little else float(usage_window.get(m, 0.0) or 0.0) / window_days
        rem = iv - used
        if rem <= 0:
            days: Optional[int] = 0
        elif rate > 0 and rem / rate <= PROJECTION_HORIZON_DAYS:
            days = math.ceil(rem / rate - EPS)                          # C18
        elif rate > 0:
            days = None                                                 # pace too slow: beyond the horizon
            beyond_horizon = True
        else:
            days = None                                                 # no_recent_usage
        trig.append(TriggerStatus(m, float(iv), used, used / iv, days,
                                  today + timedelta(days=days) if days is not None else None,
                                  next_due_usage=base + iv, rate_per_day=rate))
    if not trig:
        return None
    best = max(trig, key=lambda t: t.consumed)        # first max in TRIGGERS order
    consumed = best.consumed
    state = plan_state(consumed, tolerance, due_soon_at)
    dated = [t for t in trig if t.days is not None]
    if consumed >= 1.0:
        # S4-3: overdue → days_to_due 0, today, past_due (whatever the projections say)
        zero = [t for t in dated if t.days == 0]
        due_t = max(zero, key=lambda t: t.consumed).measure if zero else best.measure
        return PlanStatus(state, consumed, best.measure, trig, 0, due_t, today, calendar_due, True,
                          low_confidence=low_conf)
    if not dated:
        return PlanStatus(state, consumed, best.measure, trig, None, None, None, calendar_due, False,
                          reason="beyond_horizon" if beyond_horizon
                          else "insufficient_data" if too_little else "no_recent_usage", low_confidence=low_conf)
    first = min(dated, key=lambda t: (t.days, -t.consumed))           # earliest date; tie → larger %
    return PlanStatus(state, consumed, best.measure, trig, first.days, first.measure,
                      today + timedelta(days=first.days), calendar_due, False, low_confidence=low_conf)


# ── work-order state machine (domain.md §5.3, S4-5) ──────────────────────────

WO_TRANSITIONS = {
    ("draft", "open"), ("open", "approved"), ("approved", "scheduled"), ("scheduled", "in_progress"),
    ("in_progress", "on_hold"), ("on_hold", "in_progress"), ("on_hold", "scheduled"),
    ("in_progress", "completed"), ("completed", "closed"),
}
BEFORE_COMPLETED = ("draft", "open", "approved", "scheduled", "in_progress", "on_hold")
OPEN_STATES = BEFORE_COMPLETED
BACKLOG_STATES = ("approved", "scheduled", "on_hold", "in_progress")
CANCEL_REASONS = ("superseded", "production_conflict", "duplicate", "not_needed", "other")
STATE_ORDER = ("draft", "open", "approved", "scheduled", "in_progress", "on_hold", "completed", "closed", "cancelled")


def is_emergency(wo_type: str, urgency: Optional[str]) -> bool:
    return wo_type == "corrective" and urgency == "emergency"


def allowed_targets(state: str, wo_type: str, urgency: Optional[str]) -> list[str]:
    """Targets reachable from `state` (cancel needs a cancel_reason)."""
    out = [b for (a, b) in WO_TRANSITIONS if a == state]
    if state == "open" and is_emergency(wo_type, urgency):
        out.append("in_progress")
    if state in BEFORE_COMPLETED:
        out.append("cancelled")
    return sorted(set(out), key=STATE_ORDER.index)


def transition_error(state: str, to: str, wo_type: str, urgency: Optional[str],
                     cancel_reason: Optional[str]) -> Optional[str]:
    """None if valid; else a message (→ 422)."""
    if to not in allowed_targets(state, wo_type, urgency):
        if state == "open" and to == "in_progress":
            return "O atalho open → in_progress é só para corretiva emergencial."
        if state in ("completed", "closed") and to != "closed":
            return "OS concluída/fechada não reabre nem é cancelada: abra uma nova OS ligada a esta."
        return f"Transição inválida: {state} → {to}."
    if to == "cancelled":
        if not cancel_reason:
            return "Cancelar exige cancel_reason."
        if cancel_reason not in CANCEL_REASONS:
            return f"cancel_reason inválido (use {', '.join(CANCEL_REASONS)})."
    return None


def remaining_hours(estimated: float, labor: float, informed: Optional[float]) -> float:
    """S4-5: informed by the technician; otherwise max(estimated − labor, 0)."""
    if informed is not None:
        return float(informed)
    return max(float(estimated or 0.0) - float(labor or 0.0), 0.0)


def is_planned_work(wo_type: str, urgency: Optional[str]) -> bool:
    """kpis.md §3.1: planned = preventive + predictive + improvement + deferred corrective."""
    return wo_type in ("preventive", "predictive", "improvement") or (wo_type == "corrective" and urgency == "deferred")


# ── management KPIs (kpis.md §3, S4-6, S4-7) ─────────────────────────────────

@dataclass
class WoFacts:
    id: str
    label: str
    type: str
    urgency: Optional[str]
    state: str
    labor_hours: float
    estimated_hours: float
    remaining_hours: Optional[float]
    scheduled_for: Optional[date]
    scheduled_at: Optional[datetime]
    completed_at: Optional[datetime]
    cancel_reason: Optional[str]
    plan_id: Optional[str] = None


def pmp(wos: Iterable[WoFacts], start: datetime, stop: datetime) -> dict:
    done = [w for w in wos if w.completed_at is not None and start <= w.completed_at < stop]
    planned = [w for w in done if is_planned_work(w.type, w.urgency)]
    ph = sum(w.labor_hours for w in planned)
    th = sum(w.labor_hours for w in done)
    return {"pmp": (ph / th) if th > 0 else None, "reason": None if th > 0 else "insufficient_data",
            "planned_hours": ph, "unplanned_hours": th - ph, "total_hours": th,
            "work_orders": [w.id for w in sorted(done, key=lambda w: w.label)]}


def schedule_compliance(wos: Iterable[WoFacts], first_day: date, end_day: date, tz: ZoneInfo,
                        tolerance_days: int = 0) -> dict:
    """S4-6: period by local scheduled_for in [first_day, end_day); freeze = Monday 00:00 local of the
    week of scheduled_for; superseded cancellations leave the denominator; anything else not completed
    by the end of the period is not done."""
    den, on_time, late, not_done, excluded = [], [], [], [], []
    for w in sorted(wos, key=lambda w: w.label):
        d = w.scheduled_for
        if d is None or not (first_day <= d < end_day) or is_emergency(w.type, w.urgency):
            continue
        monday = d - timedelta(days=d.weekday())
        freeze = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
        if w.scheduled_at is not None and w.scheduled_at > freeze:
            excluded.append({"wo_id": w.id, "reason": "scheduled_after_freeze"})
            continue
        if w.state == "cancelled" and w.cancel_reason == "superseded":
            excluded.append({"wo_id": w.id, "reason": "cancelled_superseded"})
            continue
        den.append(w.id)
        done_day = local_date(w.completed_at, tz) if w.completed_at is not None else None
        if done_day is not None and done_day <= d + timedelta(days=tolerance_days):
            on_time.append(w.id)
        elif done_day is not None and done_day < end_day:
            late.append(w.id)
        else:
            # S4-6 (audit A-11): not completed by the end of the period = not done, even if completed afterwards —
            # the counts of a closed period do not depend on the day the report is generated
            not_done.append(w.id)
    n = len(den)
    return {"schedule_compliance": (len(on_time) / n) if n else None,
            "reason": None if n else "insufficient_data",
            "scheduled": n, "on_time": len(on_time), "late": len(late), "not_done": len(not_done),
            "denominator_ids": den, "on_time_ids": on_time, "late_ids": late, "not_done_ids": not_done,
            "compliance_excluded": excluded}


def backlog(wos: Iterable[WoFacts], today: date, capacity_hours_per_week: Optional[float],
            overdue_plan_ids: Iterable[str] = ()) -> dict:
    """S4-7: approved/scheduled/on_hold/in_progress, remaining hours; drafts/open apart."""
    ws = sorted(wos, key=lambda w: w.label)
    overdue_plans = set(overdue_plan_ids)
    bl = [w for w in ws if w.state in BACKLOG_STATES]
    rem = {w.id: remaining_hours(w.estimated_hours, w.labor_hours, w.remaining_hours) for w in bl}
    hours = sum(rem.values())
    overdue = [w.id for w in bl if (w.scheduled_for is not None and w.scheduled_for < today)
               or (w.plan_id is not None and w.plan_id in overdue_plans)]
    weeks = (hours / capacity_hours_per_week) if capacity_hours_per_week and capacity_hours_per_week > 0 else None
    return {"backlog_hours": hours, "backlog_count": len(bl), "backlog_weeks": weeks,
            "backlog_weeks_reason": None if weeks is not None else "no_capacity",
            "crew_capacity_hours_per_week": capacity_hours_per_week,
            "remaining_by_wo": rem, "overdue_count": len(overdue), "overdue_hours": sum(rem[i] for i in overdue),
            "overdue_ids": overdue,
            "drafts_not_in_backlog": [w.id for w in ws if w.state == "draft"],
            "requests_pending": [w.id for w in ws if w.state == "open"]}


# ── reliability (kpis.md §2, S4-8 … S4-11) ───────────────────────────────────

COUNTED_SEVERITIES = ("critical", "degraded")


@dataclass
class FailureFacts:
    id: str
    severity: str
    failed_at: datetime
    restored_at: Optional[datetime]
    position_id: Optional[str]
    component_id: Optional[str]
    machine_id: Optional[str] = None
    repair_started_at: Optional[datetime] = None
    repair_finished_at: Optional[datetime] = None
    outside_production: bool = False          # kpis S5-1c item 6: failed_at outside planned production time


def reliability_row(failures: Sequence[FailureFacts], hours: Optional[float], cycles: Optional[float]) -> dict:
    """MTBF = hours / n, MCBF = cycles / n, MTTR = mean restore time of failures with restored_at
    (S4-10). n counts critical/degraded only; incipient returned apart.
    Sprint 7: failures with no operating time → MTBF null, reason insufficient_data (audit A-4); λc per 10⁶ cycles
    (§2.5, A-8); failures_outside_production (S5-1c item 6, A-6); MART (§2.3, A-20)."""
    counted = [f for f in failures if f.severity in COUNTED_SEVERITIES]
    n = len(counted)
    inc = sum(1 for f in failures if f.severity == "incipient")
    rest = [(f.restored_at - f.failed_at).total_seconds() for f in counted if f.restored_at is not None]
    mttr_s = (sum(rest) / len(rest)) if rest else None
    mtbf = (hours / n) if n and hours else None
    mcbf = (cycles / n) if n and cycles else None
    reps = [(f.repair_finished_at - f.repair_started_at).total_seconds() for f in counted
            if f.repair_started_at is not None and f.repair_finished_at is not None
            and f.repair_finished_at >= f.repair_started_at]
    mart_s = (sum(reps) / len(reps)) if reps else None
    reason = "no_failures" if not n else (None if hours else "insufficient_data")
    return {"n": n, "failures": n, "incipient": inc, "operating_hours": hours, "cycles": cycles,
            "mtbf_h": mtbf, "mcbf": mcbf, "mcbf_cycles": mcbf,
            "mttr_s": mttr_s, "mttr_min": mttr_s / 60 if mttr_s is not None else None,
            "mttr_h": mttr_s / 3600 if mttr_s is not None else None,
            "mttr_reason": None if rest else ("no_failures" if not n else "no_restored_failures"),
            "restored": len(rest),
            "failure_rate_per_h": (n / hours) if hours else None,
            "failure_rate_per_million_cycles": (n / cycles * 1e6) if cycles else None,
            "failures_outside_production": sum(1 for f in counted if f.outside_production),
            "mart_s": mart_s, "mart_min": mart_s / 60 if mart_s is not None else None,
            "mart_reason": None if reps else "no_repair_times", "repairs_timed": len(reps),
            "reason": reason, "null_reason": reason}


@dataclass
class ComponentLife:
    id: str
    manufacturer: Optional[str]
    model: Optional[str]
    component_type: str
    nominal_life: dict
    usage_since_install: dict
    installed: bool          # currently in a position
    removed: bool
    removal_reason: Optional[str]


def life_measure_of(components: Sequence[ComponentLife]) -> Optional[str]:
    """S4-11: measure of the type's nominal life (cycles for cylinders, hours for motors/gearboxes)."""
    for m in ("cycles", "hours", "starts"):
        if any((c.nominal_life or {}).get(m) for c in components):
            return m
    return None


def mttf_row(components: Sequence[ComponentLife]) -> dict:
    """S4-11: mean usage_since_install of components removed with removal_reason = failure (whole
    history); other removals = suspensions; installed = in_service. No failures → null."""
    m = life_measure_of(components) or "hours"
    failed = [c for c in components if c.removed and c.removal_reason == "failure"]
    susp = [c for c in components if c.removed and c.removal_reason != "failure"]
    in_service = [c for c in components if c.installed and not c.removed]
    all_ages = [float(c.usage_since_install.get(m, 0.0) or 0.0) for c in failed]
    # audit A-9 (S7-6): a failure with no recorded usage (age ≤ 0 — imported, or removed before the gateway) is
    # excluded like in the Weibull (reports.md §2.1) and counted apart
    ages = [a for a in all_ages if a > 0]
    excluded = len(all_ages) - len(ages)
    v = (sum(ages) / len(ages)) if ages else None
    return {"measure": m, "failures": len(ages), "suspensions": len(susp), "in_service": len(in_service),
            "mttf": v, "mttf_h": v if m == "hours" else None, "mctf": v if m == "cycles" else None,
            "small_sample": 0 < len(ages) < 3, "excluded_no_usage": excluded,
            "reason": None if ages else ("insufficient_data" if excluded else "no_failures")}


# ── failure codes (domain.md §5.5, ISO 14224) ────────────────────────────────

FAILURE_MODES = [
    ("BRD", "Quebra"), ("FTS", "Falha ao partir"), ("STP", "Falha ao parar"), ("UST", "Parada espúria"),
    ("ELP", "Vazamento externo"), ("INL", "Vazamento interno"), ("OHE", "Superaquecimento"), ("VIB", "Vibração"),
    ("NOI", "Ruído"), ("PDE", "Desvio de parâmetro"), ("ERO", "Saída errática"), ("LOO", "Perda de saída"),
    ("STD", "Deficiência estrutural"), ("AIR", "Leitura anormal de instrumento"), ("SER", "Problemas menores"),
    ("OTH", "Outro"), ("UNK", "Desconhecido"), ("FTO", "Falha ao abrir (válvulas)"), ("FTC", "Falha ao fechar (válvulas)"),
]
FAILURE_MECHANISMS = [
    ("1", "Mecânico"), ("1.1", "Vazamento"), ("1.2", "Vibração"), ("1.3", "Folga/desalinhamento"),
    ("1.4", "Deformação"), ("1.5", "Afrouxamento"), ("1.6", "Travamento"),
    ("2", "Material"), ("2.1", "Cavitação"), ("2.2", "Corrosão"), ("2.3", "Erosão"), ("2.4", "Desgaste"),
    ("2.5", "Ruptura"), ("2.6", "Fadiga"), ("2.7", "Superaquecimento"), ("2.8", "Explosão/estouro"),
    ("3", "Instrumento"), ("3.1", "Falha de controle"), ("3.2", "Sem sinal"), ("3.3", "Sinal falho"),
    ("3.4", "Fora de ajuste"), ("3.5", "Erro de software"), ("3.6", "Falha de causa comum"),
    ("4", "Elétrico"), ("4.1", "Curto-circuito"), ("4.2", "Circuito aberto"), ("4.3", "Sem energia"),
    ("4.4", "Energia com defeito"), ("4.5", "Falha de aterramento/isolação"),
    ("5", "Influência externa"), ("5.1", "Bloqueio/entupimento"), ("5.2", "Contaminação"), ("5.3", "Outra externa"),
    ("6", "Diversos"), ("6.1", "Sem causa encontrada"), ("6.2", "Causas combinadas"), ("6.3", "Outro"),
    ("6.4", "Desconhecido"),
]
FAILURE_CAUSES = [
    ("1", "Projeto"), ("1.1", "Capacidade inadequada"), ("1.2", "Material inadequado"),
    ("2", "Fabricação/instalação"), ("2.1", "Erro de fabricação"), ("2.2", "Erro de instalação"),
    ("3", "Operação/manutenção"), ("3.1", "Serviço fora do projeto"), ("3.2", "Erro de operação"),
    ("3.3", "Erro de manutenção"), ("3.4", "Desgaste e envelhecimento esperados"),
    ("4", "Gestão"), ("4.1", "Erro de documentação"), ("4.2", "Erro de gestão"),
    ("5", "Diversos"), ("5.1", "Sem causa encontrada"), ("5.2", "Causa comum"), ("5.3", "Causa combinada"),
    ("5.4", "Outro"), ("5.5", "Desconhecida"),
]
DETECTION_METHODS = [
    ("periodic_maintenance", "Manutenção periódica"), ("functional_test", "Teste funcional"),
    ("inspection", "Inspeção"), ("periodic_condition_monitoring", "Monitoramento de condição periódico"),
    ("continuous_condition_monitoring", "Monitoramento contínuo (alarme do FactoryConnect)"),
    ("production_interference", "Interferência na produção"), ("casual_observation", "Observação casual"),
    ("corrective_maintenance", "Manutenção corretiva"), ("on_demand", "Sob demanda"), ("other", "Outro"),
]
SEVERITIES = [("critical", "Crítica — perda total imediata da função"),
              ("degraded", "Degradada — função parcial"),
              ("incipient", "Incipiente — início de degradação, ainda funciona")]
# modo de falha sugerido por tipo de componente (lista curta do app do técnico)
MODES_BY_COMPONENT_TYPE = {
    "pneumatic_cylinder": ["ELP", "INL", "PDE", "BRD", "STD", "OTH"],
    "valve": ["FTO", "FTC", "ELP", "INL", "PDE", "OTH"],
    "electric_motor": ["OHE", "VIB", "NOI", "FTS", "UST", "BRD", "OTH"],
    "servo": ["OHE", "VIB", "ERO", "PDE", "FTS", "OTH"],
    "robot_axis": ["BRD", "VIB", "NOI", "PDE", "ERO", "OTH"],
    "gripper": ["BRD", "PDE", "ELP", "ERO", "OTH"],
    "bearing": ["VIB", "NOI", "OHE", "BRD", "OTH"],
    "belt": ["BRD", "PDE", "NOI", "OTH"],
    "pump": ["ELP", "INL", "LOO", "VIB", "OHE", "OTH"],
    "spindle": ["VIB", "OHE", "NOI", "BRD", "OTH"],
    "other": ["BRD", "ELP", "PDE", "OTH", "UNK"],
}
FAILURE_MODE_CODES = {c for c, _ in FAILURE_MODES}
FAILURE_MECHANISM_CODES = {c for c, _ in FAILURE_MECHANISMS}
FAILURE_CAUSE_CODES = {c for c, _ in FAILURE_CAUSES}
DETECTION_METHOD_CODES = {c for c, _ in DETECTION_METHODS}
