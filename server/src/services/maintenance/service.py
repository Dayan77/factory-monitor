"""
Maintenance module — plans, work orders and their side effects (domain.md §5, S4-1 … S4-7).

Everything filters tenant_id. Functions here never commit unless their name says so; the routes
commit and then call `notifications.service.deliver_pending`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (ApiToken, Component, FailureRecord, Line, Machine, MaintenancePlan, MaintenanceSettings,
                       Position, User, WorkOrder, WorkOrderHistory, WorkOrderLabor, WorkOrderReplacement)
from ...notifications import service as notif
from .. import clock, webhooks
from . import core
from . import usage as mu

log = structlog.get_logger()

CLOCK_SKEW = timedelta(minutes=5)
PLANNERS = ("maintenance_planner", "manager")
SUPERVISORS = ("production_supervisor",)
TECHNICIANS = ("technician",)
ADMINS = ("admin",)


def wo_label(number: int) -> str:
    return f"OS-{number:03d}"


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _422(msg, **extra) -> HTTPException:
    detail = {"message": msg, **extra} if extra else msg
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


async def settings_for(db: AsyncSession, tenant_id: str) -> MaintenanceSettings:
    s = await db.get(MaintenanceSettings, tenant_id)
    if s is None:
        # Sprint 7 (scale harness): concurrent first reads (the plan engine + GET /maintenance-plans/status) raced on
        # the INSERT of the defaults → UniqueViolation → 500. Insert-if-absent, then read.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        await db.execute(pg_insert(MaintenanceSettings).values(
            tenant_id=tenant_id, schedule_tolerance_days=0, technician_hours_per_week=44.0, due_soon_at=0.8,
            emergency_call_after_s=0, default_mttr_min=60.0, digest_hour_local=7)
            .on_conflict_do_nothing(index_elements=["tenant_id"]))
        s = await db.get(MaintenanceSettings, tenant_id)
    return s


async def machine_line(db: AsyncSession, tenant_id: str, machine_id: str) -> Optional[str]:
    return (await db.execute(select(Machine.line_id).where(Machine.id == machine_id,
                                                           Machine.tenant_id == tenant_id))).scalar_one_or_none()


# ── plans ─────────────────────────────────────────────────────────────────────

def plan_intervals(p: MaintenancePlan) -> dict:
    return {"cycles": p.interval_cycles, "hours": p.interval_hours, "starts": p.interval_starts,
            "days": p.interval_days}


@dataclass
class PlanEval:
    plan: MaintenancePlan
    status: Optional[core.PlanStatus]
    tz: ZoneInfo
    today: date


async def evaluate_plans(db: AsyncSession, tenant_id: str, plans: list[MaintenancePlan],
                         now: Optional[datetime] = None) -> list[PlanEval]:
    now = now or clock.now()
    cfg = await settings_for(db, tenant_id)
    pos_ids = [p.position_id for p in plans if p.position_id]
    mach_ids = [p.machine_id for p in plans if not p.position_id]
    pos_now = await mu.totals(db, tenant_id, "position", pos_ids)
    pos_win = await mu.rate_window(db, tenant_id, "position", pos_ids, now)
    m_now = await mu.totals(db, tenant_id, "machine", mach_ids)
    m_win = await mu.rate_window(db, tenant_id, "machine", mach_ids, now)
    pos_days = await mu.data_days(db, tenant_id, "position", pos_ids, now)
    m_days = await mu.data_days(db, tenant_id, "machine", mach_ids, now)
    tzs = await mu.machine_tz(db, tenant_id, [p.machine_id for p in plans])
    out = []
    for p in plans:
        tz = tzs.get(p.machine_id) or await mu.tenant_tz(db, tenant_id)
        today = now.astimezone(tz).date()
        if p.position_id:
            u_now, u_win = pos_now.get(p.position_id, {}), pos_win.get(p.position_id, {})
            days = pos_days.get(p.position_id, 0.0)
        else:
            u_now, u_win = m_now.get(p.machine_id, {}), m_win.get(p.machine_id, {})
            days = m_days.get(p.machine_id, 0.0)
        try:
            # audit A-3: the pace divides by the days with data (min 14), not always by 14
            st = core.compute_plan_status(plan_intervals(p), p.last_reset_at, p.last_reset_usage or {}, u_now, u_win,
                                          today, tz, tolerance=(p.tolerance_pct or 0) / 100.0,
                                          due_soon_at=cfg.due_soon_at, window_days=days)
        except Exception:  # noqa: BLE001 — one bad plan must not break the listing or the engine
            log.exception("maintenance.plan.evaluate_error", tenant_id=tenant_id, plan_id=p.id)
            st = None
        if p.condition_trigger:            # Sprint 5 (predictive.md P-9): condition plan reached → at least due
            from ..predictive.service import apply_condition_due
            st = apply_condition_due(p, st, today)
        out.append(PlanEval(p, st, tz, today))
    return out


async def open_plan_work_orders(db: AsyncSession, tenant_id: str, plan_ids: Iterable[str]) -> dict[str, WorkOrder]:
    ids = list(plan_ids)
    if not ids:
        return {}
    rows = (await db.execute(select(WorkOrder).where(WorkOrder.tenant_id == tenant_id, WorkOrder.plan_id.in_(ids),
                                                     WorkOrder.state.in_(core.OPEN_STATES)))).scalars().all()
    return {w.plan_id: w for w in rows}


async def next_number(db: AsyncSession, tenant_id: str) -> int:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"wo-number:{tenant_id}"})
    cur = (await db.execute(select(func.max(WorkOrder.number)).where(WorkOrder.tenant_id == tenant_id))).scalar_one()
    return (cur or 0) + 1


async def add_history(db: AsyncSession, wo: WorkOrder, at: datetime, by, from_state: Optional[str],
                      to_state: str, note: Optional[str] = None) -> None:
    """`by`: user id, None (system) or the acting User — an API-token principal is recorded with its token id."""
    token_id = None
    if isinstance(by, User):
        token_id = getattr(by, "api_token_id", None)
        by = by.id
    db.add(WorkOrderHistory(tenant_id=wo.tenant_id, work_order_id=wo.id, at=at, by=by, from_state=from_state,
                            to_state=to_state, note=note, api_token_id=token_id))
    wo.state_changed_at = at


async def create_plan_draft(db: AsyncSession, plan: MaintenancePlan, at: datetime, by: Optional[str] = None,
                            note: str = "gerada pelo plano (≥ 80 %)") -> Optional[WorkOrder]:
    """S4-4: one draft per plan at ≥ 80 %. Returns None if the plan already has an open WO."""
    if (await open_plan_work_orders(db, plan.tenant_id, [plan.id])):
        return None
    number = await next_number(db, plan.tenant_id)
    wo = WorkOrder(tenant_id=plan.tenant_id, number=number, type=plan.wo_type, urgency=None, state="draft",
                   plan_id=plan.id, position_id=plan.position_id, machine_id=plan.machine_id, title=plan.name,
                   description=plan.description,
                   task_list=[{"id": t.get("id"), "text": t.get("text"), "done": False} for t in (plan.task_list or [])],
                   estimated_hours=plan.estimated_hours or 0.0, stops_machine=plan.stops_machine, created_by=by,
                   created_at=at)
    try:
        async with db.begin_nested():
            db.add(wo)
            await db.flush()
    except IntegrityError:
        return None          # a concurrent recompute created it (unique open-WO-per-plan index)
    await add_history(db, wo, at, by, None, "draft", note)
    await webhooks.enqueue(db, plan.tenant_id, "work_order.created", await wo_out(db, wo))
    return wo


async def run_plan_engine(db: AsyncSession, tenant_id: str, plan_ids: Optional[list[str]] = None,
                          at: Optional[datetime] = None, now: Optional[datetime] = None) -> list[PlanEval]:
    """Recompute plan states; create the single draft of a plan at ≥ 80 % (S4-4); notify state changes."""
    now = now or clock.now()
    q = select(MaintenancePlan).where(MaintenancePlan.tenant_id == tenant_id, MaintenancePlan.is_active.is_(True))
    if plan_ids is not None:
        q = q.where(MaintenancePlan.id.in_(plan_ids))
    plans = list((await db.execute(q.with_for_update())).scalars().all())
    if not plans:
        return []
    cfg = await settings_for(db, tenant_id)
    evals = await evaluate_plans(db, tenant_id, plans, now)
    open_wos = await open_plan_work_orders(db, tenant_id, [p.id for p in plans])
    for ev in evals:
        if ev.status is None:
            continue
        try:
            async with db.begin_nested():
                await _apply_plan_eval(db, tenant_id, cfg, ev, open_wos, at or now, now)
        except Exception:  # noqa: BLE001 — errors are isolated per plan
            log.exception("maintenance.plan.engine_error", tenant_id=tenant_id, plan_id=ev.plan.id)
    await db.flush()
    return evals


async def _apply_plan_eval(db: AsyncSession, tenant_id: str, cfg, ev: PlanEval, open_wos: dict,
                           at: datetime, now: datetime) -> None:
    p, st = ev.plan, ev.status
    old_state = p.state
    p.state, p.consumed, p.evaluated_at = st.state, st.consumed, now
    line_id = await machine_line(db, tenant_id, p.machine_id)
    created = None
    if st.consumed >= cfg.due_soon_at and p.id not in open_wos:
        created = await create_plan_draft(db, p, at)
        if created is not None:
            open_wos[p.id] = created
            await notify_draft(db, p, st, created, line_id)
    if old_state != st.state and st.state in ("due", "overdue"):
        users = await notif.users_with_roles(db, tenant_id, PLANNERS, line_id)
        kind = "plan_due" if st.state == "due" else "plan_overdue"
        await notif.notify(db, tenant_id, users, kind,
                           f"{p.name}: {'vencido' if st.state == 'due' else 'além da tolerância'} "
                           f"({st.consumed * 100:.0f} %)",
                           body=f"Gatilho {st.consumed_trigger}.", link="/maintenance/upcoming",
                           data={"plan_id": p.id, "state": st.state, "consumed": st.consumed}, line_id=line_id)
        await webhooks.enqueue(db, tenant_id, "plan.due", {"plan_id": p.id, "name": p.name, "state": st.state,
                                                            "consumed": st.consumed,
                                                            "open_work_order_id": getattr(open_wos.get(p.id), "id", None)})


async def notify_draft(db: AsyncSession, p: MaintenancePlan, st: core.PlanStatus, created: WorkOrder,
                       line_id: Optional[str]) -> None:
    planners = await notif.users_with_roles(db, p.tenant_id, ("maintenance_planner",), line_id)
    await notif.notify(db, p.tenant_id, planners, "plan_due_soon",
                       f"{p.name}: {st.consumed * 100:.0f} % — rascunho {wo_label(created.number)} criado",
                       body=f"Gatilho {st.consumed_trigger}; vence em {st.next_due_date or '—'}.",
                       link=f"/maintenance/work-orders/{created.id}",
                       data={"plan_id": p.id, "work_order_id": created.id, "consumed": st.consumed},
                       group_key="plan_due_soon", line_id=line_id,
                       grouped_title="{count} planos cruzaram 80 % (rascunhos criados)")


def status_row(ev: PlanEval, names: dict, open_wo: Optional[WorkOrder]) -> dict:
    p, st = ev.plan, ev.status
    trig = []
    if st is not None:
        for t in st.triggers:
            trig.append({"measure": t.measure, "interval": t.interval, "usage": t.usage, "consumed": t.consumed,
                         "days": t.days, "projected_date": t.projected_date.isoformat() if t.projected_date else None,
                         "next_due_usage": t.next_due_usage, "rate_per_day": t.rate_per_day})
    nd = st.next_due_usage if st else {}
    pick = None
    if st is not None:
        for m in (st.due_trigger, st.consumed_trigger, "cycles", "hours", "starts"):
            if m in nd:
                pick = m
                break
    return {
        "plan_id": p.id, "plan_name": p.name, "position_id": p.position_id,
        "position_name": names.get(p.position_id), "machine_id": p.machine_id, "machine_name": names.get(p.machine_id),
        "line_id": names.get(("line", p.machine_id)), "line_name": names.get(("line_name", p.machine_id)),
        "wo_type": p.wo_type, "estimated_hours": p.estimated_hours,
        "state": st.state if st else "ok", "consumed": st.consumed if st else None,
        "determining_trigger": st.consumed_trigger if st else None,
        "consumed_trigger": st.consumed_trigger if st else None,
        "due_trigger": st.due_trigger if st else None,
        "triggers": trig,
        "projected_date": st.next_due_date.isoformat() if st and st.next_due_date else None,
        "next_due_date": st.next_due_date.isoformat() if st and st.next_due_date else None,
        "days_to_due": st.days_to_due if st else None,
        "calendar_due_date": st.calendar_due_date.isoformat() if st and st.calendar_due_date else None,
        "past_due": bool(st.past_due) if st else False,
        "low_confidence": bool(st.low_confidence) if st else False,
        "reason": st.reason if st else "no_triggers",
        "since_maintenance": st.since_maintenance if st else {},
        "consumed_by_trigger": st.consumed_by_trigger if st else {},
        "days_by_trigger": st.days_by_trigger if st else {},
        "next_due_usage": ({"measure": pick, "value": nd[pick]} if pick else None),
        "next_due_usage_by_measure": nd,
        "last_reset_at": clock.iso(p.last_reset_at),
        "last_reset_by": p.last_reset_by,
        "open_work_order_id": open_wo.id if open_wo else None,
        "open_work_order_number": wo_label(open_wo.number) if open_wo else None,
        "open_work_order_state": open_wo.state if open_wo else None,
        "today": ev.today.isoformat(),
    }


async def asset_names(db: AsyncSession, tenant_id: str, machine_ids: Iterable[str], position_ids: Iterable[str],
                      user_ids: Iterable[str] = (), plan_ids: Iterable[str] = ()) -> dict:
    out: dict = {}
    uids, plids = list(set(filter(None, user_ids))), list(set(filter(None, plan_ids)))
    if uids:
        for uid, name in (await db.execute(select(User.id, User.name).where(User.tenant_id == tenant_id,
                                                                           User.id.in_(uids)))).all():
            out[("user", uid)] = name
    if plids:
        for pid, name in (await db.execute(select(MaintenancePlan.id, MaintenancePlan.name).where(
                MaintenancePlan.tenant_id == tenant_id, MaintenancePlan.id.in_(plids)))).all():
            out[("plan", pid)] = name
    mids, pids = list(set(filter(None, machine_ids))), list(set(filter(None, position_ids)))
    if mids:
        for mid, name, lid, lname in (await db.execute(
                select(Machine.id, Machine.name, Line.id, Line.name).join(Line, Line.id == Machine.line_id)
                .where(Machine.tenant_id == tenant_id, Machine.id.in_(mids)))).all():
            out[mid] = name
            out[("line", mid)] = lid
            out[("line_name", mid)] = lname
    if pids:
        for pid, name in (await db.execute(select(Position.id, Position.name).where(
                Position.tenant_id == tenant_id, Position.id.in_(pids)))).all():
            out[pid] = name
    return out


async def reset_plan(db: AsyncSession, plan: MaintenancePlan, at: datetime, by: str, now: datetime) -> bool:
    """§5.4/S4-2: the reference becomes `at` and the anchor's usage_total at that instant.
    A reset older than the current reference is ignored (history replayed out of order)."""
    if plan.last_reset_at is not None and at <= plan.last_reset_at:
        return False
    scope, eid = ("position", plan.position_id) if plan.position_id else ("machine", plan.machine_id)
    plan.last_reset_usage = await mu.usage_at(db, plan.tenant_id, scope, eid, at, now)
    plan.last_reset_at = at
    plan.last_reset_by = by
    plan.condition_due_date = None          # predictive.md P-9: completing the WO zeroes the condition trigger too
    plan.condition_alert_id = None
    return True


# ── work orders ───────────────────────────────────────────────────────────────

async def wo_out(db: AsyncSession, wo: WorkOrder, user: Optional[User] = None, full: bool = True,
                 names: Optional[dict] = None, today: Optional[date] = None) -> dict:
    hist, labor, reps = [], [], []
    if full:
        rows = (await db.execute(select(WorkOrderHistory, User.name, ApiToken.name)
                                 .outerjoin(User, User.id == WorkOrderHistory.by)
                                 .outerjoin(ApiToken, ApiToken.id == WorkOrderHistory.api_token_id)
                                 .where(WorkOrderHistory.work_order_id == wo.id, WorkOrderHistory.tenant_id == wo.tenant_id)
                                 .order_by(WorkOrderHistory.at, WorkOrderHistory.recorded_at))).all()
        hist = [{"at": clock.iso(h.at), "by": h.by,
                 "by_name": f"API: {tn}" if h.api_token_id else n, "api_token_id": h.api_token_id,
                 "from_state": h.from_state, "to_state": h.to_state, "note": h.note} for h, n, tn in rows]
        labor = [{"id": l.id, "user_id": l.user_id, "hours": l.hours, "remaining_hours": l.remaining_hours,
                  "note": l.note, "at": clock.iso(l.at)}
                 for l in (await db.execute(select(WorkOrderLabor).where(
                     WorkOrderLabor.work_order_id == wo.id, WorkOrderLabor.tenant_id == wo.tenant_id)
                     .order_by(WorkOrderLabor.at))).scalars()]
        reps = [{"id": r.id, "position_id": r.position_id, "removed_component_id": r.removed_component_id,
                 "installed_component_id": r.installed_component_id, "removal_reason": r.removal_reason,
                 "applied_at": clock.iso(r.applied_at)}
                for r in (await db.execute(select(WorkOrderReplacement).where(
                    WorkOrderReplacement.work_order_id == wo.id, WorkOrderReplacement.tenant_id == wo.tenant_id)
                    .order_by(WorkOrderReplacement.created_at))).scalars()]
    if names is None:
        names = await asset_names(db, wo.tenant_id, [wo.machine_id], [wo.position_id], [wo.assignee_id], [wo.plan_id])
    if today is None:
        today = clock.now().astimezone((await mu.machine_tz(db, wo.tenant_id, [wo.machine_id])).get(wo.machine_id)
                                       or await mu.tenant_tz(db, wo.tenant_id)).date()
    iso = clock.iso
    allowed = core.allowed_targets(wo.state, wo.type, wo.urgency)
    out = {
        "id": wo.id, "number": wo_label(wo.number), "seq": wo.number, "type": wo.type, "urgency": wo.urgency,
        "state": wo.state, "plan_id": wo.plan_id, "plan_name": names.get(("plan", wo.plan_id)),
        "position_id": wo.position_id,
        "position_name": names.get(wo.position_id), "machine_id": wo.machine_id, "machine_name": names.get(wo.machine_id),
        "line_id": names.get(("line", wo.machine_id)), "line_name": names.get(("line_name", wo.machine_id)),
        "downtime_event_id": wo.downtime_event_id, "failure_id": wo.failure_id, "parent_id": wo.parent_id,
        "title": wo.title, "description": wo.description,
        "task_list": [{"id": t.get("id"), "text": t.get("text"), "done": bool(t.get("done")),
                       "result": t.get("result"), "note": t.get("note")} for t in (wo.task_list or [])],
        "estimated_hours": wo.estimated_hours, "labor_hours": wo.labor_hours,
        "remaining_hours": core.remaining_hours(wo.estimated_hours, wo.labor_hours, wo.remaining_hours),
        "remaining_hours_informed": wo.remaining_hours,
        "stops_machine": wo.stops_machine,
        "scheduled_for": wo.scheduled_for.isoformat() if wo.scheduled_for else None,
        "scheduled_at": iso(wo.scheduled_at), "assignee_id": wo.assignee_id,
        "assignee_name": names.get(("user", wo.assignee_id)), "created_by": wo.created_by,
        "created_at": iso(wo.created_at), "requested_at": iso(wo.requested_at), "approved_at": iso(wo.approved_at),
        "started_at": iso(wo.started_at), "completed_at": iso(wo.completed_at), "closed_at": iso(wo.closed_at),
        "cancelled_at": iso(wo.cancelled_at), "cancel_reason": wo.cancel_reason, "hold_reason": wo.hold_reason,
        "overdue": bool(wo.scheduled_for and wo.scheduled_for < today and wo.state in core.BEFORE_COMPLETED),
        "replacements": reps, "history": hist, "labor": labor,
        "source_alert_id": wo.source_alert_id, "finding": wo.finding, "resets_baseline": wo.resets_baseline,
        "due_by": wo.due_by.isoformat() if wo.due_by else None,
        "allowed_transitions": allowed,
        "user_allowed_transitions": [t for t in allowed if user is None or can_transition(user, wo, t)],
    }
    if user is not None and is_operator_only(user):
        for k in ("estimated_hours", "labor_hours", "remaining_hours", "remaining_hours_informed"):
            out[k] = None
        out["labor"] = []
    return out


def is_operator_only(user: User) -> bool:
    return user.role_set <= {"operator"}


def _in_team(user: User, wo_line: Optional[str]) -> bool:
    return not user.line_ids or (wo_line is not None and str(wo_line) in {str(x) for x in user.line_ids})


def can_transition(user: User, wo: WorkOrder, to: str, wo_line: Optional[str] = None) -> bool:
    """maintenance_workflows.md §1 (state validity is checked separately)."""
    rs = user.role_set
    if rs & {"admin", "superadmin", "manager"}:
        return True
    frm = wo.state
    planner = "maintenance_planner" in rs
    tech = "technician" in rs
    sup = "production_supervisor" in rs
    if planner and ((frm, to) in {("draft", "open"), ("open", "approved"), ("approved", "scheduled"),
                                  ("on_hold", "scheduled"), ("on_hold", "in_progress")} or to == "cancelled"):
        return True
    if tech and (frm, to) in {("scheduled", "in_progress"), ("in_progress", "on_hold"), ("on_hold", "in_progress"),
                              ("in_progress", "completed"), ("open", "in_progress")}:
        mine = wo.assignee_id is None or wo.assignee_id == user.id
        return mine or (wo_line is not None and _in_team(user, wo_line) and bool(user.line_ids))
    if sup and (frm, to) == ("completed", "closed"):
        return wo_line is None or _in_team(user, wo_line)
    return False


def can_execute(user: User, wo: WorkOrder) -> bool:
    """Technician's own WO (assignee, or unassigned), or admin/manager (labor, tasks, replacements)."""
    rs = user.role_set
    if rs & {"admin", "superadmin", "manager"}:
        return True
    return "technician" in rs and (wo.assignee_id is None or wo.assignee_id == user.id)


