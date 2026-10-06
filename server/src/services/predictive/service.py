"""Predictive module — configuration helpers, baseline resets, alert lifecycle (P-8), conversion into predictive
work orders and condition plans (P-9), hooks called by the maintenance workflow, notifications and serialization.

Everything filters tenant_id. Functions never commit; routes/engine commit and then call `deliver(db)`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (Component, ConditionBaseline, ConditionFeature, FailureRecord, Machine,
                       MaintenancePlan, Position, PredictiveAlert, PredictiveAlertEvent, PredictiveMonitor,
                       PredictiveRule, User, WorkOrder)
from ...notifications import service as notif
from .. import clock
from . import core
from .defaults import COMPATIBLE_MODES, DEFAULTS, DEFAULT_UNIT, FEATURE_LABEL, MANUAL_CLOSE_RESOLUTIONS, SEV_LABEL

log = structlog.get_logger()

READ_ROLES = ("maintenance_planner", "manager", "technician", "production_supervisor")
CONFIG_ROLES = ("maintenance_planner", "manager")             # + admin (has_any)
ACK_ROLES = ("maintenance_planner", "manager", "technician")
CLOSE_ROLES = ("maintenance_planner", "manager")
NOT_STARTED = ("draft", "open", "approved", "scheduled")

notif.KINDS.setdefault("predictive_watch", notif.KindSpec("Preditiva: atenção (resumo diário)", email=True, digest=True))
notif.KINDS.setdefault("predictive_warning", notif.KindSpec("Preditiva: alerta (sugere OS preditiva)", email=True,
                                                            priority="high"))
notif.KINDS.setdefault("predictive_critical", notif.KindSpec("Preditiva: crítico", email=True, web_push=True,
                                                             priority="high"))

_WS_KEY = "fm_predictive_ws"


def _422(msg, **extra) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": msg, **extra} if extra else msg)


def aware(t: Optional[datetime]) -> Optional[datetime]:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


# ── configuration ─────────────────────────────────────────────────────────────

async def rules_of(db: AsyncSession, m: PredictiveMonitor) -> list[PredictiveRule]:
    return list((await db.execute(select(PredictiveRule).where(
        PredictiveRule.tenant_id == m.tenant_id, PredictiveRule.monitor_id == m.id)
        .order_by(PredictiveRule.created_at, PredictiveRule.code))).scalars().all())


async def position_name(db: AsyncSession, m: PredictiveMonitor) -> str:
    return (await db.execute(select(Position.name).where(Position.id == m.position_id,
                                                         Position.tenant_id == m.tenant_id))).scalar_one_or_none() or ""


def monitor_cfg(m: PredictiveMonitor, rules: list[PredictiveRule], label: str = "") -> core.MonitorCfg:
    return core.MonitorCfg(
        feature=m.feature, direction=m.direction, warmup_windows=m.warmup_windows, failure_limit=m.failure_limit,
        failure_limit_relative=bool(m.failure_limit_relative), sensor_resolution=m.sensor_resolution or 0.0,
        rules=[core.RuleCfg(code=r.code, type=r.type, value=r.value, sustained_for=r.sustained_for,
                            severity=r.severity, enabled=r.enabled) for r in rules],
        params={k: v for k, v in (m.params or {}).items() if k in DEFAULTS}, label=label,
        unit=m.unit or DEFAULT_UNIT.get(m.feature, ""))


async def plant_tz(db: AsyncSession, tenant_id: str, machine_id: str) -> ZoneInfo:
    from ..maintenance import usage as mu
    return (await mu.machine_tz(db, tenant_id, [machine_id])).get(machine_id) or await mu.tenant_tz(db, tenant_id)


# ── baseline resets (P-2) ─────────────────────────────────────────────────────

async def latest_reset(db: AsyncSession, m: PredictiveMonitor) -> Optional[ConditionBaseline]:
    return (await db.execute(select(ConditionBaseline).where(
        ConditionBaseline.tenant_id == m.tenant_id, ConditionBaseline.monitor_id == m.id)
        .order_by(ConditionBaseline.reset_at.desc(), ConditionBaseline.created_at.desc()).limit(1))).scalar_one_or_none()


async def create_reset(db: AsyncSession, m: PredictiveMonitor, at: datetime, kind: str, warmup: bool,
                       reason: Optional[str] = None, user_id: Optional[str] = None,
                       source_id: Optional[str] = None) -> Optional[ConditionBaseline]:
    """New baseline segment from `at`. A reset at or before the current one is ignored (history replayed out of
    order); the same instant twice (WO completion + its component swap) keeps the first."""
    last = await latest_reset(db, m)
    if last is not None and aware(at) <= aware(last.reset_at):
        return None
    b = ConditionBaseline(tenant_id=m.tenant_id, monitor_id=m.id, position_id=m.position_id, feature=m.feature,
                          reset_at=at, reset_kind=kind, warmup=warmup, reason=reason, user_id=user_id,
                          source_id=source_id)
    db.add(b)
    await db.flush()
    st = dict(m.status or {})
    st.update(phase="warmup" if warmup and m.warmup_windows else "learning", baseline=None, ewma=None, cusum=None,
              cycle_cursor=None)
    m.status = st
    return b


async def monitors_of_position(db: AsyncSession, tenant_id: str, position_id: str) -> list[PredictiveMonitor]:
    return list((await db.execute(select(PredictiveMonitor).where(
        PredictiveMonitor.tenant_id == tenant_id, PredictiveMonitor.position_id == position_id))).scalars().all())


async def sync_resets(db: AsyncSession, m: PredictiveMonitor, now: datetime) -> list[ConditionBaseline]:
    """Automatic resets the workflow did not deliver through the hooks (component installed at the position from
    the components API; WO completed with resets_baseline) — idempotent, ordered by time."""
    last = await latest_reset(db, m)
    since = aware(last.reset_at) if last else None
    found: list[tuple[datetime, str, str, str]] = []
    q = select(Component).where(Component.tenant_id == m.tenant_id, Component.installed_at.is_not(None),
                                Component.installed_at <= now,
                                or_(Component.position_id == m.position_id, Component.last_position_id == m.position_id))
    if since is not None:
        q = q.where(Component.installed_at > since)
    for c in (await db.execute(q)).scalars():
        found.append((aware(c.installed_at), "installation", f"componente {c.serial_number or c.id[:8]} instalado", c.id))
    q = select(WorkOrder).where(WorkOrder.tenant_id == m.tenant_id, WorkOrder.position_id == m.position_id,
                                WorkOrder.resets_baseline.is_(True), WorkOrder.completed_at.is_not(None),
                                WorkOrder.completed_at <= now)
    if since is not None:
        q = q.where(WorkOrder.completed_at > since)
    from ..maintenance.service import wo_label
    for w in (await db.execute(q)).scalars():
        found.append((aware(w.completed_at), "work_order", f"{wo_label(w.number)} concluída", w.id))
    out = []
    for at, kind, reason, sid in sorted(found):
        b = await create_reset(db, m, at, kind, True, reason, None, sid)
        if b is not None:
            out.append(b)
    return out


async def reset_position(db: AsyncSession, tenant_id: str, position_id: str, at: datetime, kind: str, reason: str,
                         source_id: Optional[str] = None) -> int:
    n = 0
    for m in await monitors_of_position(db, tenant_id, position_id):
        if await create_reset(db, m, at, kind, True, reason, None, source_id) is not None:
            n += 1
    return n


# ── alert events / notifications / websocket ──────────────────────────────────

def add_event(db: AsyncSession, a: PredictiveAlert, at: datetime, action: str, source: str, *,
              from_state: Optional[str] = None, to_state: Optional[str] = None, severity: Optional[str] = None,
              resolution: Optional[str] = None, comment: Optional[str] = None, by: Optional[str] = None,
              work_order_id: Optional[str] = None, window_i: Optional[int] = None) -> PredictiveAlertEvent:
    ev = PredictiveAlertEvent(tenant_id=a.tenant_id, alert_id=a.id, at=at, action=action, source=source,
                              from_state=from_state, to_state=to_state, severity=severity, resolution=resolution,
                              comment=comment, by=by, work_order_id=work_order_id, window_i=window_i)
    db.add(ev)
    return ev


async def machine_line(db: AsyncSession, tenant_id: str, machine_id: str) -> Optional[str]:
    return (await db.execute(select(Machine.line_id).where(Machine.id == machine_id,
                                                           Machine.tenant_id == tenant_id))).scalar_one_or_none()


async def notify_alert(db: AsyncSession, a: PredictiveAlert, event: str) -> None:
    """P-8 effects: watch → planner's daily summary; warning → bell + e-mail (planner, manager); critical → push to
    planner and on-call technician of the area. Every escalation notifies."""
    line_id = await machine_line(db, a.tenant_id, a.machine_id)
    pos = (await db.execute(select(Position.name).where(Position.id == a.position_id))).scalar_one_or_none() or ""
    title = f"{SEV_LABEL[a.severity]} preditiva — {pos}: {FEATURE_LABEL.get(a.feature, a.feature)}"
    link = f"/maintenance/predictive?alert={a.id}"
    data = {"alert_id": a.id, "severity": a.severity, "position_id": a.position_id, "feature": a.feature,
            "event": event}
    if a.severity == "watch":
        users = await notif.users_with_roles(db, a.tenant_id, ("maintenance_planner",), line_id)
        await notif.notify(db, a.tenant_id, users, "predictive_watch", title, body=a.explanation, link=link,
                           data=data, line_id=line_id, group_key="predictive_watch",
                           grouped_title="{count} sinais em atenção (preditiva)")
    elif a.severity == "warning":
        users = await notif.users_with_roles(db, a.tenant_id, ("maintenance_planner", "manager"), line_id)
        await notif.notify(db, a.tenant_id, users, "predictive_warning", title, body=a.explanation, link=link,
                           data=data, line_id=line_id)
    else:
        users = await notif.users_with_roles(db, a.tenant_id, ("maintenance_planner",), line_id)
        users += await notif.users_with_roles(db, a.tenant_id, ("technician",), line_id, on_call=True)
        await notif.notify(db, a.tenant_id, users, "predictive_critical", title, body=a.explanation, link=link,
                           data=data, line_id=line_id)


async def queue_ws(db: AsyncSession, a: PredictiveAlert, event: str) -> None:
    """{type: 'predictive_alert', event, alert} to the users allowed to read alerts (not the whole tenant socket:
    the operator only sees the condition badge)."""
    line_id = await machine_line(db, a.tenant_id, a.machine_id)
    users = await notif.users_with_roles(db, a.tenant_id, READ_ROLES + ("admin", "superadmin"), line_id)
    msg = {"type": "predictive_alert", "event": event, "alert": await alert_out(db, a, full=False)}
    db.info.setdefault(_WS_KEY, []).append((a.tenant_id, [u.id for u in users], msg))


async def deliver(db: AsyncSession) -> None:
    """After commit: queued WebSocket messages and notifications."""
    from ...websocket.manager import manager
    for tenant_id, user_ids, msg in db.info.pop(_WS_KEY, []):
        for uid in user_ids:
            try:
                await manager.send_to_user(str(tenant_id), str(uid), msg)
            except Exception:  # noqa: BLE001
                log.warning("predictive.ws_send_failed", tenant_id=tenant_id)
    await notif.deliver_pending(db)


def discard_pending(db: AsyncSession) -> None:
    db.info.pop(_WS_KEY, None)


# ── lifecycle (P-8) ───────────────────────────────────────────────────────────

USER_TRANSITIONS = {"open": ("acknowledged", "closed"), "acknowledged": ("closed",), "converted": (), "closed": ()}


def allowed_transitions(a: PredictiveAlert) -> list[str]:
    out = list(USER_TRANSITIONS.get(a.state, ()))
    if a.state in ("open", "acknowledged"):
        out.append("converted")
    return out


def user_allowed(user: User, a: PredictiveAlert) -> list[str]:
    from ...auth import has_any
    out = []
    for t in allowed_transitions(a):
        if t == "acknowledged" and has_any(user, ACK_ROLES):
            out.append(t)
        elif t in ("closed", "converted") and has_any(user, CLOSE_ROLES):
            out.append(t)
    return out


async def transition_alert(db: AsyncSession, a: PredictiveAlert, to: str, user: User, *, resolution: Optional[str],
                           comment: Optional[str], now: datetime) -> Optional[ConditionBaseline]:
    from ...auth import has_any
    if to not in allowed_transitions(a) or to == "converted":
        raise _422(f"Transição inválida: {a.state} → {to}." + (" Use POST …/convert." if to == "converted" else ""),
                   allowed=allowed_transitions(a), state=a.state, to=to)
    roles = ACK_ROLES if to == "acknowledged" else CLOSE_ROLES
    if not has_any(user, roles):
        raise HTTPException(status_code=403, detail="Seu papel não permite esta transição.")
    frm = a.state
    new_base = None
    if to == "acknowledged":
        a.state, a.acknowledged = "acknowledged", True
        add_event(db, a, now, "acknowledge", "user", from_state=frm, to_state=to, comment=comment, by=user.id)
    else:
        if resolution not in MANUAL_CLOSE_RESOLUTIONS:
            raise _422("Fechar exige `resolution`: " + ", ".join(MANUAL_CLOSE_RESOLUTIONS) + ".",
                       allowed_resolutions=list(MANUAL_CLOSE_RESOLUTIONS))
        if resolution == "rebaseline" and not (comment or "").strip():
            raise _422("Reiniciar a linha de base exige o motivo (`comment`).")
        a.state, a.resolution, a.closed_at, a.closed_by = "closed", resolution, now, user.id
        add_event(db, a, now, "close", "user", from_state=frm, to_state="closed", resolution=resolution,
                  comment=comment, by=user.id)
        if resolution == "rebaseline":
            m = await db.get(PredictiveMonitor, a.monitor_id)
            new_base = await create_reset(db, m, now, "alert_rebaseline", False, comment, user.id, a.id)
        await queue_ws(db, a, "closed")
    await db.flush()
    return new_base


def _wo_title(a: PredictiveAlert, pos: str) -> str:
    return f"Preditiva — {pos}: {FEATURE_LABEL.get(a.feature, a.feature)}"[:255]


async def condition_plan(db: AsyncSession, a: PredictiveAlert) -> Optional[MaintenancePlan]:
    plans = (await db.execute(select(MaintenancePlan).where(
        MaintenancePlan.tenant_id == a.tenant_id, MaintenancePlan.position_id == a.position_id,
        MaintenancePlan.is_active.is_(True), MaintenancePlan.condition_trigger.is_not(None)))).scalars().all()
    for p in plans:
        ct = p.condition_trigger or {}
        if ct.get("feature") == a.feature:
            return p
    return None


def _due_from_rul(a: PredictiveAlert, today: date) -> date:
    r = a.rul or {}
    lo = r.get("projected_date_low")
    if lo:
        return date.fromisoformat(lo)
    return today + timedelta(days=7)


async def convert_alert(db: AsyncSession, a: PredictiveAlert, user: Optional[User], now: datetime, *,
                        estimated_hours: Optional[float] = None, note: Optional[str] = None,
                        source: str = "user", at: Optional[datetime] = None) -> WorkOrder:
    """P-9: one `predictive` WO, state `approved` (who converts approves), due_by = data_low of the RUL (no RUL:
    today + 7 days). With a condition plan at the position, the plan's draft is used instead (source='plan')."""
    from ..maintenance import service as ms
    from .. import webhooks
    if a.state not in ("open", "acknowledged"):
        raise _422(f"Só alerta aberto ou reconhecido vira OS (estado {a.state}).", allowed=allowed_transitions(a),
                   state=a.state, to="converted")
    if a.work_order_id:
        wo = await db.get(WorkOrder, a.work_order_id)
        if wo is not None and wo.state not in ("cancelled", "completed", "closed"):
            raise _422("O alerta já tem uma OS preditiva aberta.", work_order_id=wo.id)
    at = at or now
    tz = await plant_tz(db, a.tenant_id, a.machine_id)
    today = at.astimezone(tz).date()
    pos = (await db.execute(select(Position.name).where(Position.id == a.position_id))).scalar_one_or_none() or ""
    plan = await condition_plan(db, a)
    desc = (a.explanation or "") + f"\n\nGráfico: /maintenance/predictive?alert={a.id}"
    frm = a.state
    if source == "plan" and plan is not None:
        wo = await ms.create_plan_draft(db, plan, at, by=None, note="gerada pelo plano de condição (preditiva)")
        if wo is None:
            return None  # type: ignore[return-value]  — plan already has an open WO (S4-4)
        wo.source_alert_id, wo.description, wo.due_by = a.id, desc, _due_from_rul(a, today)
        wo.title = plan.name or _wo_title(a, pos)
        plan.condition_due_date = _due_from_rul(a, today) if (a.rul or {}).get("projected_date_low") else today
        plan.condition_alert_id = a.id
    else:
        number = await ms.next_number(db, a.tenant_id)
        est = estimated_hours if estimated_hours is not None else (plan.estimated_hours if plan else 0.0)
        wo = WorkOrder(tenant_id=a.tenant_id, number=number, type="predictive", urgency=None, state="approved",
                       plan_id=None, position_id=a.position_id, machine_id=a.machine_id, title=_wo_title(a, pos),
                       description=desc, task_list=[{"id": t.get("id"), "text": t.get("text"), "done": False}
                                                    for t in ((plan.task_list or []) if plan else [])],
                       estimated_hours=est or 0.0, stops_machine=bool(plan.stops_machine) if plan else False,
                       created_by=user.id if user else None, created_at=at, requested_at=at, approved_at=at,
                       source_alert_id=a.id, due_by=_due_from_rul(a, today))
        db.add(wo)
        await db.flush()
        await ms.add_history(db, wo, at, user, None, "approved", note or "gerada do alerta preditivo")
        await webhooks.enqueue(db, a.tenant_id, "work_order.created", await ms.wo_out(db, wo, full=False))
    a.state, a.work_order_id, a.acknowledged = "converted", wo.id, True
    add_event(db, a, at, "convert", source, from_state=frm, to_state="converted", comment=note,
              by=user.id if user else None, work_order_id=wo.id)
    await queue_ws(db, a, "converted")
    await db.flush()
    return wo


