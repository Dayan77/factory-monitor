"""
Maintenance API (Sprint 4 — domain.md §5, kpis.md §2–§3, maintenance_workflows.md §1).

Routers (mounted in main.py):
  plans_router     /api/maintenance-plans     CRUD + /status (whichever-comes-first) + /recompute
  wo_router        /api/work-orders           list/detail/create, /transition, /approve-draft, /labor,
                                              /tasks, /replacements, /emergency-call
  failures_router  /api/failures              ISO 14224 failure records
  codes_router     /api/failure-codes         ISO 14224 code lists
  mkpi_router      /api/maintenance           /kpis, /reliability, /components, /settings, /absences

Roles (a user may hold several; admin/superadmin always pass): see maintenance_workflows.md §1.
Every query filters tenant_id; ids of another tenant answer 404.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, has_any, require_any
from ...db import get_db
from ...models import (Component, ComponentType, DowntimeEvent, FailureRecord, Line, Machine, MaintenanceAbsence,
                       MaintenancePlan, Plant, Position, RemovalReason, User, WorkOrder, WorkOrderReplacement)
from ...notifications import service as notif
from ...services.response_cache import cached_endpoint
from ...services import clock, webhooks
from ...services.maintenance import core, kpis as mk, service as ms
from ...services.maintenance import usage as mu
from ...services.maintenance.stale import stale_open_ids
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas_maintenance import (AbsenceIn, EmergencyCallIn, FailureCreate, FailurePatch, LaborIn, PlanCreate,
                                   PlanOut, PlanUpdate, ReplacementIn, SettingsIn, TaskPatch, TransitionIn,
                                   WorkOrderCreate, WorkOrderUpdate)
from ..tenant_refs import ensure_owned

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]

PLANNER = ("maintenance_planner", "manager")
MAINT_READ = ("maintenance_planner", "manager", "technician", "production_supervisor")
KPI_READ = ("maintenance_planner", "manager", "production_supervisor")
FAILURE_EDIT = ("maintenance_planner", "manager", "technician", "production_supervisor")

plans_router = APIRouter()
wo_router = APIRouter()
failures_router = APIRouter()
codes_router = APIRouter()
mkpi_router = APIRouter()


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} não encontrado(a).")


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


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
    from ...services.predictive import service as predictive_service
    await db.commit()
    await predictive_service.deliver(db)      # predictive WebSocket messages (P-8) + notifications


# ══ plans ════════════════════════════════════════════════════════════════════

async def _plan_anchor(db: AsyncSession, tenant_id: str, position_id: Optional[str], machine_id: Optional[str]
                       ) -> tuple[Optional[str], str]:
    if position_id:
        position_id = await ensure_owned(db, Position, position_id, tenant_id, "position_id")
        pm = await mu.position_machine(db, tenant_id, position_id)
        if machine_id and machine_id != pm:
            raise HTTPException(status_code=422, detail="machine_id diferente da máquina da posição.")
        return position_id, pm
    machine_id = await ensure_owned(db, Machine, machine_id, tenant_id, "machine_id")
    return None, machine_id


def _tasks(items) -> list[dict]:
    return [{"id": t.id or uuid.uuid4().hex[:8], "text": t.text} for t in items]


async def _set_last_done(db: AsyncSession, plan: MaintenancePlan, at: Optional[datetime], usage) -> None:
    now = clock.now()
    at = _aware(at) or now
    if at > now + ms.CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="last_done_at não pode estar no futuro.")
    scope, eid = ("position", plan.position_id) if plan.position_id else ("machine", plan.machine_id)
    base = await mu.usage_at(db, plan.tenant_id, scope, eid, at, now)
    if usage is not None:
        for k, v in usage.model_dump(exclude_none=True).items():
            base[k] = float(v)
    plan.last_reset_at = at
    plan.last_reset_usage = base
    plan.last_reset_by = "cadastro"


@plans_router.get("/", response_model=list[PlanOut], dependencies=[Depends(require_any(*MAINT_READ))])
async def list_plans(user: CurrentUser, db: DB, position_id: Optional[str] = None, machine_id: Optional[str] = None,
                     is_active: Optional[bool] = None):
    q = select(MaintenancePlan).where(MaintenancePlan.tenant_id == user.tenant_id)
    if position_id:
        q = q.where(MaintenancePlan.position_id == (position_id if is_uuid(position_id) else None))
    if machine_id:
        q = q.where(MaintenancePlan.machine_id == (machine_id if is_uuid(machine_id) else None))
    if is_active is not None:
        q = q.where(MaintenancePlan.is_active.is_(is_active))
    return (await db.execute(q.order_by(MaintenancePlan.name))).scalars().all()


@plans_router.get("/status", dependencies=[Depends(require_any(*MAINT_READ))])
@cached_endpoint("plans_status")   # Sprint 7: short TTL, per user (services/response_cache.py)
async def plans_status(user: CurrentUser, db: DB, plant_id: Optional[str] = None, line_id: Optional[str] = None,
                       machine_id: Optional[str] = None,
                       state: Optional[list[Literal["ok", "due_soon", "due", "overdue"]]] = Query(None)):
    """Próximas manutenções: one row per active plan, ordered by days_to_due (S4-3)."""
    for v in (plant_id, line_id, machine_id):
        if v and not is_uuid(v):
            return []
    mids = await mk.scope_machines(db, user.tenant_id, plant_id, line_id)
    if machine_id:
        mids = [m for m in mids if m == machine_id]
    if not mids:
        return []
    plans = list((await db.execute(select(MaintenancePlan).where(
        MaintenancePlan.tenant_id == user.tenant_id, MaintenancePlan.is_active.is_(True),
        MaintenancePlan.machine_id.in_(mids)))).scalars().all())
    evals = await ms.evaluate_plans(db, user.tenant_id, plans)
    names = await ms.asset_names(db, user.tenant_id, [p.machine_id for p in plans], [p.position_id for p in plans])
    open_wos = await ms.open_plan_work_orders(db, user.tenant_id, [p.id for p in plans])
    rows = [ms.status_row(ev, names, open_wos.get(ev.plan.id)) for ev in evals]
    if state:
        rows = [r for r in rows if r["state"] in state]
    rows.sort(key=lambda r: (r["days_to_due"] is None, r["days_to_due"] if r["days_to_due"] is not None else 0,
                             -(r["consumed"] or 0)))
    return rows


@plans_router.post("/recompute", dependencies=[Depends(require_any(*PLANNER))])
async def recompute_plans(user: CurrentUser, db: DB):
    """Runs the plan engine now (states, drafts at ≥ 80 %, notifications). The engine also runs hourly
    and whenever usage changes."""
    evals = await ms.run_plan_engine(db, user.tenant_id)
    await _commit(db)
    return {"plans": len(evals)}


@plans_router.post("/", response_model=PlanOut, status_code=201, dependencies=[Depends(require_any(*PLANNER))])
async def create_plan(body: PlanCreate, user: CurrentUser, db: DB):
    pos_id, mach_id = await _plan_anchor(db, user.tenant_id, body.position_id, body.machine_id)
    data = body.model_dump(exclude={"position_id", "machine_id", "last_done_at", "last_done_usage", "task_list"})
    plan = MaintenancePlan(tenant_id=user.tenant_id, position_id=pos_id, machine_id=mach_id,
                           task_list=_tasks(body.task_list), created_by=user.id, last_reset_at=clock.now(), **data)
    await _set_last_done(db, plan, body.last_done_at, body.last_done_usage)
    db.add(plan)
    await db.flush()
    await ms.run_plan_engine(db, user.tenant_id, plan_ids=[plan.id])
    await _commit(db)
    await db.refresh(plan)
    return plan


@plans_router.get("/{plan_id}", response_model=PlanOut, dependencies=[Depends(require_any(*MAINT_READ))])
async def get_plan(plan_id: str, user: CurrentUser, db: DB):
    return await _get(db, MaintenancePlan, plan_id, user.tenant_id, "Plano")


@plans_router.patch("/{plan_id}", response_model=PlanOut, dependencies=[Depends(require_any(*PLANNER))])
async def update_plan(plan_id: str, body: PlanUpdate, user: CurrentUser, db: DB):
    plan = await _get(db, MaintenancePlan, plan_id, user.tenant_id, "Plano", lock=True)
    apply_patch(plan, body, nullable=frozenset({"interval_cycles", "interval_hours", "interval_starts", "interval_days",
                                                "description"}),
                skip=frozenset({"task_list", "last_done_at", "last_done_usage"}))
    if body.task_list is not None:
        plan.task_list = _tasks(body.task_list)
    if not any((plan.interval_cycles, plan.interval_hours, plan.interval_starts, plan.interval_days,
                plan.condition_trigger)):          # condition_trigger: predictive.md P-9 (Sprint 5)
        raise HTTPException(status_code=422, detail="O plano precisa de pelo menos um gatilho.")
    if "last_done_at" in body.model_fields_set or "last_done_usage" in body.model_fields_set:
        await _set_last_done(db, plan, body.last_done_at or plan.last_reset_at, body.last_done_usage)
    await db.flush()
    await ms.run_plan_engine(db, user.tenant_id, plan_ids=[plan.id])
    await _commit(db)
    await db.refresh(plan)
    return plan


@plans_router.delete("/{plan_id}", status_code=204, dependencies=[Depends(require_any(*PLANNER))])
async def delete_plan(plan_id: str, user: CurrentUser, db: DB):
    """Deactivates the plan (history of its WOs is kept); a draft WO of the plan is cancelled."""
    plan = await _get(db, MaintenancePlan, plan_id, user.tenant_id, "Plano", lock=True)
    plan.is_active = False
    draft = (await ms.open_plan_work_orders(db, user.tenant_id, [plan.id])).get(plan.id)
    if draft is not None and draft.state == "draft":
        await ms.transition(db, draft, "cancelled", user, cancel_reason="not_needed", note="plano desativado",
                            check_permission=False)
    await _commit(db)


# ══ work orders ═════════════════════════════════════════════════════════════

def _visible_filter(user: User):
    """Technician: own + team (lines); operator-only: own requests; others: all of the tenant."""
    rs = user.role_set
    if rs & {"admin", "superadmin", "manager", "maintenance_planner", "production_supervisor"}:
        return None
    if "technician" in rs:
        conds = [WorkOrder.assignee_id == user.id]
        if user.line_ids:
            conds.append(WorkOrder.machine_id.in_(select(Machine.id).where(Machine.line_id.in_(list(user.line_ids)))))
        else:
            return None
        return or_(*conds)
    return WorkOrder.created_by == user.id


async def _get_wo(db: AsyncSession, wo_id: str, user: User, lock: bool = False) -> WorkOrder:
    if not is_uuid(wo_id):
        raise _404("OS")
    q = select(WorkOrder).where(WorkOrder.id == wo_id, WorkOrder.tenant_id == user.tenant_id)
    vis = _visible_filter(user)
    if vis is not None:
        q = q.where(vis)
    if lock:
        q = q.with_for_update()
    wo = (await db.execute(q)).scalar_one_or_none()
    if wo is None:
        raise _404("OS")
    return wo


@wo_router.get("/")
async def list_work_orders(user: CurrentUser, db: DB, state: Optional[list[str]] = Query(None),
                           type: Optional[str] = None, assignee_id: Optional[str] = None,
                           machine_id: Optional[str] = None, line_id: Optional[str] = None,
                           plant_id: Optional[str] = None, plan_id: Optional[str] = None,
                           overdue: Optional[bool] = None, from_: Optional[date] = Query(None, alias="from"),
                           to: Optional[date] = None, completed_from: Optional[datetime] = None,
                           completed_to: Optional[datetime] = None, limit: int = Query(500, ge=1, le=2000)):
    """Filters: state (repeatable), type, assignee_id ('me' = caller), machine_id, line_id, plant_id, plan_id,
    overdue (scheduled_for < today local and not completed), from/to (scheduled_for, or creation date when not
    scheduled; inclusive), completed_from/completed_to (completed_at)."""
    for v in (machine_id, line_id, plant_id, plan_id):
        if v and not is_uuid(v):
            return []
    q = select(WorkOrder).where(WorkOrder.tenant_id == user.tenant_id)
    vis = _visible_filter(user)
    if vis is not None:
        q = q.where(vis)
    if state:
        q = q.where(WorkOrder.state.in_(state))
    if type:
        q = q.where(WorkOrder.type == type)
    if assignee_id:
        aid = user.id if assignee_id == "me" else assignee_id
        if not is_uuid(aid):
            return []
        q = q.where(WorkOrder.assignee_id == aid)
    if machine_id:
        q = q.where(WorkOrder.machine_id == machine_id)
    if line_id or plant_id:
        mids = await mk.scope_machines(db, user.tenant_id, plant_id, line_id)
        q = q.where(WorkOrder.machine_id.in_(mids))
    if plan_id:
        q = q.where(WorkOrder.plan_id == plan_id)
    if from_ is not None or to is not None:
        lo, hi = from_ or date.min, to or date.max
        created_day = func.date(WorkOrder.created_at)
        q = q.where(or_(and_(WorkOrder.scheduled_for.is_not(None), WorkOrder.scheduled_for >= lo, WorkOrder.scheduled_for <= hi),
                        and_(WorkOrder.scheduled_for.is_(None), created_day >= lo, created_day <= hi)))
    if completed_from is not None:
        q = q.where(WorkOrder.completed_at >= _aware(completed_from))
    if completed_to is not None:
        q = q.where(WorkOrder.completed_at < _aware(completed_to))
    wos = (await db.execute(q.order_by(WorkOrder.number.desc()).limit(limit))).scalars().all()
    names = await ms.asset_names(db, user.tenant_id, [w.machine_id for w in wos], [w.position_id for w in wos],
                                 [w.assignee_id for w in wos], [w.plan_id for w in wos])
    tzs = await mu.machine_tz(db, user.tenant_id, [w.machine_id for w in wos])
    fallback = await mu.tenant_tz(db, user.tenant_id)
    now = clock.now()
    out = [await ms.wo_out(db, w, user, full=False, names=names,
                           today=now.astimezone(tzs.get(w.machine_id) or fallback).date()) for w in wos]
    if overdue is not None:
        out = [o for o in out if o["overdue"] == overdue]
    return out


@wo_router.get("/{wo_id}")
async def get_work_order(wo_id: str, user: CurrentUser, db: DB):
    wo = await _get_wo(db, wo_id, user)
    return await ms.wo_out(db, wo, user)


def _can_create(user: User, body: WorkOrderCreate) -> bool:
    if has_any(user, PLANNER):
        return True
    # supervisor / operator / technician: only an 'open' corrective request (technician: emergency too)
    return body.state == "open" and body.type == "corrective" and body.plan_id is None \
        and bool(user.role_set & {"technician", "production_supervisor", "operator"})


@wo_router.post("/", status_code=201)
async def create_work_order(body: WorkOrderCreate, user: CurrentUser, db: DB):
    if not _can_create(user, body):
        raise HTTPException(status_code=403, detail="Seu papel só pode abrir solicitação corretiva (state=open).")
    tid = user.tenant_id
    now = clock.now()
    at = _aware(body.at) or now
    if at > now + ms.CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="`at` não pode estar no futuro.")
    plan = None
    position_id, machine_id = None, None
    if body.plan_id:
        plan = await _get(db, MaintenancePlan, body.plan_id, tid, "plan_id")
        if body.type not in ("preventive", "predictive"):
            raise HTTPException(status_code=422, detail="OS de plano é preventive ou predictive.")
        position_id, machine_id = plan.position_id, plan.machine_id
    if body.position_id:
        position_id, machine_id = await _plan_anchor(db, tid, body.position_id, body.machine_id or machine_id)
    elif body.machine_id and not machine_id:
        machine_id = await ensure_owned(db, Machine, body.machine_id, tid, "machine_id")
    ev = None
    if body.downtime_event_id:
        ev = await _get(db, DowntimeEvent, body.downtime_event_id, tid, "downtime_event_id")
        machine_id = machine_id or ev.machine_id
    failure = None
    fid = body.failure_id or (ev.failure_id if ev else None)
    if fid:
        failure = await _get(db, FailureRecord, fid, tid, "failure_id")
        machine_id = machine_id or failure.machine_id
        position_id = position_id or failure.position_id
    if machine_id is None:
        raise HTTPException(status_code=422, detail="Informe machine_id, position_id, plan_id ou downtime_event_id.")
    if body.parent_id:
        await _get(db, WorkOrder, body.parent_id, tid, "parent_id")
    if body.assignee_id:
        await ms._check_assignee(db, tid, body.assignee_id)
    if plan is not None and (await ms.open_plan_work_orders(db, tid, [plan.id])):
        raise HTTPException(status_code=409, detail="O plano já tem uma OS aberta (S4-4: nunca duas).")
    number = await ms.next_number(db, tid)
    wo = WorkOrder(tenant_id=tid, number=number, type=body.type, urgency=body.urgency if body.type == "corrective" else None,
                   state=body.state, plan_id=plan.id if plan else None, position_id=position_id, machine_id=machine_id,
                   downtime_event_id=ev.id if ev else None, failure_id=failure.id if failure else None,
                   parent_id=body.parent_id, title=body.title, description=body.description,
                   task_list=[{**t, "done": False} for t in _tasks(body.task_list)] or
                             ([{"id": t.get("id"), "text": t.get("text"), "done": False} for t in plan.task_list] if plan else []),
                   estimated_hours=body.estimated_hours or (plan.estimated_hours if plan else 0.0),
                   stops_machine=body.stops_machine or (plan.stops_machine if plan else False),
                   assignee_id=body.assignee_id, created_by=user.id, created_at=at,
                   requested_at=at if body.state == "open" else None)
    db.add(wo)
    await db.flush()
    await ms.add_history(db, wo, at, user, None, body.state, None)
    if failure is not None and failure.work_order_id is None:
        failure.work_order_id = wo.id
    call = None
    if core.is_emergency(wo.type, wo.urgency) and wo.state == "open":
        m = await db.get(Machine, machine_id)
        title = wo.title if wo.title.startswith("Chamado emergencial") else f"Chamado emergencial: {m.name} — {wo.title}"
        call = await ms.emergency_call(db, tid, machine_id, title,
                                body=body.description, link=f"/m/work-orders/{wo.id}",
                                data={"work_order_id": wo.id, "machine_id": machine_id,
                                      "downtime_event_id": wo.downtime_event_id}, exclude_user=user.id)
        if failure is not None:
            failure.emergency_notified_at = failure.emergency_notified_at or clock.now()
    if wo.assignee_id:
        await ms._notify_assignee(db, wo, True, False)
    await webhooks.enqueue(db, tid, "work_order.created", await ms.wo_out(db, wo, full=False))
    await _commit(db)
    out = await ms.wo_out(db, wo, user)
    if call is not None:
        out["emergency_call"] = call.as_dict()     # who was reached — the caller is told when nobody was
    return out


@wo_router.post("/emergency-call", status_code=201)
async def emergency_call(body: EmergencyCallIn, user: CurrentUser, db: DB):
    """"Chamar manutenção": emergency corrective WO (open) linked to the stop and its failure draft; notifies
    the on-call technicians of the line (push with sound; without on-call coverage it escalates to planners/admins).
    The response carries `emergency_call: {notified_count, on_call_technicians, fallback, warning}`; an already
    open call for the stop is returned with `already_open: true` (nobody is notified again)."""
    if not (user.role_set & {"technician", "production_supervisor", "operator", "maintenance_planner", "manager",
                             "admin", "superadmin"}):
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    tid = user.tenant_id
    ev = await _get(db, DowntimeEvent, body.downtime_event_id, tid, "downtime_event_id") if body.downtime_event_id else None
    if ev is not None:
        existing = (await db.execute(select(WorkOrder).where(
            WorkOrder.tenant_id == tid, WorkOrder.downtime_event_id == ev.id,
            WorkOrder.state.in_(core.OPEN_STATES)))).scalars().first()
        if existing is not None:
            return {**(await ms.wo_out(db, existing, user)), "already_open": True}
    machine_id = ev.machine_id if ev else body.machine_id
    if not machine_id:
        raise HTTPException(status_code=422, detail="Informe downtime_event_id ou machine_id.")
    m = await _get(db, Machine, machine_id, tid, "machine_id")
    req = WorkOrderCreate(type="corrective", urgency="emergency", state="open", machine_id=m.id,
                          position_id=body.position_id, downtime_event_id=ev.id if ev else None,
                          title=body.title or f"Chamado emergencial — {m.name}", description=body.note)
    return await create_work_order(req, user, db)


@wo_router.post("/{wo_id}/transition")
async def transition_work_order(wo_id: str, body: TransitionIn, user: CurrentUser, db: DB,
                                x_client_action_id: Optional[str] = Header(None, max_length=64)):
    """State machine (domain.md §5.3, S4-5). Invalid → 422 {message, allowed, state, to}; role not allowed → 403."""
    wo = await _get_wo(db, wo_id, user, lock=True)
    if x_client_action_id and wo.state == body.to:
        # offline replay of a transition already applied (same client action): idempotent
        return {**(await ms.wo_out(db, wo, user)), "plans_reset": [], "new_draft_id": None, "replayed": True}
    res = await ms.transition(db, wo, body.to, user, note=body.note, at=body.at, scheduled_for=body.scheduled_for,
                              assignee_id=body.assignee_id, hold_reason=body.hold_reason,
                              cancel_reason=body.cancel_reason, labor_hours=body.labor_hours,
                              remaining_hours=body.remaining_hours, finding=body.finding,
                              resets_baseline=body.resets_baseline)
    await _commit(db)
    await db.refresh(wo)
    return {**(await ms.wo_out(db, wo, user)), "plans_reset": res["plans_reset"], "new_draft_id": res["new_draft_id"]}


@wo_router.post("/{wo_id}/approve-draft")
async def approve_draft(wo_id: str, user: CurrentUser, db: DB, note: Optional[str] = None):
    """One click 'aprovar rascunho': draft → open → approved (two audited transitions)."""
    wo = await _get_wo(db, wo_id, user, lock=True)
    if wo.state != "draft":
        raise HTTPException(status_code=422, detail={"message": "Só um rascunho pode ser aprovado assim.",
                                                     "allowed": core.allowed_targets(wo.state, wo.type, wo.urgency)})
    await ms.transition(db, wo, "open", user, note=note)
    await ms.transition(db, wo, "approved", user, note=note)
    await _commit(db)
    return await ms.wo_out(db, wo, user)


@wo_router.patch("/{wo_id}")
async def update_work_order(wo_id: str, body: WorkOrderUpdate, user: CurrentUser, db: DB):
    """Planner edits (checklist, hours, assignee, reprogramming scheduled_for — scheduled_at is kept, S4-5)."""
    if not has_any(user, PLANNER):
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    wo = await _get_wo(db, wo_id, user, lock=True)
    if wo.state not in core.BEFORE_COMPLETED:
        raise HTTPException(status_code=422, detail="OS concluída/fechada/cancelada não é editada.")
    sent = body.model_fields_set
    assigned = rescheduled = False
    if "assignee_id" in sent and body.assignee_id != wo.assignee_id:
        if body.assignee_id:
            await ms._check_assignee(db, user.tenant_id, body.assignee_id)
        wo.assignee_id = body.assignee_id
        assigned = bool(body.assignee_id)
    if "scheduled_for" in sent and body.scheduled_for != wo.scheduled_for:
        if wo.state not in ("scheduled", "on_hold", "in_progress"):
            raise HTTPException(status_code=422, detail="Programe a OS pela transição approved → scheduled.")
        if body.scheduled_for is None:
            raise HTTPException(status_code=422, detail="scheduled_for não pode ser removido de OS programada.")
        wo.scheduled_for = body.scheduled_for
        rescheduled = True
    if "position_id" in sent and body.position_id:
        pos, mach = await _plan_anchor(db, user.tenant_id, body.position_id, None)
        if mach != wo.machine_id:
            raise HTTPException(status_code=422, detail="A posição é de outra máquina.")
        wo.position_id = pos
    if "urgency" in sent and body.urgency and wo.type == "corrective":
        wo.urgency = body.urgency
    for f in ("title", "description", "estimated_hours", "stops_machine"):
        if f in sent and (getattr(body, f) is not None or f == "description"):
            setattr(wo, f, getattr(body, f))
    if body.task_list is not None:
        old = {t.get("id"): t for t in (wo.task_list or [])}
        wo.task_list = [{**t, "done": bool(old.get(t["id"], {}).get("done"))} for t in _tasks(body.task_list)]
    await ms._notify_assignee(db, wo, assigned, rescheduled and not assigned)
    await webhooks.enqueue(db, user.tenant_id, "work_order.updated", await ms.wo_out(db, wo, full=False))
    await _commit(db)
    return await ms.wo_out(db, wo, user)


@wo_router.post("/{wo_id}/labor", status_code=201)
async def add_labor(wo_id: str, body: LaborIn, user: CurrentUser, db: DB,
                    x_client_action_id: Optional[str] = Header(None, max_length=64)):
    """Technician logs hours on their own WO (planner cannot log for the technician). An offline-queued
    action replayed with the same `X-Client-Action-Id` is applied once."""
    wo = await _get_wo(db, wo_id, user, lock=True)
    if not ms.can_execute(user, wo):
        raise HTTPException(status_code=403, detail="Só o técnico da OS aponta horas.")
    now = clock.now()
    at = _aware(body.at) or now
    if at > now + ms.CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="`at` não pode estar no futuro.")
    await ms.add_labor(db, wo, user, body.hours, body.note, body.remaining_hours, at, x_client_action_id)
    await _commit(db)
    return await ms.wo_out(db, wo, user)


@wo_router.patch("/{wo_id}/tasks")
async def patch_task(wo_id: str, body: TaskPatch, user: CurrentUser, db: DB):
    wo = await _get_wo(db, wo_id, user, lock=True)
    if not ms.can_execute(user, wo):
        raise HTTPException(status_code=403, detail="Só o técnico da OS marca o checklist.")
    if wo.state not in ("scheduled", "in_progress", "on_hold", "open", "approved"):
        raise HTTPException(status_code=422, detail="Checklist só antes da conclusão.")
    tasks = [dict(t) for t in (wo.task_list or [])]
    for t in tasks:
        if t.get("id") == body.task_id:
            t["done"] = body.done
            if body.result is not None:
                t["result"] = body.result
            if body.note is not None:
                t["note"] = body.note
            break
    else:
        raise _404("Tarefa")
    wo.task_list = tasks
    await _commit(db)
    return await ms.wo_out(db, wo, user)


@wo_router.post("/{wo_id}/replacements", status_code=201)
async def add_replacement(wo_id: str, body: ReplacementIn, user: CurrentUser, db: DB):
    """Registers a component swap; applied at completion (domain.md §5.4, S4-2)."""
    wo = await _get_wo(db, wo_id, user, lock=True)
    if not ms.can_execute(user, wo):
        raise HTTPException(status_code=403, detail="Só o técnico da OS registra a troca.")
    if wo.state not in ("open", "approved", "scheduled", "in_progress", "on_hold"):
        raise HTTPException(status_code=422, detail="Troca de componente só antes da conclusão da OS.")
    tid = user.tenant_id
    pos = await _get(db, Position, body.position_id, tid, "position_id")
    if pos.machine_id != wo.machine_id:
        raise HTTPException(status_code=422, detail="A posição é de outra máquina.")
    pending = (await db.execute(select(WorkOrderReplacement).where(
        WorkOrderReplacement.work_order_id == wo.id, WorkOrderReplacement.position_id == pos.id,
        WorkOrderReplacement.applied_at.is_(None)))).scalars().first()
    if pending is not None:
        raise HTTPException(status_code=409, detail="Já há uma troca registrada para esta posição nesta OS.")
    old = (await db.execute(select(Component).where(Component.tenant_id == tid, Component.position_id == pos.id,
                                                    Component.removed_at.is_(None)))).scalar_one_or_none()
    if body.new_component_id:
        new = await _get(db, Component, body.new_component_id, tid, "new_component_id", lock=True)
        if new.position_id is not None or new.removed_at is not None:
            raise HTTPException(status_code=409, detail="O componente novo deve estar em estoque (nem instalado nem removido).")
        claimed = (await db.execute(select(WorkOrder.number).join(
            WorkOrderReplacement, WorkOrderReplacement.work_order_id == WorkOrder.id).where(
            WorkOrderReplacement.tenant_id == tid, WorkOrderReplacement.installed_component_id == new.id,
            WorkOrderReplacement.applied_at.is_(None), WorkOrder.state.in_(core.OPEN_STATES)))).scalars().first()
        if claimed is not None:
            raise HTTPException(status_code=409, detail=f"Este componente de estoque já está reservado para uma troca "
                                                        f"na {ms.wo_label(claimed)}.")
    else:
        nc = body.new_component
        ctype = nc.component_type or pos.component_type.value
        try:
            ctype_e = ComponentType(ctype)
        except ValueError:
            raise HTTPException(status_code=422, detail="component_type inválido.")
        life = {k: v for k, v in (nc.nominal_life or {}).items() if k in ("cycles", "hours", "starts")
                and isinstance(v, (int, float)) and v > 0}
        if not life and old is not None and old.nominal_life and old.component_type == ctype_e \
                and (nc.model or "").strip().lower() == (old.model or "").strip().lower():
            life = dict(old.nominal_life)          # same model as the removed one → same nominal life
        new = Component(tenant_id=tid, component_type=ctype_e, manufacturer=nc.manufacturer, model=nc.model,
                        serial_number=nc.serial_number, nominal_life=life)
        db.add(new)
        await db.flush()
    if new.component_type != pos.component_type and ComponentType.other not in (new.component_type, pos.component_type):
        raise HTTPException(status_code=422, detail="Tipo do componente diferente do tipo da posição.")
    rep = WorkOrderReplacement(tenant_id=tid, work_order_id=wo.id, position_id=pos.id,
                               removed_component_id=old.id if old else None, installed_component_id=new.id,
                               removal_reason=RemovalReason(body.removal_reason).value, created_by=user.id)
    db.add(rep)
    await _commit(db)
    return await ms.wo_out(db, wo, user)


@wo_router.delete("/{wo_id}/replacements/{rep_id}", status_code=204)
async def delete_replacement(wo_id: str, rep_id: str, user: CurrentUser, db: DB):
    wo = await _get_wo(db, wo_id, user, lock=True)
    if not ms.can_execute(user, wo):
        raise HTTPException(status_code=403, detail="Só o técnico da OS altera a troca.")
    rep = await _get(db, WorkOrderReplacement, rep_id, user.tenant_id, "Troca")
    if rep.work_order_id != wo.id or rep.applied_at is not None:
        raise HTTPException(status_code=409, detail="Troca já aplicada (conclusão) não é desfeita.")
    await db.delete(rep)
    await _commit(db)


# ══ failures (ISO 14224) ═════════════════════════════════════════════════════

def _failure_status(f: FailureRecord) -> str:
    return "complete" if (f.severity and f.failure_mode and f.mechanism and f.cause and f.detection_method) else "draft"


async def _failure_out(db: AsyncSession, f: FailureRecord) -> dict:
    names = await ms.asset_names(db, f.tenant_id, [f.machine_id], [f.position_id])
    comp = await db.get(Component, f.component_id) if f.component_id else None
    return _failure_dict(f, names, comp, await stale_open_ids(db, f.tenant_id, [f], clock.now()))


async def _failures_out(db: AsyncSession, tenant_id: str, rows: list[FailureRecord]) -> list[dict]:
    """Sprint 7 (performance): a list of failures with ONE name lookup and ONE component query (it was 2–3 queries
    per failure: 2 000 failures of a month took > 1 s, the monthly maintenance report asks for them)."""
    if not rows:
        return []
    names = await ms.asset_names(db, tenant_id, list({f.machine_id for f in rows}),
                                 list({f.position_id for f in rows if f.position_id}))
    cids = list({f.component_id for f in rows if f.component_id})
    comps = {c.id: c for c in (await db.execute(select(Component).where(
        Component.tenant_id == tenant_id, Component.id.in_(cids)))).scalars()} if cids else {}
    stale = await stale_open_ids(db, tenant_id, rows, clock.now())
    return [_failure_dict(f, names, comps.get(f.component_id), stale) for f in rows]


def _failure_dict(f: FailureRecord, names: dict, comp, stale_ids: set[str]) -> dict:
    iso = clock.iso
    return {
        "id": f.id, "machine_id": f.machine_id, "machine_name": names.get(f.machine_id),
        "line_id": names.get(("line", f.machine_id)), "position_id": f.position_id,
        "position_name": names.get(f.position_id), "component_id": f.component_id,
        "component": ({"id": comp.id, "manufacturer": comp.manufacturer, "model": comp.model,
                       "serial_number": comp.serial_number, "component_type": comp.component_type.value} if comp else None),
        "downtime_event_id": f.downtime_event_id, "work_order_id": f.work_order_id,
        "failed_at": iso(f.failed_at), "restored_at": iso(f.restored_at), "detected_at": iso(f.detected_at),
        "repair_started_at": iso(f.repair_started_at), "repair_finished_at": iso(f.repair_finished_at),
        "restore_duration_s": (f.restored_at - f.failed_at).total_seconds() if f.restored_at else None,
        "severity": f.severity, "failure_mode": f.failure_mode, "mechanism": f.mechanism,
        "failure_mechanism": f.mechanism, "cause": f.cause, "failure_cause": f.cause,
        "detection_method": f.detection_method, "status": f.status, "usage_at_failure": f.usage_at_failure,
        "note": f.note, "open": f.restored_at is None and (f.downtime_event_id is not None
                                                      or f.detection_method == "production_interference"),
        # S7-23: old open record contradicted by the data → "falha aberta há muito tempo — verificar"
        # (same predicate as the plant map: services/maintenance/stale.py)
        "open_stale": f.id in stale_ids,
        "created_at": iso(f.created_at), "updated_at": iso(f.updated_at),
    }


def _check_codes(mode, mech, cause, det) -> None:
    if mode is not None and mode not in core.FAILURE_MODE_CODES:
        raise HTTPException(status_code=422, detail=f"failure_mode inválido: {mode}")
    if mech is not None and mech not in core.FAILURE_MECHANISM_CODES:
        raise HTTPException(status_code=422, detail=f"mechanism inválido: {mech}")
    if cause is not None and cause not in core.FAILURE_CAUSE_CODES:
        raise HTTPException(status_code=422, detail=f"cause inválida: {cause}")
    if det is not None and det not in core.DETECTION_METHOD_CODES:
        raise HTTPException(status_code=422, detail=f"detection_method inválido: {det}")


async def _link_item(db: AsyncSession, f: FailureRecord) -> None:
    """Validates position/component against the machine; S4-9 fills the component installed at failed_at;
    snapshots usage_at_failure (server-filled)."""
    tid = f.tenant_id
    if f.position_id:
        pos = await _get(db, Position, f.position_id, tid, "position_id")
        if pos.machine_id != f.machine_id:
            raise HTTPException(status_code=422, detail="A posição é de outra máquina.")
        comps = (await db.execute(select(Component).where(Component.tenant_id == tid,
                                                          Component.last_position_id == pos.id))).scalars().all()
        if f.component_id:
            c = await _get(db, Component, f.component_id, tid, "component_id")
            if c.last_position_id != pos.id:
                raise HTTPException(status_code=422, detail="O componente não estava nesta posição.")
        else:
            f.component_id = mk.attribute_component(f, {pos.id: list(comps)})
    elif f.component_id:
        c = await _get(db, Component, f.component_id, tid, "component_id")
        if c.last_position_id is None:
            raise HTTPException(status_code=422, detail="Componente nunca instalado.")
        f.position_id = c.last_position_id
        pm = await mu.position_machine(db, tid, f.position_id)
        if pm != f.machine_id:
            raise HTTPException(status_code=422, detail="O componente é de outra máquina.")
    now = clock.now()
    snap = {}
    if f.position_id:
        snap["position_total"] = await mu.usage_at(db, tid, "position", f.position_id, f.failed_at, now)
    if f.component_id:
        snap["component_since_install"] = await mu.usage_at(db, tid, "component", f.component_id, f.failed_at, now)
    f.usage_at_failure = snap or None


@failures_router.get("/", dependencies=[Depends(require_any(*MAINT_READ))])
async def list_failures(user: CurrentUser, db: DB, machine_id: Optional[str] = None, line_id: Optional[str] = None,
                        plant_id: Optional[str] = None, position_id: Optional[str] = None,
                        component_id: Optional[str] = None, severity: Optional[str] = None,
                        open: Optional[bool] = None, status: Optional[str] = None,
                        start: Optional[datetime] = None, stop: Optional[datetime] = None,
                        limit: int = Query(500, ge=1, le=2000)):
    for v in (machine_id, line_id, plant_id, position_id, component_id):
        if v and not is_uuid(v):
            return []
    q = select(FailureRecord).where(FailureRecord.tenant_id == user.tenant_id)
    if machine_id:
        q = q.where(FailureRecord.machine_id == machine_id)
    if line_id or plant_id:
        q = q.where(FailureRecord.machine_id.in_(await mk.scope_machines(db, user.tenant_id, plant_id, line_id)))
    if position_id:
        q = q.where(FailureRecord.position_id == position_id)
    if component_id:
        q = q.where(FailureRecord.component_id == component_id)
    if severity:
        q = q.where(FailureRecord.severity == severity)
    if open is not None:
        q = q.where(FailureRecord.restored_at.is_(None) if open else FailureRecord.restored_at.is_not(None))
    if status:
        q = q.where(FailureRecord.status == status)
    if start:
        q = q.where(FailureRecord.failed_at >= _aware(start))
    if stop:
        q = q.where(FailureRecord.failed_at < _aware(stop))
    rows = (await db.execute(q.order_by(FailureRecord.failed_at.desc()).limit(limit))).scalars().all()
    return await _failures_out(db, user.tenant_id, rows)


@failures_router.get("/{failure_id}", dependencies=[Depends(require_any(*MAINT_READ))])
async def get_failure(failure_id: str, user: CurrentUser, db: DB):
    return await _failure_out(db, await _get(db, FailureRecord, failure_id, user.tenant_id, "Falha"))


@failures_router.post("/", status_code=201, dependencies=[Depends(require_any(*FAILURE_EDIT))])
async def create_failure(body: FailureCreate, user: CurrentUser, db: DB):
    """Failure without a stop (e.g. found in a preventive: detection_method = periodic_maintenance) or a
    manual record. Counts in MTBF/MCBF; MTTR only with restored_at (S4-10)."""
    tid = user.tenant_id
    mid = await ensure_owned(db, Machine, body.machine_id, tid, "machine_id")
    _check_codes(body.failure_mode, body.mechanism, body.cause, body.detection_method)
    failed_at, restored_at = _aware(body.failed_at), _aware(body.restored_at)
    if failed_at > clock.now() + ms.CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="failed_at não pode estar no futuro.")
    if restored_at is not None and restored_at < failed_at:
        raise HTTPException(status_code=422, detail="restored_at anterior a failed_at.")
    f = FailureRecord(tenant_id=tid, machine_id=mid, position_id=body.position_id, component_id=body.component_id,
                      failed_at=failed_at, restored_at=restored_at, detected_at=_aware(body.detected_at),
                      repair_started_at=_aware(body.repair_started_at), repair_finished_at=_aware(body.repair_finished_at),
                      severity=body.severity, failure_mode=body.failure_mode, mechanism=body.mechanism, cause=body.cause,
                      detection_method=body.detection_method, note=body.note, emergency_notified_at=clock.now())
    if body.downtime_event_id:
        ev = await _get(db, DowntimeEvent, body.downtime_event_id, tid, "downtime_event_id")
        if ev.failure_id:
            raise HTTPException(status_code=409, detail="A parada já tem uma falha (uma parada ↔ no máximo uma falha).")
        f.downtime_event_id = ev.id
    if body.work_order_id:
        wo = await _get(db, WorkOrder, body.work_order_id, tid, "work_order_id")
        f.work_order_id = wo.id
    await _link_item(db, f)
    f.status = _failure_status(f)
    db.add(f)
    await db.flush()
    if f.downtime_event_id:
        ev = await db.get(DowntimeEvent, f.downtime_event_id)
        ev.failure_id = f.id
    if f.work_order_id:
        wo = await db.get(WorkOrder, f.work_order_id)
        if wo.failure_id is None:
            wo.failure_id = f.id
    line_id = await ms.machine_line(db, tid, mid)
    planners = await notif.users_with_roles(db, tid, ("maintenance_planner",), line_id)
    await notif.notify(db, tid, [u for u in planners if u.id != user.id], "failure_new",
                       f"Falha registrada ({f.severity})", link=f"/maintenance/failures/{f.id}",
                       data={"failure_id": f.id}, group_key="failure_new", line_id=line_id,
                       grouped_title="{count} falhas novas registradas")
    # predictive.md P-8: a failure at a monitored position closes its open predictive alert now
    from ...services.predictive.engine import on_failure_changed
    await on_failure_changed(db, tid, [f.position_id])
    out = await _failure_out(db, f)
    await webhooks.enqueue(db, tid, "failure.created", out)
    await _commit(db)
    return out


@failures_router.patch("/{failure_id}", dependencies=[Depends(require_any(*FAILURE_EDIT))])
async def patch_failure(failure_id: str, body: FailurePatch, user: CurrentUser, db: DB):
    """Complete ISO 14224 data (mode, mechanism, cause, detection), link to position/component/WO,
    confirm restoration (supervisor, machine without run_state)."""
    f = await _get(db, FailureRecord, failure_id, user.tenant_id, "Falha", lock=True)
    old_position = f.position_id
    sent = body.model_fields_set
    data = body.model_dump(exclude_unset=True)
    if "failure_mechanism" in sent:
        data["mechanism"] = data.pop("failure_mechanism")
    if "failure_cause" in sent:
        data["cause"] = data.pop("failure_cause")
    _check_codes(data.get("failure_mode"), data.get("mechanism"), data.get("cause"), data.get("detection_method"))
    relink = False
    for k, v in data.items():
        if k in ("failed_at", "restored_at", "detected_at", "repair_started_at", "repair_finished_at"):
            v = _aware(v)
        if k == "severity" and v is None:
            continue
        if k == "work_order_id" and v is not None:
            v = (await _get(db, WorkOrder, v, user.tenant_id, "work_order_id")).id
        if k in ("position_id", "component_id", "failed_at"):
            relink = True
        setattr(f, k, v)
    if f.restored_at is not None and f.restored_at < f.failed_at:
        raise HTTPException(status_code=422, detail="restored_at anterior a failed_at.")
    if relink:
        if "position_id" in sent and "component_id" not in sent:
            f.component_id = None
        await _link_item(db, f)
    f.status = _failure_status(f)
    await db.flush()
    await db.refresh(f)
    # predictive.md P-8: closures by failure follow the current record (failed_at moved, severity or position
    # changed) — re-evaluated now for the old and the new position
    from ...services.predictive.engine import on_failure_changed
    await on_failure_changed(db, user.tenant_id, [old_position, f.position_id])
    out = await _failure_out(db, f)
    await webhooks.enqueue(db, user.tenant_id, "failure.updated", out)
    await _commit(db)
    return out


@codes_router.get("/")
async def failure_codes(user: CurrentUser):
    """ISO 14224 lists (domain.md §5.5) with PT labels, plus the short list of modes per component type."""
    pair = lambda lst: [{"code": c, "label": l} for c, l in lst]  # noqa: E731
    return {"failure_modes": pair(core.FAILURE_MODES), "mechanisms": pair(core.FAILURE_MECHANISMS),
            "causes": pair(core.FAILURE_CAUSES), "detection_methods": pair(core.DETECTION_METHODS),
            "severities": pair(core.SEVERITIES), "modes_by_component_type": core.MODES_BY_COMPONENT_TYPE,
            "cancel_reasons": list(core.CANCEL_REASONS),
            "removal_reasons": [r.value for r in RemovalReason]}


# ══ KPIs, reliability, components, settings ═════════════════════════════════

@mkpi_router.get("/kpis", dependencies=[Depends(require_any(*KPI_READ))])
async def maintenance_kpis(user: CurrentUser, db: DB, plant_id: Optional[str] = None,
                           start: Optional[datetime] = None, stop: Optional[datetime] = None):
    """PMP (kpis.md §3.1), schedule compliance (§3.2, S4-6) and backlog (§3.3, S4-7). Default period:
    current month (plant local) until now."""
    if plant_id:
        await _get(db, Plant, plant_id, user.tenant_id, "plant_id")
    if start and stop and _aware(stop) <= _aware(start):
        raise HTTPException(status_code=422, detail="stop deve ser após start.")
    return await mk.management_kpis(db, user.tenant_id, plant_id, _aware(start), _aware(stop))


@mkpi_router.get("/reliability", dependencies=[Depends(require_any(*KPI_READ))])
async def maintenance_reliability(user: CurrentUser, db: DB,
                                  group_by: Literal["position", "machine", "component_type", "manufacturer",
                                                    "manufacturer_model"] = "position",
                                  start: Optional[datetime] = None, stop: Optional[datetime] = None,
                                  plant_id: Optional[str] = None, line_id: Optional[str] = None):
    """MTBF/MCBF/MTTR (kpis.md §2, S4-8 … S4-10) and MTTF (S4-11). Default window (S7-21): from local midnight
    12 months ago up to now (today's failures included)."""
    if plant_id:
        await _get(db, Plant, plant_id, user.tenant_id, "plant_id")
    if line_id:
        await _get(db, Line, line_id, user.tenant_id, "line_id")
    return await mk.reliability(db, user.tenant_id, group_by, _aware(start), _aware(stop), plant_id, line_id)


@mkpi_router.get("/components", dependencies=[Depends(require_any(*MAINT_READ))])
async def maintenance_components(user: CurrentUser, db: DB, plant_id: Optional[str] = None,
                                 line_id: Optional[str] = None, min_life: Optional[float] = None):
    """Installed components by % of life (S4-12), with RUL and alert (attention ≥ 0.8, nominal_reached ≥ 1)."""
    for v in (plant_id, line_id):
        if v and not is_uuid(v):
            return []
    rows = await mk.components_life(db, user.tenant_id, plant_id, line_id)
    if min_life is not None:
        rows = [r for r in rows if (r["life_consumed_pct"] or 0) >= min_life]
    return rows


def _settings_out(s) -> dict:
    return {"schedule_tolerance_days": s.schedule_tolerance_days, "technician_hours_per_week": s.technician_hours_per_week,
            "due_soon_at": s.due_soon_at, "emergency_call_after_s": s.emergency_call_after_s,
            "default_mttr_min": s.default_mttr_min, "digest_hour_local": s.digest_hour_local}


@mkpi_router.get("/settings", dependencies=[Depends(require_any(*MAINT_READ))])
async def get_settings(user: CurrentUser, db: DB):
    s = await ms.settings_for(db, user.tenant_id)
    await db.commit()
    return _settings_out(s)


@mkpi_router.put("/settings", dependencies=[Depends(require_any("manager"))])
async def put_settings(body: SettingsIn, user: CurrentUser, db: DB):
    s = await ms.settings_for(db, user.tenant_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    await db.commit()
    return _settings_out(s)


@mkpi_router.get("/absences", dependencies=[Depends(require_any(*PLANNER))])
async def list_absences(user: CurrentUser, db: DB, start: Optional[date] = None, stop: Optional[date] = None):
    q = select(MaintenanceAbsence).where(MaintenanceAbsence.tenant_id == user.tenant_id)
    if start:
        q = q.where(MaintenanceAbsence.date >= start)
    if stop:
        q = q.where(MaintenanceAbsence.date < stop)
    rows = (await db.execute(q.order_by(MaintenanceAbsence.date))).scalars().all()
    return [{"id": a.id, "user_id": a.user_id, "date": a.date.isoformat(), "hours": a.hours, "note": a.note} for a in rows]


@mkpi_router.post("/absences", status_code=201, dependencies=[Depends(require_any(*PLANNER))])
async def create_absence(body: AbsenceIn, user: CurrentUser, db: DB):
    u = await ms._check_assignee(db, user.tenant_id, body.user_id)
    a = MaintenanceAbsence(tenant_id=user.tenant_id, user_id=u.id, date=body.date, hours=body.hours, note=body.note)
    db.add(a)
    await db.commit()
    return {"id": a.id, "user_id": a.user_id, "date": a.date.isoformat(), "hours": a.hours, "note": a.note}


@mkpi_router.delete("/absences/{absence_id}", status_code=204, dependencies=[Depends(require_any(*PLANNER))])
async def delete_absence(absence_id: str, user: CurrentUser, db: DB):
    a = await _get(db, MaintenanceAbsence, absence_id, user.tenant_id, "Ausência")
    await db.delete(a)
    await db.commit()