def check_at(at: Optional[datetime], wo: WorkOrder, now: datetime) -> datetime:
    at = _aware(at) or now
    if at > now + CLOCK_SKEW:
        raise _422("`at` não pode estar no futuro.")
    ref = wo.state_changed_at or wo.created_at
    if ref is not None and at < ref:
        raise _422("`at` anterior à última mudança de estado da OS.")
    return at


async def add_labor(db: AsyncSession, wo: WorkOrder, user: User, hours: float, note: Optional[str],
                    remaining: Optional[float], at: datetime, client_action_id: Optional[str] = None) -> WorkOrderLabor:
    if wo.state not in ("in_progress", "on_hold", "completed"):
        raise _422("Apontamento só com a OS em execução, em espera ou concluída.")
    if hours < 0 or hours > 24:
        raise _422("hours deve estar entre 0 e 24.")
    if remaining is not None and remaining < 0:
        raise _422("remaining_hours não pode ser negativo.")
    if client_action_id:
        dup = (await db.execute(select(WorkOrderLabor).where(
            WorkOrderLabor.work_order_id == wo.id, WorkOrderLabor.client_action_id == client_action_id))).scalar_one_or_none()
        if dup is not None:
            return dup                     # offline replay of an action already applied
    entry = WorkOrderLabor(tenant_id=wo.tenant_id, work_order_id=wo.id, user_id=user.id, hours=hours,
                           remaining_hours=remaining, note=note, at=at, client_action_id=client_action_id)
    db.add(entry)
    wo.labor_hours = float(wo.labor_hours or 0.0) + hours
    if remaining is not None:
        wo.remaining_hours = remaining
    return entry