# ── hooks from the maintenance workflow ───────────────────────────────────────

async def validate_wo_transition(db: AsyncSession, wo: WorkOrder, to: str, finding: Optional[str]) -> None:
    """P-9: completing a predictive WO born from an alert requires `finding`; `defect_confirmed` requires a
    FailureRecord of the WO (severity incipient/degraded, detection continuous_condition_monitoring, ISO 14224 mode
    compatible with the signal)."""
    if to != "completed" or not wo.source_alert_id:
        return
    finding = finding or wo.finding
    if finding not in ("defect_confirmed", "no_defect"):
        raise _422("Concluir OS preditiva exige `finding`: defect_confirmed ou no_defect.",
                   allowed_findings=["defect_confirmed", "no_defect"])
    if finding == "defect_confirmed":
        a = await db.get(PredictiveAlert, wo.source_alert_id)
        modes = COMPATIBLE_MODES.get(a.feature, ()) if a is not None else ()
        fr = (await db.execute(select(FailureRecord).where(
            FailureRecord.tenant_id == wo.tenant_id,
            or_(FailureRecord.work_order_id == wo.id, FailureRecord.id == wo.failure_id)))).scalars().all()
        ok = [f for f in fr if f.severity in ("incipient", "degraded")
              and f.detection_method == "continuous_condition_monitoring" and f.failure_mode in modes]
        if not ok:
            raise _422("defect_confirmed exige um registro de falha da OS com severidade incipient/degraded, "
                       "detection_method = continuous_condition_monitoring e modo ISO 14224 compatível "
                       f"({', '.join(modes)}).", compatible_modes=list(modes))


