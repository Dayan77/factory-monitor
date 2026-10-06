"""
Predictive maintenance API (Sprint 5 — docs/predictive.md P-12). Mounted at /api/predictive.

  GET  /monitors · /monitors/{id} · POST /monitors · PATCH /monitors/{id}   state, baseline, last x/z/EWMA/CUSUM, RUL
  POST /monitors/{id}/rebaseline                                           manual reset (reason required, no warm-up)
  POST /monitors/{id}/process                                              run the pipeline of the monitor now
  GET  /rules · POST /rules · PATCH /rules/{id} · DELETE /rules/{id}       condition rules (P-3)
  GET  /features?position_id&feature&start&stop                             series for the chart (phase, limits, EWMA…)
  GET  /alerts?state&severity&line_id&position_id · GET /alerts/{id}       list / detail with evidence and text
  POST /alerts/{id}/transition · POST /alerts/{id}/convert                 lifecycle (P-8), predictive WO (P-9)
  GET  /evaluation?start&stop&line_id                                       P-10 metrics
  POST /recompute                                                          admin: recompute since the last baseline
  GET  /badges?machine_id                                                  condition badge (any role, the operator too)
  PUT/GET /production-plan                                                 daily production plan (P-7)
  POST /condition-plans                                                    condition-based maintenance plan (P-9)

Roles: read = maintenance_planner, manager, technician, production_supervisor (+ admin); configure / rebaseline =
planner, manager, admin; recompute = admin. Every query filters tenant_id; ids of another tenant answer 404.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_any
from ...db import get_db
from ...models import (Component, ConditionBaseline, ConditionFeature, Line, Machine, MaintenancePlan,
                       Position, PredictiveAlert, PredictiveMonitor, PredictiveRule, ProductionPlanDay, Tag, User)
from ...services import clock
from ...services.predictive import engine as pe
from ...services.predictive import service as ps
from ...services.predictive.defaults import DEFAULT_UNIT, DEFAULTS, FEATURE_LABEL, SEV_RANK
from ...services.predictive.evaluation import evaluate_tenant
from ...services.validation import is_uuid
from ..tenant_refs import ensure_owned

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]

READ = ps.READ_ROLES
CONFIG = ps.CONFIG_ROLES

router = APIRouter()

Feature = Literal["stroke_time", "current", "vibration", "temperature"]
Severity = Literal["watch", "warning", "critical"]
RuleType = Literal["threshold", "pct_of_baseline", "rate_of_change"]


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} não encontrado(a).")


async def _get(db: AsyncSession, model, obj_id: str, tenant_id: str, what: str, lock: bool = False):
    if not is_uuid(obj_id):
        raise _404(what)
    q = select(model).where(model.id == obj_id, model.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    obj = (await db.execute(q)).scalar_one_or_none()
    if obj is None:
        raise _404(what)
    return obj


async def _commit(db: AsyncSession) -> None:
    await db.commit()
    await ps.deliver(db)


# ══ schemas ══════════════════════════════════════════════════════════════════

class RuleIn(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=64)
    type: RuleType
    value: float
    sustained_for: Optional[int] = Field(None, ge=1, le=1000)
    sustained_duration: Optional[str] = Field(None, description="duração, ex. '16h' → ceil(duração / janela)")
    severity: Severity = "warning"
    enabled: bool = True
    description: Optional[str] = Field(None, max_length=255)


class RuleCreate(RuleIn):
    monitor_id: str


class RulePatch(BaseModel):
    value: Optional[float] = None
    sustained_for: Optional[int] = Field(None, ge=1, le=1000)
    severity: Optional[Severity] = None
    enabled: Optional[bool] = None
    description: Optional[str] = Field(None, max_length=255)


class MonitorCreate(BaseModel):
    position_id: str
    feature: Feature
    tag_id: Optional[str] = None
    ambient_tag_id: Optional[str] = None
    direction: Literal["up", "down"] = "up"
    window_cycles: Optional[int] = Field(None, ge=10, le=10_000_000)
    window_hours: Optional[float] = Field(None, gt=0, le=24 * 31)
    warmup_windows: Optional[int] = Field(None, ge=0, le=1000)
    failure_limit: Optional[float] = None
    failure_limit_relative: Optional[bool] = None
    sensor_resolution: float = Field(0.0, ge=0)
    unit: Optional[str] = Field(None, max_length=32)
    params: dict = Field(default_factory=dict)
    enabled: bool = True
    start_at: Optional[datetime] = Field(None, description="início do monitoramento (padrão: agora)")
    default_rules: bool = True
    rules: Optional[list[RuleIn]] = None

    @model_validator(mode="after")
    def _check(self):
        if self.window_cycles and self.window_hours:
            raise ValueError("Informe window_cycles OU window_hours.")
        if self.feature == "stroke_time" and self.window_hours:
            raise ValueError("stroke_time usa janela por ciclos (window_cycles).")
        if self.feature != "stroke_time" and self.window_cycles:
            raise ValueError("Sinais analógicos usam janela por tempo (window_hours).")
        bad = set(self.params) - set(DEFAULTS) - {"data_gap_s"}
        if bad:
            raise ValueError(f"Parâmetros desconhecidos: {sorted(bad)}")
        return self


class MonitorPatch(BaseModel):
    tag_id: Optional[str] = None
    ambient_tag_id: Optional[str] = None
    direction: Optional[Literal["up", "down"]] = None
    window_cycles: Optional[int] = Field(None, ge=10, le=10_000_000)
    window_hours: Optional[float] = Field(None, gt=0, le=24 * 31)
    warmup_windows: Optional[int] = Field(None, ge=0, le=1000)
    failure_limit: Optional[float] = None
    failure_limit_relative: Optional[bool] = None
    sensor_resolution: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = Field(None, max_length=32)
    params: Optional[dict] = None
    enabled: Optional[bool] = None
    reason: Optional[str] = Field(None, max_length=500)


class RebaselineIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class TransitionIn(BaseModel):
    to: Literal["acknowledged", "closed", "converted"]
    resolution: Optional[str] = None
    comment: Optional[str] = Field(None, max_length=2000)


class ConvertIn(BaseModel):
    estimated_hours: Optional[float] = Field(None, ge=0, le=1000)
    note: Optional[str] = Field(None, max_length=2000)


class RecomputeIn(BaseModel):
    monitor_id: Optional[str] = None


class PlanDayIn(BaseModel):
    day: date
    planned_parts: Optional[float] = Field(None, ge=0)
    planned_hours: Optional[float] = Field(None, ge=0, le=24)


class ProductionPlanIn(BaseModel):
    machine_id: str
    days: list[PlanDayIn] = Field(max_length=400)


class ConditionPlanIn(BaseModel):
    position_id: str
    feature: Feature
    min_severity: Literal["warning", "critical"] = "warning"
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    estimated_hours: float = Field(0.0, ge=0)
    task_list: list[str] = Field(default_factory=list)
    stops_machine: bool = False


# ══ monitors ═════════════════════════════════════════════════════════════════

async def _rul_nominal(db: AsyncSession, m: PredictiveMonitor, now: datetime) -> Optional[dict]:
    from ...services.usage import queries as uq
    comp = (await db.execute(select(Component).where(Component.tenant_id == m.tenant_id,
                                                     Component.position_id == m.position_id,
                                                     Component.removed_at.is_(None)))).scalar_one_or_none()
    if comp is None:
        return None
    tz = await ps.plant_tz(db, m.tenant_id, m.machine_id)
    try:
        d = await uq.component_usage_detail(db, m.tenant_id, comp, tz, now)
    except Exception:  # noqa: BLE001 — nominal life is optional context
        return None
    return {"component_id": comp.id, "rul_days": d["rul_days"], "measure": d["rul_measure"], "reason": d["rul_reason"],
            "projected_date": d["projected_end_date"].isoformat() if d["projected_end_date"] else None,
            "life_consumed_pct": d["life_consumed_pct"]}


def _effective(nominal: Optional[dict], cond: Optional[dict]) -> tuple[Optional[dict], Optional[str]]:
    """P-6: the one with the earliest date; without condition RUL the nominal one stands."""
    cd = (cond or {}).get("projected_date")
    nd = (nominal or {}).get("projected_date")
    if cd and (not nd or cd <= nd):
        return {"rul_days": cond.get("rul_days"), "projected_date": cd}, "condition"
    if nd:
        return {"rul_days": nominal.get("rul_days"), "projected_date": nd}, "nominal"
    return None, None


async def monitor_out(db: AsyncSession, m: PredictiveMonitor, names: dict, now: datetime,
                      with_rules: bool = True) -> dict:
    st = m.status or {}
    reset = await ps.latest_reset(db, m)
    phase = st.get("phase") or "learning"
    if not m.enabled:
        state = "disabled"
    elif reset is not None and reset.established_at is not None:
        state = "monitoring"
    else:
        state = "learning"
    learning = st.get("learning") or {"windows": 0, "required": (m.params or {}).get(
        "baseline_min_windows", DEFAULTS["baseline_min_windows"])}
    last = st.get("last") or {}
    cond = last.get("rul") if state == "monitoring" else None
    nominal = await _rul_nominal(db, m, now)
    eff, src = _effective(nominal, cond)
    open_alert = (await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id,
        PredictiveAlert.state != "closed"))).scalar_one_or_none()
    lf = m.failure_limit
    if lf is not None and m.failure_limit_relative:
        lf = lf * reset.m0 if reset is not None and reset.m0 is not None else None
    out = {
        "id": m.id, "position_id": m.position_id, "position_name": names.get(m.position_id),
        "machine_id": m.machine_id, "machine_name": names.get(m.machine_id),
        "line_id": names.get(("line", m.machine_id)), "line_name": names.get(("line_name", m.machine_id)),
        "feature": m.feature, "feature_label": FEATURE_LABEL.get(m.feature), "unit": m.unit or DEFAULT_UNIT[m.feature],
        "tag_id": m.tag_id, "ambient_tag_id": m.ambient_tag_id, "direction": m.direction,
        "window": {"cycles": m.window_cycles} if m.window_cycles else {"hours": m.window_hours},
        "anchor_at": clock.iso(m.anchor_at), "warmup_windows": m.warmup_windows,
        "failure_limit": m.failure_limit, "failure_limit_relative": m.failure_limit_relative,
        "failure_limit_value": lf, "sensor_resolution": m.sensor_resolution, "params": m.params or {},
        "enabled": m.enabled, "state": state, "phase": phase,
        "learning": {**learning, "label": f"aprendendo ({learning.get('windows', 0)}/{learning.get('required')} janelas)"}
        if state == "learning" else None,
        "baseline": ps.baseline_out(reset) if reset is not None else None,
        "last": {k: last.get(k) for k in ("i", "end", "x", "z", "ewma", "cusum", "rolling_z", "slope", "triggers",
                                          "phase")} if last else None,
        "z": last.get("z") if state == "monitoring" else None,
        "ewma": st.get("ewma") if state == "monitoring" else None,
        "cusum": st.get("cusum") if state == "monitoring" else None,
        "ewma_limit": st.get("ewma_limit"), "cusum_h": st.get("cusum_h"),
        "rul_condition": cond if cond else ({"rul_days": None, "reason": "baseline_learning"} if state == "learning"
                                            else None),
        "rul_nominal": nominal, "rul_effective": eff, "rul_source": src,
        "open_alert": {"id": open_alert.id, "severity": open_alert.severity, "state": open_alert.state}
        if open_alert else None,
        "processed_until": clock.iso(m.processed_until),
    }
    if with_rules:
        out["rules"] = [ps.rule_out(r) for r in await ps.rules_of(db, m)]
    return out


async def _scope_monitors(db: AsyncSession, tenant_id: str, plant_id=None, line_id=None, machine_id=None,
                          position_id=None, feature=None) -> list[PredictiveMonitor]:
    q = (select(PredictiveMonitor).join(Machine, Machine.id == PredictiveMonitor.machine_id)
         .join(Line, Line.id == Machine.line_id).where(PredictiveMonitor.tenant_id == tenant_id))
    for val, col in ((plant_id, Line.plant_id), (line_id, Machine.line_id), (machine_id, PredictiveMonitor.machine_id),
                     (position_id, PredictiveMonitor.position_id)):
        if val:
            if not is_uuid(val):
                return []
            q = q.where(col == val)
    if feature:
        q = q.where(PredictiveMonitor.feature == feature)
    return list((await db.execute(q.order_by(Machine.name, PredictiveMonitor.feature))).scalars().all())


@router.get("/monitors", dependencies=[Depends(require_any(*READ))])
async def list_monitors(user: CurrentUser, db: DB, plant_id: Optional[str] = None, line_id: Optional[str] = None,
                        machine_id: Optional[str] = None, position_id: Optional[str] = None,
                        feature: Optional[Feature] = None):
    ms_ = await _scope_monitors(db, user.tenant_id, plant_id, line_id, machine_id, position_id, feature)
    names = await ps._names(db, user.tenant_id, [m.position_id for m in ms_], [m.machine_id for m in ms_])
    now = clock.now()
    return [await monitor_out(db, m, names, now) for m in ms_]


@router.get("/monitors/{monitor_id}", dependencies=[Depends(require_any(*READ))])
async def get_monitor(monitor_id: str, user: CurrentUser, db: DB):
    m = await _get(db, PredictiveMonitor, monitor_id, user.tenant_id, "Monitor")
    names = await ps._names(db, user.tenant_id, [m.position_id], [m.machine_id])
    out = await monitor_out(db, m, names, clock.now())
    bases = (await db.execute(select(ConditionBaseline).where(
        ConditionBaseline.tenant_id == user.tenant_id, ConditionBaseline.monitor_id == m.id)
        .order_by(ConditionBaseline.reset_at))).scalars().all()
    out["baselines"] = [ps.baseline_out(b) for b in bases]
    return out


def _sustained(r: RuleIn, m: PredictiveMonitor) -> int:
    if r.sustained_for is not None:
        return r.sustained_for
    if r.sustained_duration:
        import math
        import re
        mt = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hd])\s*", r.sustained_duration)
        if not mt or not m.window_hours:
            raise HTTPException(status_code=422, detail="sustained_duration exige janela por tempo e formato '16h'/'2d'.")
        hours = float(mt.group(1)) * (24 if mt.group(2) == "d" else 1)
        return max(1, math.ceil(hours / m.window_hours - 1e-9))
    return DEFAULTS["sustained_for"]


async def _add_rule(db: AsyncSession, m: PredictiveMonitor, r: RuleIn) -> PredictiveRule:
    n = (await db.execute(select(func.count()).select_from(PredictiveRule).where(
        PredictiveRule.monitor_id == m.id))).scalar_one()
    code = r.code or f"{m.feature[:3].upper()}-{r.type[:3].upper()}-{n + 1}"
    rule = PredictiveRule(tenant_id=m.tenant_id, monitor_id=m.id, code=code, type=r.type, value=r.value,
                          sustained_for=_sustained(r, m), severity=r.severity, enabled=r.enabled,
                          description=r.description)
    try:
        async with db.begin_nested():
            db.add(rule)
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Já existe regra com o código {code} neste monitor.")
    return rule


def _suggested_rules(feature: str, failure_limit: Optional[float], relative: bool) -> list[RuleIn]:
    """P-3 suggested defaults when the predictive is switched on (the user edits them)."""
    if feature == "stroke_time":
        return [RuleIn(code="PCT-110", type="pct_of_baseline", value=1.10, severity="warning")]
    if feature == "current":
        out = [RuleIn(code="PCT-115", type="pct_of_baseline", value=1.15, severity="warning")]
        if failure_limit is not None and not relative:
            out.insert(0, RuleIn(code="THR-IN", type="threshold", value=failure_limit, severity="critical"))
        return out
    if feature == "temperature" and failure_limit is not None and not relative:
        return [RuleIn(code="THR-MAX", type="threshold", value=failure_limit, severity="critical")]
    return []        # vibration: ISO 20816 zones still to be confirmed (predictive.md, open points) — user informs


async def _check_tag(db: AsyncSession, tenant_id: str, tag_id: Optional[str], label: str) -> Optional[str]:
    if not tag_id:
        return None
    return await ensure_owned(db, Tag, tag_id, tenant_id, label)


@router.post("/monitors", status_code=201, dependencies=[Depends(require_any(*CONFIG))])
async def create_monitor(body: MonitorCreate, user: CurrentUser, db: DB):
    pid = await ensure_owned(db, Position, body.position_id, user.tenant_id, "position_id")
    pos = await db.get(Position, pid)
    tag_id = await _check_tag(db, user.tenant_id, body.tag_id, "tag_id")
    amb = await _check_tag(db, user.tenant_id, body.ambient_tag_id, "ambient_tag_id")
    now = clock.now()
    start = ps.aware(body.start_at) or now
    if start > now + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="start_at não pode estar no futuro.")
    win = DEFAULTS["window"][body.feature]
    wc = body.window_cycles or win.get("cycles") if body.feature == "stroke_time" else None
    wh = (body.window_hours or win.get("hours")) if body.feature != "stroke_time" else None
    anchor = start
    if wh:
        # time windows aligned to local midnight of the start day (00–08, 08–16, 16–24 for 8 h)
        tz = await ps.plant_tz(db, user.tenant_id, pos.machine_id)
        d = start.astimezone(tz).date()
        anchor = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)
    rel = body.failure_limit_relative
    fl = body.failure_limit
    if body.feature == "stroke_time" and fl is None:
        fl, rel = DEFAULTS["stroke_time_failure_limit_of_baseline"], True
    m = PredictiveMonitor(tenant_id=user.tenant_id, position_id=pid, machine_id=pos.machine_id, feature=body.feature,
                          tag_id=tag_id, ambient_tag_id=amb, direction=body.direction, window_cycles=wc,
                          window_hours=wh, anchor_at=anchor,
                          warmup_windows=body.warmup_windows if body.warmup_windows is not None
                          else DEFAULTS["warmup_windows"][body.feature],
                          failure_limit=fl, failure_limit_relative=bool(rel), sensor_resolution=body.sensor_resolution,
                          unit=body.unit or DEFAULT_UNIT[body.feature], params=body.params, enabled=body.enabled,
                          status={}, created_by=user.id)
    try:
        async with db.begin_nested():
            db.add(m)
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Esta posição já monitora este sinal.")
    await ps.create_reset(db, m, start, "initial", True, "início do monitoramento", user.id)
    rules = body.rules if body.rules is not None else (
        _suggested_rules(body.feature, fl, bool(rel)) if body.default_rules else [])
    for r in rules:
        await _add_rule(db, m, r)
    await _commit(db)
    names = await ps._names(db, user.tenant_id, [m.position_id], [m.machine_id])
    return await monitor_out(db, m, names, clock.now())


CONFIG_RESET_FIELDS = ("tag_id", "ambient_tag_id", "window_cycles", "window_hours", "direction")


@router.patch("/monitors/{monitor_id}", dependencies=[Depends(require_any(*CONFIG))])
async def update_monitor(monitor_id: str, body: MonitorPatch, user: CurrentUser, db: DB):
    m = await _get(db, PredictiveMonitor, monitor_id, user.tenant_id, "Monitor", lock=True)
    data = body.model_dump(exclude_unset=True)
    reason = data.pop("reason", None)
    if "tag_id" in data:
        data["tag_id"] = await _check_tag(db, user.tenant_id, data["tag_id"], "tag_id")
    if "ambient_tag_id" in data:
        data["ambient_tag_id"] = await _check_tag(db, user.tenant_id, data["ambient_tag_id"], "ambient_tag_id")
    if "window_cycles" in data and m.feature != "stroke_time":
        raise HTTPException(status_code=422, detail="Sinais analógicos usam janela por tempo (window_hours).")
    if "window_hours" in data and m.feature == "stroke_time":
        raise HTTPException(status_code=422, detail="stroke_time usa janela por ciclos (window_cycles).")
    if "params" in data:
        bad = set(data["params"] or {}) - set(DEFAULTS) - {"data_gap_s"}
        if bad:
            raise HTTPException(status_code=422, detail=f"Parâmetros desconhecidos: {sorted(bad)}")
    changed = [k for k in CONFIG_RESET_FIELDS if k in data and data[k] != getattr(m, k)]
    for k, v in data.items():
        if k in ("window_cycles", "window_hours") and v is None:
            continue
        setattr(m, k, v if k != "params" else (v or {}))
    if changed:
        # P-1/P-2: changing the window, aggregate or tag restarts the baseline (with warm-up)
        now = clock.now()
        await ps.create_reset(db, m, now, "config_change", True, reason or f"configuração alterada: {', '.join(changed)}",
                              user.id)
        if "window_hours" in changed:
            m.anchor_at = now
    await _commit(db)
    names = await ps._names(db, user.tenant_id, [m.position_id], [m.machine_id])
    return await monitor_out(db, m, names, clock.now())


@router.post("/monitors/{monitor_id}/rebaseline", dependencies=[Depends(require_any(*CONFIG))])
async def rebaseline(monitor_id: str, body: RebaselineIn, user: CurrentUser, db: DB):
    """P-2: manual 'new normal' (no warm-up); the reason stays in the audit trail."""
    m = await _get(db, PredictiveMonitor, monitor_id, user.tenant_id, "Monitor", lock=True)
    b = await ps.create_reset(db, m, clock.now(), "manual", False, body.reason.strip(), user.id)
    if b is None:
        raise HTTPException(status_code=409, detail="Já existe um reinício neste instante.")
    await _commit(db)
    return ps.baseline_out(b)


@router.post("/monitors/{monitor_id}/process", dependencies=[Depends(require_any(*CONFIG))])
async def process_now(monitor_id: str, user: CurrentUser, db: DB):
    m = await _get(db, PredictiveMonitor, monitor_id, user.tenant_id, "Monitor", lock=True)
    res = await pe.process_monitor(db, m, clock.now())
    await _commit(db)
    return res


# ══ rules ════════════════════════════════════════════════════════════════════

@router.get("/rules", dependencies=[Depends(require_any(*READ))])
async def list_rules(user: CurrentUser, db: DB, monitor_id: Optional[str] = None):
    q = select(PredictiveRule).where(PredictiveRule.tenant_id == user.tenant_id)
    if monitor_id:
        if not is_uuid(monitor_id):
            return []
        q = q.where(PredictiveRule.monitor_id == monitor_id)
    return [ps.rule_out(r) for r in (await db.execute(q.order_by(PredictiveRule.created_at))).scalars()]


@router.post("/rules", status_code=201, dependencies=[Depends(require_any(*CONFIG))])
async def create_rule(body: RuleCreate, user: CurrentUser, db: DB):
    m = await _get(db, PredictiveMonitor, body.monitor_id, user.tenant_id, "monitor_id")
    r = await _add_rule(db, m, body)
    await _commit(db)
    return ps.rule_out(r)


@router.patch("/rules/{rule_id}", dependencies=[Depends(require_any(*CONFIG))])
async def update_rule(rule_id: str, body: RulePatch, user: CurrentUser, db: DB):
    r = await _get(db, PredictiveRule, rule_id, user.tenant_id, "Regra")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "description":
            setattr(r, k, v)
    await _commit(db)
    return ps.rule_out(r)


@router.delete("/rules/{rule_id}", status_code=204, dependencies=[Depends(require_any(*CONFIG))])
async def delete_rule(rule_id: str, user: CurrentUser, db: DB):
    r = await _get(db, PredictiveRule, rule_id, user.tenant_id, "Regra")
    await db.delete(r)
    await _commit(db)


# ══ features ═════════════════════════════════════════════════════════════════

@router.get("/features", dependencies=[Depends(require_any(*READ))])
async def get_features(user: CurrentUser, db: DB, position_id: Optional[str] = None, feature: Optional[Feature] = None,
                       monitor_id: Optional[str] = None, start: Optional[datetime] = None,
                       stop: Optional[datetime] = None, limit: int = Query(2000, ge=1, le=10000)):
    """Series for the chart: per window x, phase, z, EWMA, CUSUM, rolling z, slope, triggers, RUL; the baselines
    (m0 ± 3σ band) and the limits (rules, failure limit)."""
    if not position_id and not monitor_id:
        raise HTTPException(status_code=422, detail="Informe position_id (ou monitor_id).")
    if monitor_id:
        ms_ = [await _get(db, PredictiveMonitor, monitor_id, user.tenant_id, "Monitor")]
    else:
        if not is_uuid(position_id):
            raise _404("Posição")
        await ensure_owned(db, Position, position_id, user.tenant_id, "position_id")
        ms_ = await _scope_monitors(db, user.tenant_id, position_id=position_id, feature=feature)
    names = await ps._names(db, user.tenant_id, [m.position_id for m in ms_], [m.machine_id for m in ms_])
    series = []
    for m in ms_:
        q = select(ConditionFeature).where(ConditionFeature.tenant_id == user.tenant_id,
                                           ConditionFeature.monitor_id == m.id)
        if start:
            q = q.where(ConditionFeature.end_at >= ps.aware(start))
        if stop:
            q = q.where(ConditionFeature.end_at <= ps.aware(stop))
        rows = list((await db.execute(q.order_by(ConditionFeature.i.desc()).limit(limit))).scalars().all())[::-1]
        bases = (await db.execute(select(ConditionBaseline).where(
            ConditionBaseline.tenant_id == user.tenant_id, ConditionBaseline.monitor_id == m.id)
            .order_by(ConditionBaseline.reset_at))).scalars().all()
        st = m.status or {}
        bands = []
        for b in bases:
            if b.m0 is not None:
                bands.append({"baseline_id": b.id, "from_end_at": clock.iso(b.established_at), "m0": b.m0,
                              "sigma": b.sigma, "low": b.m0 - 3 * b.sigma, "high": b.m0 + 3 * b.sigma})
        cur = bases[-1] if bases else None
        lf = m.failure_limit
        if lf is not None and m.failure_limit_relative:
            lf = lf * cur.m0 if cur is not None and cur.m0 is not None else None
        series.append({
            "monitor_id": m.id, "position_id": m.position_id, "position_name": names.get(m.position_id),
            "feature": m.feature, "feature_label": FEATURE_LABEL.get(m.feature), "unit": m.unit,
            "window": {"cycles": m.window_cycles} if m.window_cycles else {"hours": m.window_hours},
            "baselines": [ps.baseline_out(b) for b in bases], "bands": bands,
            "limits": {"failure_limit": lf, "ewma_limit": st.get("ewma_limit"), "cusum_h": st.get("cusum_h"),
                       "rules": [ps.rule_out(r) for r in await ps.rules_of(db, m)]},
            "points": [ps.feature_out(f) for f in rows],
        })
    return {"position_id": position_id or (ms_[0].position_id if ms_ else None), "series": series}


# ══ alerts ═══════════════════════════════════════════════════════════════════

@router.get("/alerts", dependencies=[Depends(require_any(*READ))])
async def list_alerts(user: CurrentUser, db: DB, state: Optional[list[str]] = Query(None),
                      severity: Optional[Severity] = None, line_id: Optional[str] = None,
                      position_id: Optional[str] = None, machine_id: Optional[str] = None,
                      feature: Optional[Feature] = None, limit: int = Query(200, ge=1, le=1000)):
    q = (select(PredictiveAlert).join(Machine, Machine.id == PredictiveAlert.machine_id)
         .where(PredictiveAlert.tenant_id == user.tenant_id))
    if state:
        bad = set(state) - {"open", "acknowledged", "converted", "closed"}
        if bad:
            raise HTTPException(status_code=422, detail=f"state inválido: {sorted(bad)}")
        q = q.where(PredictiveAlert.state.in_(state))
    if severity:
        q = q.where(PredictiveAlert.severity == severity)
    for val, col in ((line_id, Machine.line_id), (position_id, PredictiveAlert.position_id),
                     (machine_id, PredictiveAlert.machine_id)):
        if val:
            if not is_uuid(val):
                return []
            q = q.where(col == val)
    if feature:
        q = q.where(PredictiveAlert.feature == feature)
    rows = list((await db.execute(q.order_by(PredictiveAlert.opened_at.desc()).limit(limit))).scalars().all())
    rows.sort(key=lambda a: (a.state == "closed", -SEV_RANK[a.severity], -a.opened_at.timestamp()))
    names = await ps._names(db, user.tenant_id, [a.position_id for a in rows], [a.machine_id for a in rows])
    return [await ps.alert_out(db, a, user, full=False, names=names) for a in rows]


@router.get("/alerts/{alert_id}", dependencies=[Depends(require_any(*READ))])
async def get_alert(alert_id: str, user: CurrentUser, db: DB):
    a = await _get(db, PredictiveAlert, alert_id, user.tenant_id, "Alerta")
    return await ps.alert_out(db, a, user)


@router.post("/alerts/{alert_id}/transition", dependencies=[Depends(require_any(*READ))])
async def transition(alert_id: str, body: TransitionIn, user: CurrentUser, db: DB):
    """P-8 lifecycle. Invalid → 422 {message, allowed, state, to}; role not allowed → 403."""
    a = await _get(db, PredictiveAlert, alert_id, user.tenant_id, "Alerta", lock=True)
    if body.to == "converted":
        return await convert(alert_id, ConvertIn(note=body.comment), user, db)
    await ps.transition_alert(db, a, body.to, user, resolution=body.resolution, comment=body.comment, now=clock.now())
    await _commit(db)
    await db.refresh(a)
    return await ps.alert_out(db, a, user)


@router.post("/alerts/{alert_id}/convert", status_code=201, dependencies=[Depends(require_any(*ps.CLOSE_ROLES))])
async def convert(alert_id: str, body: ConvertIn, user: CurrentUser, db: DB):
    """P-9: creates ONE predictive WO (state approved) linked to the alert."""
    from ...services.maintenance import service as ms
    a = await _get(db, PredictiveAlert, alert_id, user.tenant_id, "Alerta", lock=True)
    from ...auth import has_any
    if not has_any(user, ps.CLOSE_ROLES):
        raise HTTPException(status_code=403, detail="Seu papel não permite gerar OS preditiva.")
    wo = await ps.convert_alert(db, a, user, clock.now(), estimated_hours=body.estimated_hours, note=body.note)
    await _commit(db)
    await db.refresh(a)
    await db.refresh(wo)
    return {**(await ps.alert_out(db, a, user)), "work_order": await ms.wo_out(db, wo, user)}


# ══ evaluation / recompute / badges ═════════════════════════════════════════

@router.get("/evaluation", dependencies=[Depends(require_any("maintenance_planner", "manager"))])
async def evaluation(user: CurrentUser, db: DB, start: Optional[datetime] = None, stop: Optional[datetime] = None,
                     line_id: Optional[str] = None):
    now = clock.now()
    stop = ps.aware(stop) or now
    start = ps.aware(start) or (stop - timedelta(days=365))
    if start >= stop:
        raise HTTPException(status_code=422, detail="start deve ser anterior a stop.")
    if line_id:
        await ensure_owned(db, Line, line_id, user.tenant_id, "line_id")
    return await evaluate_tenant(db, user.tenant_id, start, stop, now, line_id)


@router.post("/recompute", dependencies=[Depends(require_any("admin"))])
async def recompute(body: RecomputeIn, user: CurrentUser, db: DB):
    """P-12: recompute since the last baseline of each monitor (or one) — must reproduce the same alerts."""
    if body.monitor_id:
        ms_ = [await _get(db, PredictiveMonitor, body.monitor_id, user.tenant_id, "Monitor", lock=True)]
    else:
        ms_ = list((await db.execute(select(PredictiveMonitor).where(
            PredictiveMonitor.tenant_id == user.tenant_id))).scalars().all())
    out = [await pe.recompute(db, m, clock.now()) for m in ms_]
    await _commit(db)
    return {"monitors": out, "same": all(r["same"] for r in out)}


@router.get("/badges")
async def badges(user: CurrentUser, db: DB, machine_id: Optional[str] = None):
    """Condition badge per machine ('condição: atenção/alerta') — the only predictive data the operator sees."""
    q = select(PredictiveAlert.machine_id, PredictiveAlert.severity).where(
        PredictiveAlert.tenant_id == user.tenant_id, PredictiveAlert.state != "closed")
    if machine_id:
        if not is_uuid(machine_id):
            return {}
        q = q.where(PredictiveAlert.machine_id == machine_id)
    out: dict[str, str] = {}
    for mid, sev in (await db.execute(q)).all():
        if mid not in out or SEV_RANK[sev] > SEV_RANK[out[mid]]:
            out[mid] = sev
    labels = {"watch": "condição: atenção", "warning": "condição: alerta", "critical": "condição: crítico"}
    return {mid: {"severity": s, "label": labels[s]} for mid, s in out.items()}


# ══ production plan (P-7) / condition plans (P-9) ════════════════════════════

@router.put("/production-plan", dependencies=[Depends(require_any(*CONFIG))])
async def put_production_plan(body: ProductionPlanIn, user: CurrentUser, db: DB):
    mid = await ensure_owned(db, Machine, body.machine_id, user.tenant_id, "machine_id")
    days = sorted({d.day for d in body.days})
    if days:
        await db.execute(delete(ProductionPlanDay).where(ProductionPlanDay.tenant_id == user.tenant_id,
                                                         ProductionPlanDay.machine_id == mid,
                                                         ProductionPlanDay.day.in_(days)))
    for d in body.days:
        db.add(ProductionPlanDay(tenant_id=user.tenant_id, machine_id=mid, day=d.day, planned_parts=d.planned_parts,
                                 planned_hours=d.planned_hours))
    await _commit(db)
    return await get_production_plan(user, db, machine_id=mid)


@router.get("/production-plan", dependencies=[Depends(require_any(*READ))])
async def get_production_plan(user: CurrentUser, db: DB, machine_id: str = Query(...)):
    mid = await ensure_owned(db, Machine, machine_id, user.tenant_id, "machine_id")
    rows = (await db.execute(select(ProductionPlanDay).where(
        ProductionPlanDay.tenant_id == user.tenant_id, ProductionPlanDay.machine_id == mid)
        .order_by(ProductionPlanDay.day))).scalars().all()
    return {"machine_id": mid, "days": [{"day": r.day.isoformat(), "planned_parts": r.planned_parts,
                                         "planned_hours": r.planned_hours} for r in rows]}


@router.post("/condition-plans", status_code=201, dependencies=[Depends(require_any(*CONFIG))])
async def create_condition_plan(body: ConditionPlanIn, user: CurrentUser, db: DB):
    """P-9: MaintenancePlan with condition_trigger = {feature, min_severity}, wo_type predictive. When an alert of the
    position reaches min_severity the server creates ONE draft of the plan and the alert becomes `converted`."""
    import uuid as _uuid
    pid = await ensure_owned(db, Position, body.position_id, user.tenant_id, "position_id")
    pos = await db.get(Position, pid)
    now = clock.now()
    plan = MaintenancePlan(tenant_id=user.tenant_id, position_id=pid, machine_id=pos.machine_id,
                           name=body.name or f"Preditiva por condição — {pos.name}: {FEATURE_LABEL[body.feature]}",
                           description=body.description,
                           task_list=[{"id": _uuid.uuid4().hex[:8], "text": t} for t in body.task_list],
                           estimated_hours=body.estimated_hours, wo_type="predictive",
                           condition_trigger={"feature": body.feature, "min_severity": body.min_severity},
                           stops_machine=body.stops_machine, last_reset_at=now, last_reset_usage={},
                           last_reset_by="cadastro", created_by=user.id)
    db.add(plan)
    await _commit(db)
    return {"id": plan.id, "position_id": plan.position_id, "machine_id": plan.machine_id, "name": plan.name,
            "wo_type": plan.wo_type, "condition_trigger": plan.condition_trigger,
            "estimated_hours": plan.estimated_hours, "condition_due_date": None}