async def _apply_replacements(db: AsyncSession, wo: WorkOrder, at: datetime, now: datetime) -> list[str]:
    """§5.4 / S4-2 at completion. Returns the ids of the plans reset."""
    reps = (await db.execute(select(WorkOrderReplacement).where(
        WorkOrderReplacement.work_order_id == wo.id, WorkOrderReplacement.tenant_id == wo.tenant_id,
        WorkOrderReplacement.applied_at.is_(None)).order_by(WorkOrderReplacement.created_at))).scalars().all()
    reset: list[str] = []
    for r in reps:
        # lock the incoming component first: nothing is removed unless it can really be installed here
        new = (await db.execute(select(Component).where(
            Component.id == r.installed_component_id, Component.tenant_id == wo.tenant_id)
            .with_for_update())).scalar_one_or_none()
        if new is None:
            raise _422("Componente novo da troca não existe mais.")
        if new.removed_at is not None or new.position_id not in (None, r.position_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "message": "O componente novo desta troca já foi instalado em outra posição (outra OS). "
                           "Remova a troca e registre outro componente.",
                "replacement_id": r.id, "component_id": new.id})
        old = (await db.execute(select(Component).where(
            Component.tenant_id == wo.tenant_id, Component.position_id == r.position_id,
            Component.removed_at.is_(None)).with_for_update())).scalar_one_or_none()
        if old is not None and old.id != new.id:
            if old.installed_at is not None and at < old.installed_at:
                raise _422("Conclusão anterior à instalação do componente removido.")
            old.position_id = None
            old.removed_at = at
            old.removal_reason = r.removal_reason
            r.removed_component_id = old.id
            await db.flush()
        if new.position_id is None:
            new.position_id = r.position_id
            new.last_position_id = r.position_id
            new.installed_at = at
        r.applied_at = at
        await db.flush()
        plans = (await db.execute(select(MaintenancePlan).where(
            MaintenancePlan.tenant_id == wo.tenant_id, MaintenancePlan.position_id == r.position_id,
            MaintenancePlan.reset_on_replacement.is_(True), MaintenancePlan.is_active.is_(True)))).scalars().all()
        for p in plans:
            if await reset_plan(db, p, at, f"{wo_label(wo.number)} (troca de componente)", now):
                reset.append(p.id)
    return reset          # topology cache is cleared on commit (usage/cache.py session events)