def default_resets_baseline(wo: WorkOrder, had_replacements: bool) -> bool:
    """P-2: true for WOs with a component swap and for corrective/predictive; false otherwise."""
    return had_replacements or wo.type in ("corrective", "predictive")


async def on_wo_transition(db: AsyncSession, wo: WorkOrder, frm: str, to: str, at: datetime, *,
                           finding: Optional[str] = None, resets_baseline: Optional[bool] = None,
                           replaced_positions: Iterable[str] = ()) -> None:
    """Called by maintenance.service.transition after the state change (same transaction)."""
    replaced = set(replaced_positions)
    if to == "completed":
        from ...models import WorkOrderReplacement
        replaced |= set((await db.execute(select(WorkOrderReplacement.position_id).where(
            WorkOrderReplacement.tenant_id == wo.tenant_id, WorkOrderReplacement.work_order_id == wo.id,
            WorkOrderReplacement.applied_at.is_not(None)))).scalars().all())
        if finding:
            wo.finding = finding
        eff = resets_baseline if resets_baseline is not None else default_resets_baseline(wo, bool(replaced))
        wo.resets_baseline = eff
        for pid in replaced:
            await reset_position(db, wo.tenant_id, pid, at, "installation", "componente trocado na OS", wo.id)
        if eff and wo.position_id:
            from ..maintenance.service import wo_label
            await reset_position(db, wo.tenant_id, wo.position_id, at, "work_order", f"{wo_label(wo.number)} concluída",
                                 wo.id)
        if wo.source_alert_id:
            a = await db.get(PredictiveAlert, wo.source_alert_id)
            if a is not None and a.tenant_id == wo.tenant_id and a.state == "converted":
                res = "confirmed" if (wo.finding or finding) == "defect_confirmed" else "false_alarm"
                a.state, a.resolution, a.closed_at = "closed", res, at
                add_event(db, a, at, "close", "work_order", from_state="converted", to_state="closed",
                          resolution=res, work_order_id=wo.id, comment=f"OS concluída: {wo.finding or finding}")
                await queue_ws(db, a, "closed")
    elif to == "cancelled" and wo.source_alert_id:
        a = await db.get(PredictiveAlert, wo.source_alert_id)
        if a is not None and a.tenant_id == wo.tenant_id and a.state == "converted" and wo.cancel_reason != "superseded":
            a.state = "acknowledged"
            add_event(db, a, at, "unconvert", "work_order", from_state="converted", to_state="acknowledged",
                      work_order_id=wo.id, comment=f"OS cancelada ({wo.cancel_reason})")
            await queue_ws(db, a, "updated")


