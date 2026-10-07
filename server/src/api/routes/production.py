"""Fase 2 registries: shifts, calendar exceptions, products, PRI per machine, production runs,
reason codes. Every query filters tenant_id; foreign ids are checked with ensure_owned."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import (CalendarException, DowntimeEvent, Machine, MachineProduct, Plant, Product, ProductionRun,
                       ReasonCode, Shift, User, UserRole)
from ...services.oee.calendar import Calendar, ExceptionDef, ShiftDef, _window, shifts_overlap
from ...services.oee.engine import load_calendar
from ...services import operator_scope as opscope
from ...services.usage import queries as uq
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas_production import (CalendarExceptionCreate, CalendarExceptionOut, CalendarExceptionUpdate,
                                  MachineProductIn, MachineProductOut, MachineProductUpdate, ProductCreate,
                                  ProductionRunCreate, ProductionRunOut, ProductionRunStop, ProductOut,
                                  ProductUpdate, ReasonCodeCreate, ReasonCodeOut, ReasonCodeUpdate, ShiftCreate,
                                  ShiftOut, ShiftUpdate, BreakIn, _check_breaks)
from ..tenant_refs import ensure_owned

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
ADMIN = [Depends(require_roles(UserRole.admin))]
OPERATOR = [Depends(require_roles(UserRole.operator))]
CLOCK_SKEW_S = 300

shifts_router = APIRouter()
calendar_router = APIRouter()
products_router = APIRouter()
machine_products_router = APIRouter()
runs_router = APIRouter()
reasons_router = APIRouter()


def _404(label: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} não encontrado(a).")


def _422(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)


async def _get(db: AsyncSession, model, obj_id: str, tenant_id: str, label: str):
    if not is_uuid(obj_id):
        raise _404(label)
    row = (await db.execute(select(model).where(model.id == obj_id, model.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise _404(label)
    return row


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _planned_reason(db: AsyncSession, tenant_id: str, code: Optional[str], label: str) -> None:
    """Breaks and calendar planned stops are PDOT: their reason must exist and be planned (PLN-*)."""
    if code is None:
        return
    rows = (await db.execute(select(ReasonCode.planned).where(
        ReasonCode.code == code, ReasonCode.is_active.is_(True),
        or_(ReasonCode.tenant_id.is_(None), ReasonCode.tenant_id == tenant_id)))).scalars().all()
    if not rows:
        raise _422(f"{label}: motivo desconhecido {code!r}.")
    if not any(rows):
        raise _422(f"{label}: {code} não é um motivo planejado — pausas e paradas do calendário são tempo "
                   "planejado (use um código PLN-*).")


async def _plant_tz(db: AsyncSession, plant_id: str):
    return uq.tzinfo((await db.execute(select(Plant.timezone).where(Plant.id == plant_id))).scalar_one())


# ── shifts ──────────────────────────────────────────────────────────────────

async def _check_shift_overlap(db: AsyncSession, tenant_id: str, plant_id: str, cand: ShiftDef,
                               exclude_id: Optional[str] = None) -> None:
    tzname = (await db.execute(select(Plant.timezone).where(Plant.id == plant_id))).scalar_one()
    tz = uq.tzinfo(tzname)
    others = (await db.execute(select(Shift).where(Shift.tenant_id == tenant_id, Shift.plant_id == plant_id,
                                                   Shift.is_active.is_(True)))).scalars().all()
    for s in others:
        if s.id == exclude_id:
            continue
        other = ShiftDef(s.id, s.name, s.start_local, s.end_local, tuple(s.weekdays or ()), ())
        if shifts_overlap(cand, other, tz):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"O turno se sobrepõe ao turno '{s.name}' da mesma planta.")


@shifts_router.get("/", response_model=list[ShiftOut])
async def list_shifts(user: CurrentUser, db: DB, plant_id: Optional[str] = None, include_history: bool = False):
    """Shifts of the tenant. Closed versions (valid_to set, kept for past OEE) only with include_history."""
    q = select(Shift).where(Shift.tenant_id == user.tenant_id)
    if plant_id:
        if not is_uuid(plant_id):
            return []
        q = q.where(Shift.plant_id == plant_id)
    if not include_history:
        q = q.where(Shift.valid_to.is_(None))
    return (await db.execute(q.order_by(Shift.plant_id, Shift.start_local, Shift.valid_from))).scalars().all()


@shifts_router.post("/", response_model=ShiftOut, status_code=status.HTTP_201_CREATED, dependencies=ADMIN)
async def create_shift(body: ShiftCreate, user: CurrentUser, db: DB):
    plant_id = await ensure_owned(db, Plant, body.plant_id, user.tenant_id, "plant_id")
    for b in body.breaks:
        await _planned_reason(db, user.tenant_id, b.reason_code, "breaks.reason_code")
    if body.is_active:
        await _check_shift_overlap(db, user.tenant_id, plant_id, ShiftDef(
            None, body.name, body.start_local, body.end_local, tuple(body.weekdays), ()))
    row = Shift(tenant_id=user.tenant_id, plant_id=plant_id, name=body.name, start_local=body.start_local,
                end_local=body.end_local, weekdays=body.weekdays, is_active=body.is_active,
                breaks=[b.model_dump() for b in body.breaks])
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@shifts_router.get("/{shift_id}", response_model=ShiftOut)
async def get_shift(shift_id: str, user: CurrentUser, db: DB):
    return await _get(db, Shift, shift_id, user.tenant_id, "Turno")


def _has_history(row: Shift, eff: date, tz) -> bool:
    """The version was already in effect on some productive day before `eff`."""
    first = row.valid_from or row.created_at.astimezone(tz).date()
    return first < eff


@shifts_router.patch("/{shift_id}", response_model=ShiftOut, dependencies=ADMIN)
async def update_shift(shift_id: str, body: ShiftUpdate, user: CurrentUser, db: DB):
    """Name-only edits are in place. Changing hours, weekdays or breaks, or (de)activating a shift that
    already ran, versions it (D11): the current row stays in effect until the day before
    `effective_from` (default today, plant time) and a new row — returned — takes over from it."""
    row = await _get(db, Shift, shift_id, user.tenant_id, "Turno")
    sent = body.model_fields_set
    start = body.start_local if "start_local" in sent and body.start_local else row.start_local
    end = body.end_local if "end_local" in sent and body.end_local else row.end_local
    breaks = body.breaks if "breaks" in sent and body.breaks is not None else [BreakIn(**b) for b in row.breaks or []]
    try:
        _check_breaks(start, end, breaks)
    except ValueError as e:
        raise _422(str(e))
    if "breaks" in sent and body.breaks is not None:
        for b in body.breaks:
            await _planned_reason(db, user.tenant_id, b.reason_code, "breaks.reason_code")
    weekdays = body.weekdays if "weekdays" in sent and body.weekdays is not None else row.weekdays
    active = body.is_active if body.is_active is not None else row.is_active
    name = body.name or row.name
    if active:
        await _check_shift_overlap(db, user.tenant_id, row.plant_id, ShiftDef(
            None, name, start, end, tuple(weekdays), ()), exclude_id=row.id)
    tz = await _plant_tz(db, row.plant_id)
    eff = body.effective_from or datetime.now(tz).date()
    new_breaks = [b.model_dump() for b in breaks]
    schedule_changed = (start != row.start_local or end != row.end_local
                        or sorted(weekdays) != sorted(row.weekdays or []) or new_breaks != list(row.breaks or []))
    retired = row.valid_to is not None
    if retired and not active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Esta é uma versão encerrada do turno (histórico) e não pode ser alterada.")
    versioned = (retired and active) or ((schedule_changed or not active) and row.is_active
                                         and _has_history(row, eff, tz))
    if not versioned:
        apply_patch(row, body, skip=frozenset({"breaks", "effective_from"}))
        if "breaks" in sent and body.breaks is not None:
            row.breaks = new_breaks
        await db.commit()
        await db.refresh(row)
        return row
    if row.valid_from is not None and eff <= row.valid_from:
        raise _422("effective_from: deve ser posterior ao início de vigência desta versão "
                   f"({row.valid_from.isoformat()}).")
    if not retired:
        row.valid_to = eff - timedelta(days=1)
        row.is_active = False
        if "name" in sent and body.name:
            row.name = body.name
    if not active:                                  # deactivation from `eff`: nothing replaces it
        await db.commit()
        await db.refresh(row)
        return row
    new = Shift(tenant_id=row.tenant_id, plant_id=row.plant_id, name=name, start_local=start, end_local=end,
                weekdays=list(weekdays), breaks=new_breaks, is_active=True, valid_from=eff)
    db.add(new)
    await db.flush()
    # holidays / planned stops / extra shifts of this shift from `eff` on follow the new version
    await db.execute(update(CalendarException).where(
        CalendarException.tenant_id == user.tenant_id, CalendarException.shift_id == row.id,
        CalendarException.date >= eff).values(shift_id=new.id))
    await db.commit()
    await db.refresh(new)
    return new


@shifts_router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=ADMIN)
async def delete_shift(shift_id: str, user: CurrentUser, db: DB):
    """A shift with recorded stops cannot be deleted (409: deactivate it). A shift that already ran
    is closed (valid_to = yesterday) instead of deleted, so past OEE keeps its schedule (D11)."""
    row = await _get(db, Shift, shift_id, user.tenant_id, "Turno")
    used = (await db.execute(select(func.count()).select_from(DowntimeEvent).where(
        DowntimeEvent.tenant_id == user.tenant_id, DowntimeEvent.shift_id == row.id))).scalar_one()
    if used:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="O turno tem paradas registradas e não pode ser excluído: desative-o "
                                   "(o histórico continua com o horário antigo).")
    tz = await _plant_tz(db, row.plant_id)
    today = datetime.now(tz).date()
    if row.valid_to is None and row.is_active and _has_history(row, today, tz):
        row.valid_to = today - timedelta(days=1)
        row.is_active = False
    elif row.valid_to is None:
        await db.delete(row)
    await db.commit()


# ── calendar exceptions ─────────────────────────────────────────────────────

async def _check_exception_shift(db: AsyncSession, tenant_id: str, plant_id: str, shift_id: Optional[str]) -> Optional[str]:
    if shift_id is None:
        return None
    shift_id = await ensure_owned(db, Shift, shift_id, tenant_id, "shift_id")
    sp = (await db.execute(select(Shift.plant_id).where(Shift.id == shift_id))).scalar_one()
    if sp != plant_id:
        raise _422("shift_id: o turno pertence a outra planta.")
    return shift_id


async def _check_exception(db: AsyncSession, tenant_id: str, plant_id: str, row: CalendarException,
                           exclude_id: Optional[str] = None) -> None:
    """S3-6: an extra shift may not overlap a shift instance of the same plant (409, like D9).
    Planned stops need a planned reason."""
    if row.kind in ("planned_stop", "extra_shift") and row.reason_code:
        await _planned_reason(db, tenant_id, row.reason_code, "reason_code")
    if row.kind != "extra_shift" or not row.start_local or not row.end_local:
        return
    tz = await _plant_tz(db, plant_id)
    base = await load_calendar(db, tenant_id, plant_id, tz)
    others = (await db.execute(select(CalendarException).where(
        CalendarException.tenant_id == tenant_id, CalendarException.plant_id == plant_id,
        CalendarException.date.between(row.date - timedelta(days=1), row.date + timedelta(days=1))))).scalars().all()
    excs = [ExceptionDef(e.date, e.kind, e.shift_id, e.start_local, e.end_local, e.reason_code, e.note)
            for e in others if e.id != exclude_id]
    cal = Calendar(tz, [] if base.default else base.shifts, excs)
    a, b = _window(row.date, row.start_local, row.end_local, tz)
    for day in (row.date - timedelta(days=1), row.date, row.date + timedelta(days=1)):
        for inst in cal.day_instances(day):
            if inst.start < b and a < inst.end:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail=f"O turno extra se sobrepõe ao turno '{inst.name}' de "
                                           f"{inst.productive_day.strftime('%d/%m')} (S3-6).")


@calendar_router.get("/", response_model=list[CalendarExceptionOut])
async def list_exceptions(user: CurrentUser, db: DB, plant_id: Optional[str] = None,
                          start: Optional[str] = None, stop: Optional[str] = None):
    q = select(CalendarException).where(CalendarException.tenant_id == user.tenant_id)
    if plant_id:
        if not is_uuid(plant_id):
            return []
        q = q.where(CalendarException.plant_id == plant_id)
    try:
        if start:
            q = q.where(CalendarException.date >= datetime.fromisoformat(start).date())
        if stop:
            q = q.where(CalendarException.date <= datetime.fromisoformat(stop).date())
    except ValueError:
        raise _422("start/stop: datas ISO (YYYY-MM-DD).")
    return (await db.execute(q.order_by(CalendarException.date))).scalars().all()


@calendar_router.post("/", response_model=CalendarExceptionOut, status_code=status.HTTP_201_CREATED,
                      dependencies=ADMIN)
async def create_exception(body: CalendarExceptionCreate, user: CurrentUser, db: DB):
    plant_id = await ensure_owned(db, Plant, body.plant_id, user.tenant_id, "plant_id")
    shift_id = await _check_exception_shift(db, user.tenant_id, plant_id, body.shift_id)
    row = CalendarException(tenant_id=user.tenant_id, plant_id=plant_id, date=body.date, kind=body.kind,
                            shift_id=shift_id, start_local=body.start_local, end_local=body.end_local,
                            reason_code=body.reason_code, note=body.note)
    await _check_exception(db, user.tenant_id, plant_id, row)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@calendar_router.get("/{exc_id}", response_model=CalendarExceptionOut)
async def get_exception(exc_id: str, user: CurrentUser, db: DB):
    return await _get(db, CalendarException, exc_id, user.tenant_id, "Exceção de calendário")


@calendar_router.patch("/{exc_id}", response_model=CalendarExceptionOut, dependencies=ADMIN)
async def update_exception(exc_id: str, body: CalendarExceptionUpdate, user: CurrentUser, db: DB):
    row = await _get(db, CalendarException, exc_id, user.tenant_id, "Exceção de calendário")
    if "shift_id" in body.model_fields_set and body.shift_id is not None:
        body.shift_id = await _check_exception_shift(db, user.tenant_id, row.plant_id, body.shift_id)
    apply_patch(row, body, nullable=frozenset({"shift_id", "start_local", "end_local", "reason_code", "note"}))
    if (row.start_local is None) != (row.end_local is None):
        raise _422("informe start_local e end_local juntos")
    if row.kind == "extra_shift" and row.start_local is None:
        raise _422("turno extra exige start_local e end_local")
    await _check_exception(db, user.tenant_id, row.plant_id, row, exclude_id=row.id)
    await db.commit()
    await db.refresh(row)
    return row


@calendar_router.delete("/{exc_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=ADMIN)
async def delete_exception(exc_id: str, user: CurrentUser, db: DB):
    row = await _get(db, CalendarException, exc_id, user.tenant_id, "Exceção de calendário")
    await db.delete(row)
    await db.commit()


# ── products ────────────────────────────────────────────────────────────────

@products_router.get("/", response_model=list[ProductOut])
async def list_products(user: CurrentUser, db: DB):
    return (await db.execute(select(Product).where(Product.tenant_id == user.tenant_id)
                             .order_by(Product.code))).scalars().all()


@products_router.post("/", response_model=ProductOut, status_code=status.HTTP_201_CREATED, dependencies=ADMIN)
async def create_product(body: ProductCreate, user: CurrentUser, db: DB):
    row = Product(tenant_id=user.tenant_id, code=body.code, name=body.name)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Já existe um produto com este código.")
    await db.refresh(row)
    return row


@products_router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: str, user: CurrentUser, db: DB):
    return await _get(db, Product, product_id, user.tenant_id, "Produto")


@products_router.patch("/{product_id}", response_model=ProductOut, dependencies=ADMIN)
async def update_product(product_id: str, body: ProductUpdate, user: CurrentUser, db: DB):
    row = await _get(db, Product, product_id, user.tenant_id, "Produto")
    apply_patch(row, body)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Já existe um produto com este código.")
    await db.refresh(row)
    return row


@products_router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=ADMIN)
async def delete_product(product_id: str, user: CurrentUser, db: DB):
    row = await _get(db, Product, product_id, user.tenant_id, "Produto")
    used = (await db.execute(select(func.count()).select_from(ProductionRun).where(
        ProductionRun.tenant_id == user.tenant_id, ProductionRun.product_id == row.id))).scalar_one()
    if used:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="O produto tem ordens de produção registradas.")
    await db.delete(row)
    await db.commit()


# ── machine products (PRI) ─────────────────────────────────────────────────

async def _mp_out(db: AsyncSession, rows: list[MachineProduct]) -> list[MachineProductOut]:
    pids = list({r.product_id for r in rows})
    prods = {p.id: p for p in (await db.execute(select(Product).where(Product.id.in_(pids)))).scalars()} if pids else {}
    out = []
    for r in rows:
        o = MachineProductOut.model_validate(r)
        p = prods.get(r.product_id)
        o.product_code, o.product_name = (p.code, p.name) if p else (None, None)
        o.ideal_time_per_part_s = r.ideal_cycle_time_s / max(1, r.parts_per_cycle)
        out.append(o)
    return out


async def _clear_default(db: AsyncSession, machine_id: str, keep_id: Optional[str] = None) -> None:
    q = update(MachineProduct).where(MachineProduct.machine_id == machine_id, MachineProduct.is_default.is_(True))
    if keep_id:
        q = q.where(MachineProduct.id != keep_id)
    await db.execute(q.values(is_default=False))
    await db.flush()


@machine_products_router.get("/{machine_id}/products", response_model=list[MachineProductOut])
async def list_machine_products(machine_id: str, user: CurrentUser, db: DB):
    await _get(db, Machine, machine_id, user.tenant_id, "Máquina")
    rows = (await db.execute(select(MachineProduct).where(MachineProduct.tenant_id == user.tenant_id,
                                                          MachineProduct.machine_id == machine_id)
                             .order_by(MachineProduct.created_at))).scalars().all()
    return await _mp_out(db, rows)


@machine_products_router.post("/{machine_id}/products", response_model=MachineProductOut,
                              status_code=status.HTTP_201_CREATED, dependencies=ADMIN)
async def add_machine_product(machine_id: str, body: MachineProductIn, user: CurrentUser, db: DB):
    m = await _get(db, Machine, machine_id, user.tenant_id, "Máquina")
    product_id = await ensure_owned(db, Product, body.product_id, user.tenant_id, "product_id")
    if body.is_default:
        await _clear_default(db, m.id)
    row = MachineProduct(tenant_id=user.tenant_id, machine_id=m.id, product_id=product_id,
                         ideal_cycle_time_s=body.ideal_cycle_time_s, parts_per_cycle=body.parts_per_cycle,
                         is_default=body.is_default)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Este produto já está cadastrado nesta máquina.")
    await db.refresh(row)
    return (await _mp_out(db, [row]))[0]


async def _get_mp(db: AsyncSession, machine_id: str, mp_id: str, tenant_id: str) -> MachineProduct:
    row = await _get(db, MachineProduct, mp_id, tenant_id, "Produto da máquina")
    if row.machine_id != machine_id:
        raise _404("Produto da máquina")
    return row


@machine_products_router.patch("/{machine_id}/products/{mp_id}", response_model=MachineProductOut,
                               dependencies=ADMIN)
async def update_machine_product(machine_id: str, mp_id: str, body: MachineProductUpdate, user: CurrentUser, db: DB):
    row = await _get_mp(db, machine_id, mp_id, user.tenant_id)
    if body.is_default:
        await _clear_default(db, row.machine_id, keep_id=row.id)
    apply_patch(row, body)
    await db.commit()
    await db.refresh(row)
    return (await _mp_out(db, [row]))[0]


@machine_products_router.delete("/{machine_id}/products/{mp_id}", status_code=status.HTTP_204_NO_CONTENT,
                                dependencies=ADMIN)
async def delete_machine_product(machine_id: str, mp_id: str, user: CurrentUser, db: DB):
    row = await _get_mp(db, machine_id, mp_id, user.tenant_id)
    await db.delete(row)
    await db.commit()


# ── production runs ────────────────────────────────────────────────────────

async def _runs_out(db: AsyncSession, rows: list[ProductionRun]) -> list[ProductionRunOut]:
    pids = list({r.product_id for r in rows})
    codes = dict((await db.execute(select(Product.id, Product.code).where(Product.id.in_(pids)))).all()) if pids else {}
    out = []
    for r in rows:
        o = ProductionRunOut.model_validate(r)
        o.product_code = codes.get(r.product_id)
        out.append(o)
    return out


def _not_future(ts: datetime, label: str) -> datetime:
    if (ts - datetime.now(timezone.utc)).total_seconds() > CLOCK_SKEW_S:
        raise _422(f"{label} não pode estar no futuro.")
    return ts


@runs_router.get("/", response_model=list[ProductionRunOut])
async def list_runs(user: CurrentUser, db: DB, machine_id: Optional[str] = None, open: Optional[bool] = None,
                    limit: int = 200):
    q = select(ProductionRun).where(ProductionRun.tenant_id == user.tenant_id)
    if machine_id:
        if not is_uuid(machine_id):
            return []
        await opscope.check_machine(db, user, machine_id)        # operator PWA: only the lines of the operator
        q = q.where(ProductionRun.machine_id == machine_id)
    allowed = await opscope.allowed_machine_ids(db, user)
    if allowed is not None:
        q = q.where(ProductionRun.machine_id.in_(allowed))
    if open is not None:
        q = q.where(ProductionRun.ended_at.is_(None) if open else ProductionRun.ended_at.is_not(None))
    rows = (await db.execute(q.order_by(ProductionRun.started_at.desc()).limit(max(1, min(limit, 1000))))).scalars().all()
    return await _runs_out(db, rows)


@runs_router.post("/", response_model=ProductionRunOut, status_code=status.HTTP_201_CREATED, dependencies=OPERATOR)
async def start_run(body: ProductionRunCreate, user: CurrentUser, db: DB,
                    x_client_action_id: Annotated[Optional[str], Header()] = None):
    """Starts an order/lot. The machine's open run (if any) is closed at the new start. X-Client-Action-Id (operator
    tablet): a replay returns the run created the first time."""
    action = opscope.valid_action_id(x_client_action_id)
    prev = await opscope.replayed(db, user, action, "production_run.start")
    if prev is not None:
        return (await _runs_out(db, [await _get(db, ProductionRun, prev.entity_id, user.tenant_id,
                                                "Ordem de produção")]))[0]
    machine_id = await ensure_owned(db, Machine, body.machine_id, user.tenant_id, "machine_id")
    await opscope.check_machine(db, user, machine_id)
    product_id = await ensure_owned(db, Product, body.product_id, user.tenant_id, "product_id")
    started = _not_future(_aware(body.started_at) or datetime.now(timezone.utc), "started_at")
    ended = _aware(body.ended_at)
    if ended is not None and ended <= started:
        raise _422("ended_at deve ser após started_at.")
    if ended is None:
        cur = (await db.execute(select(ProductionRun).where(
            ProductionRun.tenant_id == user.tenant_id, ProductionRun.machine_id == machine_id,
            ProductionRun.ended_at.is_(None)).with_for_update())).scalar_one_or_none()
        if cur is not None:
            if started <= cur.started_at:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Já existe uma ordem aberta iniciada depois deste horário.")
            cur.ended_at = started
            await db.flush()
    row = ProductionRun(tenant_id=user.tenant_id, machine_id=machine_id, product_id=product_id,
                        started_at=started, ended_at=ended)
    db.add(row)
    try:
        await db.flush()
        opscope.remember(db, user, action, "production_run.start", row.id, 201)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if opscope.is_duplicate_key(e):
            prev = await opscope.replayed(db, user, action, "production_run.start")
            return (await _runs_out(db, [await _get(db, ProductionRun, prev.entity_id, user.tenant_id,
                                                    "Ordem de produção")]))[0]
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A máquina já tem uma ordem aberta.")
    await db.refresh(row)
    return (await _runs_out(db, [row]))[0]


@runs_router.get("/{run_id}", response_model=ProductionRunOut)
async def get_run(run_id: str, user: CurrentUser, db: DB):
    row = await _get(db, ProductionRun, run_id, user.tenant_id, "Ordem de produção")
    await opscope.check_machine(db, user, row.machine_id)
    return (await _runs_out(db, [row]))[0]


@runs_router.post("/{run_id}/stop", response_model=ProductionRunOut, dependencies=OPERATOR)
async def stop_run(run_id: str, user: CurrentUser, db: DB, body: Optional[ProductionRunStop] = None):
    row = await _get(db, ProductionRun, run_id, user.tenant_id, "Ordem de produção")
    await opscope.check_machine(db, user, row.machine_id)
    if row.ended_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A ordem já foi encerrada.")
    ended = _not_future(_aware(body.ended_at if body else None) or datetime.now(timezone.utc), "ended_at")
    if ended <= row.started_at:
        raise _422("ended_at deve ser após started_at.")
    row.ended_at = ended
    await db.commit()
    await db.refresh(row)
    return (await _runs_out(db, [row]))[0]


@runs_router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=ADMIN)
async def delete_run(run_id: str, user: CurrentUser, db: DB):
    row = await _get(db, ProductionRun, run_id, user.tenant_id, "Ordem de produção")
    await db.delete(row)
    await db.commit()


# ── reason codes ───────────────────────────────────────────────────────────

def _rc_out(r: ReasonCode) -> ReasonCodeOut:
    o = ReasonCodeOut.model_validate(r)
    o.is_default = r.tenant_id is None
    return o


@reasons_router.get("/", response_model=list[ReasonCodeOut])
async def list_reason_codes(user: CurrentUser, db: DB, include_inactive: bool = False):
    q = select(ReasonCode).where(or_(ReasonCode.tenant_id.is_(None), ReasonCode.tenant_id == user.tenant_id))
    if not include_inactive:
        q = q.where(ReasonCode.is_active.is_(True))
    rows = (await db.execute(q.order_by(ReasonCode.code))).scalars().all()
    return [_rc_out(r) for r in rows]


@reasons_router.post("/", response_model=ReasonCodeOut, status_code=status.HTTP_201_CREATED, dependencies=ADMIN)
async def create_reason_code(body: ReasonCodeCreate, user: CurrentUser, db: DB):
    clash = (await db.execute(select(ReasonCode.id).where(
        ReasonCode.code == body.code,
        or_(ReasonCode.tenant_id.is_(None), ReasonCode.tenant_id == user.tenant_id)))).scalar_one_or_none()
    if clash:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Código de motivo já existe.")
    row = ReasonCode(tenant_id=user.tenant_id, code=body.code, category=body.category, label=body.label,
                     planned=body.planned, oee_loss="none" if body.planned else body.oee_loss)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Código de motivo já existe.")
    await db.refresh(row)
    return _rc_out(row)


@reasons_router.patch("/{code_id}", response_model=ReasonCodeOut, dependencies=ADMIN)
async def update_reason_code(code_id: str, body: ReasonCodeUpdate, user: CurrentUser, db: DB):
    """Only the tenant's own codes are editable (the defaults are shared by every tenant)."""
    row = await _get(db, ReasonCode, code_id, user.tenant_id, "Motivo")
    apply_patch(row, body)
    if row.planned:
        row.oee_loss = "none"
    await db.commit()
    await db.refresh(row)
    return _rc_out(row)