async def transition(db: AsyncSession, wo: WorkOrder, to: str, user: User, *, note: Optional[str] = None,
                     at: Optional[datetime] = None, scheduled_for: Optional[date] = None,
                     assignee_id: Optional[str] = None, hold_reason: Optional[str] = None,
                     cancel_reason: Optional[str] = None, labor_hours: Optional[float] = None,
                     remaining_hours: Optional[float] = None, check_permission: bool = True,
                     finding: Optional[str] = None, resets_baseline: Optional[bool] = None) -> dict:
    """Validated state change with audit trail and side effects. Returns {'plans_reset': [...],
    'new_draft_id': ...}. Raises 422 (invalid transition, with `allowed`) or 403."""
    now = clock.now()
    err = core.transition_error(wo.state, to, wo.type, wo.urgency, cancel_reason)
    if err:
        raise _422(err, allowed=core.allowed_targets(wo.state, wo.type, wo.urgency), state=wo.state, to=to)
    line_id = await machine_line(db, wo.tenant_id, wo.machine_id)
    if check_permission and not can_transition(user, wo, to, line_id):
        raise HTTPException(status_code=403, detail="Seu papel não permite esta transição.")
    at = check_at(at, wo, now)
    from ..predictive import service as predictive   # P-9: `finding` of a predictive WO born from an alert
    await predictive.validate_wo_transition(db, wo, to, finding)
    frm = wo.state
    result: dict = {"plans_reset": [], "new_draft_id": None}
    assignee_changed = False
    if assignee_id is not None and assignee_id != wo.assignee_id:
        await _check_assignee(db, wo.tenant_id, assignee_id)
        wo.assignee_id = assignee_id
        assignee_changed = True
    if to == "open":
        wo.requested_at = wo.requested_at or at
    elif to == "approved":
        wo.approved_at = at
    elif to == "scheduled":
        sf = scheduled_for or wo.scheduled_for
        if sf is None:
            raise _422("Programar exige scheduled_for.")
        rescheduled = wo.scheduled_for is not None and sf != wo.scheduled_for
        wo.scheduled_for = sf
        if wo.scheduled_at is None:
            wo.scheduled_at = at                       # S4-5: freeze base, not moved by rescheduling
        if frm == "on_hold" or rescheduled:
            result["rescheduled"] = True
    elif to == "in_progress":
        wo.started_at = wo.started_at or at
        if wo.assignee_id is None and "technician" in user.role_set:
            wo.assignee_id = user.id
        wo.hold_reason = None if frm == "on_hold" else wo.hold_reason
        if wo.failure_id:
            fr = await db.get(FailureRecord, wo.failure_id)
            if fr is not None and fr.tenant_id == wo.tenant_id and fr.repair_started_at is None:
                fr.repair_started_at = at
    elif to == "on_hold":
        if not (hold_reason or wo.hold_reason):
            raise _422("Pausar exige hold_reason (aguardando peça / liberação / outro).")
        wo.hold_reason = hold_reason or wo.hold_reason
    elif to == "completed":
        wo.completed_at = at
    elif to == "closed":
        wo.closed_at = at
    elif to == "cancelled":
        wo.cancelled_at = at
        wo.cancel_reason = cancel_reason
    if labor_hours:
        if to not in ("on_hold", "completed"):
            raise _422("labor_hours na transição só ao pausar ou concluir (use POST /labor).")
        await add_labor(db, wo, user, labor_hours, note, remaining_hours, at)     # still in_progress here
    elif remaining_hours is not None:
        if remaining_hours < 0:
            raise _422("remaining_hours não pode ser negativo.")
        wo.remaining_hours = remaining_hours
    wo.state = to
    await add_history(db, wo, at, user, frm, to, note)
    await db.flush()

    if to == "completed":
        result["plans_reset"] = await _apply_replacements(db, wo, at, now)
        if wo.plan_id:
            plan = await db.get(MaintenancePlan, wo.plan_id)
            if plan is not None and plan.tenant_id == wo.tenant_id and \
                    await reset_plan(db, plan, at, f"{wo_label(wo.number)} concluída", now):
                result["plans_reset"].append(plan.id)
        if wo.failure_id:
            fr = await db.get(FailureRecord, wo.failure_id)
            if fr is not None and fr.tenant_id == wo.tenant_id and fr.repair_finished_at is None:
                fr.repair_finished_at = at
        await db.flush()
        if result["plans_reset"]:
            await run_plan_engine(db, wo.tenant_id, plan_ids=list(set(result["plans_reset"])))
        sups = await notif.users_with_roles(db, wo.tenant_id, SUPERVISORS, line_id)
        await notif.notify(db, wo.tenant_id, sups, "wo_awaiting_close",
                           f"{wo_label(wo.number)} concluída — aguardando fechamento", body=wo.title,
                           link=f"/maintenance/work-orders/{wo.id}", data={"work_order_id": wo.id}, line_id=line_id)
    if to == "cancelled" and wo.plan_id:
        # S4-4: the plan still ≥ 80 % gets a new draft at once (never two, never zero)
        await db.flush()
        plan = await db.get(MaintenancePlan, wo.plan_id)
        if plan is not None and plan.is_active:
            ev = (await evaluate_plans(db, wo.tenant_id, [plan], now))[0]
            cfg = await settings_for(db, wo.tenant_id)
            if ev.status is not None and ev.status.consumed >= cfg.due_soon_at:
                d = await create_plan_draft(db, plan, at, by=user.id,
                                            note=f"recriada após o cancelamento de {wo_label(wo.number)}")
                result["new_draft_id"] = d.id if d else None
                if d is not None:
                    await notify_draft(db, plan, ev.status, d, line_id)
    if to == "on_hold":
        planners = await notif.users_with_roles(db, wo.tenant_id, ("maintenance_planner",), line_id)
        await notif.notify(db, wo.tenant_id, planners, "wo_on_hold", f"{wo_label(wo.number)} em espera",
                           body=wo.hold_reason, link=f"/maintenance/work-orders/{wo.id}",
                           data={"work_order_id": wo.id}, line_id=line_id)
    if to == "scheduled" and wo.stops_machine and frm == "approved":
        sups = await notif.users_with_roles(db, wo.tenant_id, SUPERVISORS, line_id)
        await notif.notify(db, wo.tenant_id, sups, "window_request",
                           f"Pedido de janela de parada: {wo_label(wo.number)} em {wo.scheduled_for:%d/%m}",
                           body=wo.title, link=f"/maintenance/work-orders/{wo.id}",
                           data={"work_order_id": wo.id, "scheduled_for": wo.scheduled_for.isoformat()}, line_id=line_id)
    await _notify_assignee(db, wo, assignee_changed, bool(result.get("rescheduled")))
    # P-2/P-8: baseline resets, alert closed/back to acknowledged by the WO outcome
    await predictive.on_wo_transition(db, wo, frm, to, at, finding=finding, resets_baseline=resets_baseline)
    data = await wo_out(db, wo, full=False)
    await webhooks.enqueue(db, wo.tenant_id, "work_order.state_changed", {**data, "from_state": frm, "to_state": to})
    await webhooks.enqueue(db, wo.tenant_id, f"work_order.{to}", data)
    return result