async def failure_sweep(db: AsyncSession, m: PredictiveMonitor, now: datetime) -> int:
    """P-8: a critical/degraded failure registered at the position while the alert is not closed closes it with
    `failed_before_action`; its predictive WO not started yet is cancelled `superseded`. Failures recorded as the
    finding of the alert's own WO (condition monitoring) do not count."""
    from ..maintenance.service import add_history
    alerts = (await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id,
        PredictiveAlert.state != "closed"))).scalars().all()
    n = 0
    for a in alerts:
        q = select(FailureRecord).where(
            FailureRecord.tenant_id == m.tenant_id, FailureRecord.position_id == m.position_id,
            FailureRecord.severity.in_(("critical", "degraded")), FailureRecord.failed_at >= a.opened_at,
            FailureRecord.failed_at <= now,
            or_(FailureRecord.detection_method.is_(None),
                FailureRecord.detection_method != "continuous_condition_monitoring"))
        if a.work_order_id:
            q = q.where(or_(FailureRecord.work_order_id.is_(None), FailureRecord.work_order_id != a.work_order_id))
        f = (await db.execute(q.order_by(FailureRecord.failed_at).limit(1))).scalar_one_or_none()
        if f is None:
            continue
        frm = a.state
        at = aware(f.failed_at)
        a.state, a.resolution, a.closed_at = "closed", "failed_before_action", at
        add_event(db, a, at, "close", "failure", from_state=frm, to_state="closed", resolution="failed_before_action",
                  comment=f"falha registrada na posição ({f.failure_mode or 'modo a classificar'})")
        if a.work_order_id:
            wo = await db.get(WorkOrder, a.work_order_id)
            if wo is not None and wo.state in NOT_STARTED:
                old = wo.state
                wo.state, wo.cancel_reason, wo.cancelled_at = "cancelled", "superseded", now
                await add_history(db, wo, now, None, old, "cancelled", "substituída pela corretiva (falha antes da ação)")
        await queue_ws(db, a, "closed")
        n += 1
    return n