async def _notify_assignee(db: AsyncSession, wo: WorkOrder, assigned: bool, rescheduled: bool) -> None:
    if not wo.assignee_id or not (assigned or rescheduled):
        return
    u = await db.get(User, wo.assignee_id)
    if u is None or u.tenant_id != wo.tenant_id:
        return
    when = f" para {wo.scheduled_for:%d/%m}" if wo.scheduled_for else ""
    if assigned:
        await notif.notify(db, wo.tenant_id, [u], "wo_assigned", f"{wo_label(wo.number)} atribuída a você{when}",
                           body=wo.title, link=f"/m/work-orders/{wo.id}", data={"work_order_id": wo.id})
    else:
        await notif.notify(db, wo.tenant_id, [u], "wo_rescheduled", f"{wo_label(wo.number)} reprogramada{when}",
                           body=wo.title, link=f"/m/work-orders/{wo.id}", data={"work_order_id": wo.id})


async def _check_assignee(db: AsyncSession, tenant_id: str, user_id: str) -> User:
    from ..validation import is_uuid
    u = await db.get(User, user_id) if is_uuid(user_id) else None
    if u is None or u.tenant_id != tenant_id or not u.is_active:
        raise HTTPException(status_code=404, detail="assignee_id não encontrado(a).")
    return u


async def engine_emergency_work_order(db: AsyncSession, f: FailureRecord, title: str, at: datetime) -> WorkOrder:
    """Automatic emergency call (engine): the open emergency corrective WO of the stop, created if missing, so the
    on-call technician can "Assumir" it on the phone (workflows §3). Reuses an open WO of the same stop/failure."""
    q = select(WorkOrder).where(WorkOrder.tenant_id == f.tenant_id, WorkOrder.state.in_(core.OPEN_STATES))
    conds = []
    if f.work_order_id:
        conds.append(WorkOrder.id == f.work_order_id)
    if f.downtime_event_id:
        conds.append(WorkOrder.downtime_event_id == f.downtime_event_id)
    if conds:
        existing = (await db.execute(q.where(or_(*conds)))).scalars().first()
        if existing is not None:
            return existing
    number = await next_number(db, f.tenant_id)
    wo = WorkOrder(tenant_id=f.tenant_id, number=number, type="corrective", urgency="emergency", state="open",
                   position_id=f.position_id, machine_id=f.machine_id, downtime_event_id=f.downtime_event_id,
                   failure_id=f.id, title=title, description="Aberta automaticamente: parada por falha de equipamento (EQP).",
                   task_list=[], estimated_hours=0.0, stops_machine=False, created_by=None, created_at=at,
                   requested_at=at)
    db.add(wo)
    await db.flush()
    await add_history(db, wo, at, None, None, "open", "chamado emergencial automático")
    if f.work_order_id is None:
        f.work_order_id = wo.id
    await webhooks.enqueue(db, f.tenant_id, "work_order.created", await wo_out(db, wo, full=False))
    return wo