def apply_condition_due(plan: MaintenancePlan, st, today: date):
    """P-9: a condition plan whose alert reached min_severity is at least `due` (due_trigger = condition,
    next_due_date = data_low of the RUL or the conversion day); the other triggers keep counting."""
    from dataclasses import replace
    from ..maintenance import core as mcore
    if not plan.condition_trigger or plan.condition_due_date is None:
        return st
    due = plan.condition_due_date
    days = max((due - today).days, 0)
    if st is None:
        return mcore.PlanStatus("due", 1.0, "condition", [], days, "condition", due, None, due < today)
    state = st.state if SEV_PLAN.get(st.state, 0) >= SEV_PLAN["due"] else "due"
    nd = st.next_due_date
    if nd is None or due <= nd:
        return replace(st, state=state, due_trigger="condition", next_due_date=due, days_to_due=days)
    return replace(st, state=state)


SEV_PLAN = {"ok": 0, "due_soon": 1, "due": 2, "overdue": 3}


# ── serialization ─────────────────────────────────────────────────────────────

async def _names(db: AsyncSession, tenant_id: str, position_ids: Iterable[str], machine_ids: Iterable[str]) -> dict:
    from ..maintenance.service import asset_names
    return await asset_names(db, tenant_id, machine_ids, position_ids)