@dataclass(frozen=True)
class EmergencyCallResult:
    """Who an emergency call reached. `fallback` = no on-call technician covers the line, so the planners /
    managers and admins of the area were called instead; `escalated` counts only those planners / managers /
    admins actually reached (the line supervisors are counted in `notified` but are not maintenance people).
    `notified == 0` means nobody was reached; `on_call_technicians == 0 and escalated == 0` means no maintenance
    person was reached (at most the production supervisor). Both must be told to the caller (CLAUDE.md: never
    report a success that did not happen)."""
    notified: int
    on_call_technicians: int
    fallback: bool
    escalated: int = 0

    @property
    def maintenance_reached(self) -> bool:
        return self.on_call_technicians > 0 or self.escalated > 0

    @property
    def warning(self) -> Optional[str]:
        if self.notified == 0:
            return "Ninguém foi notificado: não há técnico de plantão nem responsável cadastrado para esta linha. Avise por rádio/telefone."
        if not self.maintenance_reached:
            return ("Nenhuma pessoa da manutenção foi notificada: não há técnico de plantão nesta linha nem "
                    "planejamento/gestão para acionar. Só a supervisão da linha recebeu o chamado. Avise a manutenção "
                    "por rádio/telefone.")
        if self.on_call_technicians == 0:
            return "Não há técnico de plantão nesta linha: o chamado foi enviado ao planejamento/gestão para acionar alguém."
        return None

    def as_dict(self) -> dict:
        return {"notified_count": self.notified, "on_call_technicians": self.on_call_technicians,
                "fallback": self.fallback, "escalated_count": self.escalated, "warning": self.warning}


async def emergency_call(db: AsyncSession, tenant_id: str, machine_id: str, title: str, body: Optional[str],
                         link: str, data: dict, exclude_user: Optional[str] = None) -> EmergencyCallResult:
    """Push + sound to the on-call technicians of the machine's line (technicians without a line restriction
    cover every line) and its supervisors (cannot be muted). When no on-call technician covers the line, the call
    escalates to the planners / managers of the area and the tenant admins, so a stopped machine is never left
    with nobody called. The result counts technicians and escalation recipients separately from supervisors."""
    line_id = await machine_line(db, tenant_id, machine_id)
    techs = [u for u in await notif.users_with_roles(db, tenant_id, TECHNICIANS, line_id, on_call=True)
             if u.id != exclude_user]
    sups = await notif.users_with_roles(db, tenant_id, SUPERVISORS, line_id)
    users = techs + sups
    fallback = not techs
    escalation = []
    if fallback:
        escalation = [u for u in await notif.users_with_roles(db, tenant_id, PLANNERS + ADMINS, line_id)
                      if u.id != exclude_user]
        users += escalation
    users = [u for u in users if u.id != exclude_user]
    ns = await notif.notify(db, tenant_id, users, "emergency_call", title, body=body, link=link,
                            data={**data, "sound": True, "no_on_call": fallback}, line_id=line_id)
    reached = {n.user_id for n in ns}
    res = EmergencyCallResult(notified=len(reached), on_call_technicians=len({u.id for u in techs} & reached),
                              fallback=fallback, escalated=len({u.id for u in escalation} & reached))
    if not res.maintenance_reached:
        log.error("emergency_call_no_maintenance_reached", tenant_id=tenant_id, machine_id=machine_id,
                  line_id=line_id, notified=res.notified)
    elif res.on_call_technicians == 0:
        log.warning("emergency_call_no_on_call", tenant_id=tenant_id, machine_id=machine_id, line_id=line_id,
                    notified=res.notified, escalated=res.escalated)
    return res