def _rul_public(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {k: v for k, v in r.items()}


async def alert_out(db: AsyncSession, a: PredictiveAlert, user: Optional[User] = None, full: bool = True,
                    names: Optional[dict] = None) -> dict:
    from ..maintenance.service import wo_label
    if names is None:
        names = await _names(db, a.tenant_id, [a.position_id], [a.machine_id])
    wo_number = wo_state = None
    if a.work_order_id:
        row = (await db.execute(select(WorkOrder.number, WorkOrder.state).where(
            WorkOrder.id == a.work_order_id, WorkOrder.tenant_id == a.tenant_id))).first()
        if row:
            wo_number, wo_state = wo_label(row[0]), row[1]
    m = await db.get(PredictiveMonitor, a.monitor_id)
    out = {
        "id": a.id, "monitor_id": a.monitor_id, "baseline_id": a.baseline_id,
        "position_id": a.position_id, "position_name": names.get(a.position_id),
        "machine_id": a.machine_id, "machine_name": names.get(a.machine_id),
        "line_id": names.get(("line", a.machine_id)), "line_name": names.get(("line_name", a.machine_id)),
        "feature": a.feature, "feature_label": FEATURE_LABEL.get(a.feature, a.feature),
        "unit": (m.unit if m else None) or DEFAULT_UNIT.get(a.feature),
        "state": a.state, "severity": a.severity, "severity_label": SEV_LABEL[a.severity],
        "opened_at": clock.iso(a.opened_at), "opened_window": a.opened_window,
        "first_triggers": a.first_triggers, "escalations": a.escalations, "escalation_windows": a.escalation_windows,
        "triggers_seen": a.triggers_seen,
        "triggers": sorted((a.evidence or {}).values(), key=lambda e: (e.get("first_i") or 0, e.get("source"))),
        "explanation": a.explanation, "rul_condition": _rul_public(a.rul),
        "acknowledged": a.acknowledged, "work_order_id": a.work_order_id, "work_order_number": wo_number,
        "work_order_state": wo_state, "resolution": a.resolution, "closed_at": clock.iso(a.closed_at),
        "allowed_transitions": allowed_transitions(a),
        "user_allowed_transitions": user_allowed(user, a) if user is not None else None,
    }
    if full:
        rows = (await db.execute(select(PredictiveAlertEvent, User.name).outerjoin(User, User.id == PredictiveAlertEvent.by)
                                 .where(PredictiveAlertEvent.alert_id == a.id,
                                        PredictiveAlertEvent.tenant_id == a.tenant_id)
                                 .order_by(PredictiveAlertEvent.at, PredictiveAlertEvent.recorded_at))).all()
        out["history"] = [{"at": clock.iso(e.at), "action": e.action, "source": e.source, "from_state": e.from_state,
                           "to_state": e.to_state, "severity": e.severity, "resolution": e.resolution,
                           "comment": e.comment, "by": e.by, "by_name": n, "work_order_id": e.work_order_id,
                           "window": e.window_i} for e, n in rows]
        out["last_point"] = a.last_point
        if a.baseline_id:
            b = await db.get(ConditionBaseline, a.baseline_id)
            out["baseline"] = baseline_out(b) if b else None
    return out


def baseline_out(b: ConditionBaseline) -> dict:
    return {"id": b.id, "reset_at": clock.iso(b.reset_at), "reset_kind": b.reset_kind, "warmup": b.warmup,
            "reason": b.reason, "user_id": b.user_id, "source_id": b.source_id, "m0": b.m0, "mad": b.mad,
            "sigma": b.sigma, "sigma_floor": b.sigma_floor, "from_i": b.from_i, "to_i": b.to_i, "n": b.n,
            "established_at": clock.iso(b.established_at),
            "state": "established" if b.established_at else "learning"}


def rule_out(r: PredictiveRule) -> dict:
    return {"id": r.id, "monitor_id": r.monitor_id, "code": r.code, "type": r.type, "value": r.value,
            "sustained_for": r.sustained_for, "severity": r.severity, "enabled": r.enabled,
            "description": r.description}


def feature_out(f: ConditionFeature) -> dict:
    return {"i": f.i, "start_at": clock.iso(f.start_at), "end_at": clock.iso(f.end_at), "x": f.x, "n": f.n,
            "valid": f.valid, "u": f.u, "running_h": f.running_h, "coverage": f.coverage, "phase": f.phase,
            "z": f.z, "ewma": f.ewma, "cusum": f.cusum, "rolling_z": f.rolling_z, "anomaly": f.anomaly,
            "slope": f.slope, "triggers": f.triggers or [], "severity": f.severity, "rul": f.rul,
            "baseline_id": f.baseline_id}
