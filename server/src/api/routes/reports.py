"""
Reports API (Sprint 6 — docs/reports.md).

Routers (mounted in main.py):
  router            /api/reports            /catalog, /status, /generate, /reliability-life, /unsubscribe,
                                            /settings, /external-recipients, /line-targets
  runs_router       /api/report-runs        list, detail, /{id}/download
  schedules_router  /api/report-schedules   CRUD, /pause, /resume, /send-now, /history

Every number of a report comes from the API itself (services/reports/source.py) with the viewer's permissions;
every query filters tenant_id; ids of another tenant answer 404.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, has_any, require_any
from ...db import get_db
from ...models import (Line, Plant, ReportDelivery, ReportDispatch, ReportExternalRecipient, ReportFile, ReportRun,
                       ReportSchedule, ReportUnsubscribe, User, UserRole, LineTarget, Shift, ComponentType)
from ...services import clock
from ...services.mailer import mail_channel
from ...services.reports import catalog as cat, delivery as dl, engine as eng, life, periods as P
from ...services.reports.service import ScopeError, build, profile_key, run_out, store_run
from ...services.reports.source import Forbidden, ReportDataError
from ...services.validation import is_uuid
from ..tenant_refs import ensure_owned

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]

KPI_READ = ("maintenance_planner", "manager", "production_supervisor")
MANAGE = ("manager",)                      # schedules: manager (admin/superadmin always pass)
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ROLE_VALUES = {r.value for r in UserRole} - {"superadmin"}

router = APIRouter()
runs_router = APIRouter()
schedules_router = APIRouter()

_jobs: set[asyncio.Task] = set()


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} não encontrado(a).")


def _scope_error(e: ScopeError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ══ catalog / status ═════════════════════════════════════════════════════

@router.get("/catalog")
async def catalog(user: CurrentUser):
    """§1: the 5 reports with formats, period, default schedule and recipients (block `report-catalog`)."""
    return [{**cat.public_entry(c), "allowed": cat.can_view(user, c["code"])} for c in cat.CATALOG]


@router.get("/status")
async def reports_status(user: CurrentUser):
    """Mail channel of the scheduled reports — the admin sees the warning "e-mail não configurado" (§5)."""
    ch = mail_channel()
    return {"mail_channel": ch, "mail_configured": ch is not None,
            "warning": None if ch else "e-mail não configurado: os relatórios agendados são gerados e ficam só "
                                       "para download"}


# ══ on-demand generation ═════════════════════════════════════════════════

class GenerateIn(BaseModel):
    code: Optional[str] = None
    report_type: Optional[str] = None
    scope_type: Optional[Literal["tenant", "plant", "line"]] = None
    scope_id: Optional[str] = None
    format: Literal["pdf", "xlsx", "json"] = "pdf"
    # period params: productive_day (daily/shift), shift_id (shift), week_start (weekly), month "YYYY-MM"
    # (monthly), cutoff_date (reliability_life); component_type (reliability_life)
    params: dict = Field(default_factory=dict)
    observations: Optional[str] = Field(default=None, max_length=4000)
    mode: Literal["file", "job"] = "file"

    @property
    def type_(self) -> str:
        return self.code or self.report_type or ""


def _file_response(run: ReportRun, content: bytes) -> Response:
    return Response(content=content, media_type=run.content_type, headers={
        "Content-Disposition": f'attachment; filename="{run.filename}"', "X-Report-Run-Id": run.id,
        "X-Report-Version": str(run.version), "X-Report-Sha256": run.file_sha256 or ""})


async def _generate(db: AsyncSession, user: User, body: GenerateIn, app, now: datetime):
    doc, src = await build(db, user.tenant_id, body.type_, body.scope_type, body.scope_id, body.params, user, now,
                           app=app, observations=body.observations)
    return doc, src


@router.post("/generate")
async def generate(body: GenerateIn, request: Request, user: CurrentUser, db: DB):
    """Builds the report with the caller's permissions. format=json → the document (preview + the API calls the
    numbers came from); pdf/xlsx → the file (a new version in report_runs); mode=job → 202 + run id, download
    from GET /api/report-runs/{id}/download when status = done."""
    t = body.type_
    if t not in cat.BY_CODE:
        raise HTTPException(status_code=422, detail=f"relatório desconhecido: {t or '—'}")
    if not cat.can_view(user, t):
        raise HTTPException(status_code=403, detail="Permissão insuficiente para este relatório.")
    if body.format != "json" and body.format not in cat.BY_CODE[t]["formats"]:
        raise HTTPException(status_code=422, detail=f"{t} não tem o formato {body.format}")
    if body.scope_id is not None and not is_uuid(body.scope_id):
        raise _404("Escopo")
    if body.format != "json":                                 # Sprint 7: exports are audited
        from ...services.security.notify import audit
        await audit(db, user.tenant_id, user.id, "report.exported", "report", t,
                    {"format": body.format, "api_token": bool(getattr(user, "api_token_id", None))})
    now = clock.now()
    app = request.app
    if body.mode == "job" and body.format != "json":
        run = ReportRun(tenant_id=user.tenant_id, report_type=t, scope_type=body.scope_type or cat.BY_CODE[t]["scope"][0],
                        scope_id=body.scope_id, period_key="pending", format=body.format, status="generating",
                        requested_by=user.id, viewer_user_id=user.id, params=body.params,
                        profile_key=profile_key(user), observations=body.observations)
        db.add(run)
        await db.commit()
        task = asyncio.create_task(_job(run.id, user.id, body, app, now))
        _jobs.add(task)
        task.add_done_callback(_jobs.discard)
        return JSONResponse(status_code=202, content={"run_id": run.id, "status": "generating",
                                                      "status_url": f"/api/report-runs/{run.id}",
                                                      "download_url": f"/api/report-runs/{run.id}/download"})
    try:
        doc, src = await _generate(db, user, body, app, now)
    except ScopeError as e:
        raise _scope_error(e) from None
    except Forbidden as e:
        raise HTTPException(status_code=403, detail=f"Permissão insuficiente ({e}).") from None
    except ReportDataError as e:
        raise HTTPException(status_code=502, detail=str(e)[:300]) from None
    if body.format == "json":
        out = doc.to_json()
        out["api_calls"] = src.calls
        return out
    run = await store_run(db, user.tenant_id, doc, body.format, profile_key(user), user, requested_by=user.id,
                          params=body.params, observations=body.observations)
    await db.commit()
    content = (await db.get(ReportFile, run.id)).content
    return _file_response(run, content)


async def _job(run_id: str, user_id: str, body: GenerateIn, app, now: datetime) -> None:
    from ...db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        placeholder = await db.get(ReportRun, run_id)
        try:
            doc, _ = await _generate(db, user, body, app, now)
            run = await store_run(db, user.tenant_id, doc, body.format, profile_key(user), user,
                                  requested_by=user.id, params=body.params, observations=body.observations)
            # the placeholder takes the generated version's data (the client polls the id it received)
            content = (await db.get(ReportFile, run.id)).content
            for k in ("scope_type", "scope_id", "scope_name", "period_key", "period_start", "period_end",
                      "period_label", "version", "supersedes_id", "generated_at", "data_until", "generator_version",
                      "file_sha256", "file_size", "filename", "content_type", "summary"):
                setattr(placeholder, k, getattr(run, k))
            placeholder.status = "done"
            await db.delete(await db.get(ReportFile, run.id))
            await db.flush()
            await db.delete(run)
            await db.flush()
            db.add(ReportFile(run_id=placeholder.id, tenant_id=user.tenant_id, content=content))
            await db.commit()
        except Exception as e:  # noqa: BLE001 — the job reports its own failure
            await db.rollback()
            placeholder = await db.get(ReportRun, run_id)
            placeholder.status = "failed"
            placeholder.error = (e.detail if isinstance(e, ScopeError) else f"{type(e).__name__}: {e}")[:1000]
            await db.commit()


# ══ Weibull life analysis (the API behind reliability_life) ═════════════

@router.get("/reliability-life", dependencies=[Depends(require_any(*KPI_READ))])
async def reliability_life(user: CurrentUser, db: DB, plant_id: Optional[str] = None,
                           cutoff: Optional[datetime] = None, component_type: Optional[str] = None):
    """Weibull 2P with right censoring by (component type, manufacturer, model) — reports.md §2."""
    if plant_id:
        await ensure_owned(db, Plant, plant_id, user.tenant_id, "plant_id")
    if component_type is not None and component_type not in {c.value for c in ComponentType}:
        raise HTTPException(status_code=422, detail=f"component_type inválido: {component_type}")
    now = clock.now()
    cut = _aware(cutoff) or now
    if cut > now:
        cut = now
    return await life.reliability_life(db, user.tenant_id, cut, plant_id, component_type)


# ══ unsubscribe (public, signed token) ══════════════════════════════════

async def _unsubscribe(db: AsyncSession, token: str) -> Optional[dict]:
    p = dl.read_unsubscribe_token(token)
    if p is None:
        return None
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    sid = p.get("s")
    if sid is not None:
        exists = (await db.execute(select(ReportSchedule.id).where(ReportSchedule.id == sid,
                                                                   ReportSchedule.tenant_id == p["t"]))).first()
        if exists is None:
            sid = None
    await db.execute(pg_insert(ReportUnsubscribe).values(tenant_id=p["t"], email=p["e"], schedule_id=sid)
                     .on_conflict_do_nothing())
    await db.commit()
    return p


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(db: DB, token: str = Query(..., min_length=10, max_length=2000)):
    """'Não quero mais receber' (one click, LGPD): signed link of every report e-mail; no login."""
    p = await _unsubscribe(db, token)
    if p is None:
        return HTMLResponse("<html><body style='font-family:sans-serif'><h3>Link inválido.</h3></body></html>",
                            status_code=400)
    return HTMLResponse("<html><head><meta name='robots' content='noindex'></head><body style='font-family:sans-serif'>"
                        "<h3>Descadastro confirmado</h3><p>Você não receberá mais este relatório do FactoryConnect "
                        "por e-mail.</p></body></html>")


@router.post("/unsubscribe")
async def unsubscribe_post(db: DB, token: str = Query(..., min_length=10, max_length=2000)):
    """RFC 8058 List-Unsubscribe-Post (one-click from the mail client)."""
    if await _unsubscribe(db, token) is None:
        raise HTTPException(status_code=400, detail="Link inválido.")
    return {"unsubscribed": True}


# ══ settings, external recipients, line targets ═════════════════════════

class SettingsIn(BaseModel):
    external_recipients_enabled: bool


@router.get("/settings", dependencies=[Depends(require_any("admin"))])
async def get_report_settings(user: CurrentUser, db: DB):
    s = await dl.settings_for(db, user.tenant_id)
    await db.commit()
    ch = mail_channel()
    return {"external_recipients_enabled": s.external_recipients_enabled, "mail_configured": ch is not None,
            "mail_channel": ch}


@router.put("/settings", dependencies=[Depends(require_any("admin"))])
async def put_report_settings(body: SettingsIn, user: CurrentUser, db: DB):
    s = await dl.settings_for(db, user.tenant_id)
    s.external_recipients_enabled = body.external_recipients_enabled
    await db.commit()
    return {"external_recipients_enabled": s.external_recipients_enabled}


class ExternalIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=3, max_length=500)
    legal_basis: Literal["consent", "contract", "legitimate_interest", "legal_obligation"]


def _ext_out(e: ReportExternalRecipient) -> dict:
    return {"id": e.id, "email": e.email, "name": e.name, "purpose": e.purpose, "legal_basis": e.legal_basis,
            "bounce_count": e.bounce_count, "disabled_at": clock.iso(e.disabled_at)}


@router.get("/external-recipients", dependencies=[Depends(require_any("admin"))])
async def list_external(user: CurrentUser, db: DB):
    rows = (await db.execute(select(ReportExternalRecipient).where(
        ReportExternalRecipient.tenant_id == user.tenant_id).order_by(ReportExternalRecipient.name))).scalars().all()
    return [_ext_out(e) for e in rows]


@router.post("/external-recipients", status_code=201, dependencies=[Depends(require_any("admin"))])
async def create_external(body: ExternalIn, user: CurrentUser, db: DB):
    s = await dl.settings_for(db, user.tenant_id)
    if not s.external_recipients_enabled:
        raise HTTPException(status_code=409, detail="Destinatários externos desabilitados (reports_external_recipients).")
    clash = (await db.execute(select(ReportExternalRecipient.id).where(
        ReportExternalRecipient.tenant_id == user.tenant_id,
        ReportExternalRecipient.email == body.email.lower()))).first()
    if clash:
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    e = ReportExternalRecipient(tenant_id=user.tenant_id, email=body.email.lower(), name=body.name,
                                purpose=body.purpose, legal_basis=body.legal_basis, created_by=user.id)
    db.add(e)
    await db.commit()
    return _ext_out(e)


@router.delete("/external-recipients/{rid}", status_code=204, dependencies=[Depends(require_any("admin"))])
async def delete_external(rid: str, user: CurrentUser, db: DB):
    if not is_uuid(rid):
        raise _404("Destinatário")
    e = (await db.execute(select(ReportExternalRecipient).where(
        ReportExternalRecipient.id == rid, ReportExternalRecipient.tenant_id == user.tenant_id))).scalar_one_or_none()
    if e is None:
        raise _404("Destinatário")
    await db.delete(e)
    await db.commit()


class TargetIn(BaseModel):
    line_id: str
    oee: Optional[float] = Field(default=None, gt=0, le=1)
    parts_per_shift: Optional[float] = Field(default=None, gt=0)
    valid_from: date


@router.get("/line-targets")
async def list_targets(user: CurrentUser, db: DB, line_id: Optional[str] = None):
    q = select(LineTarget).where(LineTarget.tenant_id == user.tenant_id)
    if line_id:
        if not is_uuid(line_id):
            return []
        q = q.where(LineTarget.line_id == line_id)
    rows = (await db.execute(q.order_by(LineTarget.line_id, LineTarget.valid_from))).scalars().all()
    return [{"id": r.id, "line_id": r.line_id, "oee": r.oee, "parts_per_shift": r.parts_per_shift,
             "valid_from": r.valid_from.isoformat()} for r in rows]


@router.put("/line-targets", dependencies=[Depends(require_any(*MANAGE))])
async def put_target(body: TargetIn, user: CurrentUser, db: DB):
    """Meta por linha (§1.1), vigente a partir de valid_from; sem meta a coluna não aparece no relatório."""
    lid = await ensure_owned(db, Line, body.line_id, user.tenant_id, "line_id")
    if body.oee is None and body.parts_per_shift is None:
        raise HTTPException(status_code=422, detail="informe oee e/ou parts_per_shift")
    r = (await db.execute(select(LineTarget).where(LineTarget.tenant_id == user.tenant_id, LineTarget.line_id == lid,
                                                   LineTarget.valid_from == body.valid_from))).scalar_one_or_none()
    if r is None:
        r = LineTarget(tenant_id=user.tenant_id, line_id=lid, valid_from=body.valid_from, created_by=user.id)
        db.add(r)
    r.oee, r.parts_per_shift = body.oee, body.parts_per_shift
    await db.commit()
    return {"id": r.id, "line_id": r.line_id, "oee": r.oee, "parts_per_shift": r.parts_per_shift,
            "valid_from": r.valid_from.isoformat()}


# ══ runs ═════════════════════════════════════════════════════════════════

def _may_download(user: User, r: ReportRun) -> bool:
    """A file carries the permissions of the profile it was generated with (§0.5)."""
    if not cat.can_view(user, r.report_type):
        return False
    if has_any(user, ("admin",)):
        return True
    return r.viewer_user_id == user.id or r.requested_by == user.id or r.profile_key == profile_key(user) \
        or r.profile_key == profile_key(user, False)


async def _get_run(db: AsyncSession, run_id: str, user: User) -> ReportRun:
    if not is_uuid(run_id):
        raise _404("Relatório")
    r = (await db.execute(select(ReportRun).where(ReportRun.id == run_id, ReportRun.tenant_id == user.tenant_id))
         ).scalar_one_or_none()
    if r is None or not _may_download(user, r):
        raise _404("Relatório")
    return r


@runs_router.get("/")
async def list_runs(user: CurrentUser, db: DB, report_type: Optional[str] = None, schedule_id: Optional[str] = None,
                    period_key: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    q = select(ReportRun).where(ReportRun.tenant_id == user.tenant_id)
    if report_type:
        q = q.where(ReportRun.report_type == report_type)
    if schedule_id:
        if not is_uuid(schedule_id):
            return []
        q = q.where(ReportRun.schedule_id == schedule_id)
    if period_key:
        q = q.where(ReportRun.period_key == period_key)
    rows = (await db.execute(q.order_by(ReportRun.created_at.desc()).limit(limit * 3))).scalars().all()
    return [run_out(r) for r in rows if _may_download(user, r)][:limit]


@runs_router.get("/{run_id}")
async def get_run(run_id: str, user: CurrentUser, db: DB):
    return run_out(await _get_run(db, run_id, user))


@runs_router.get("/{run_id}/download")
async def download_run(run_id: str, user: CurrentUser, db: DB, link: Optional[str] = None):
    """The file of one version. `link` = signed link of an e-mail above 10 MB (expires in 7 days; login required)."""
    r = await _get_run(db, run_id, user)
    if link is not None:
        p = dl.read_link_token(link)
        if p is None or p.get("r") != r.id or p.get("t") != user.tenant_id:
            raise HTTPException(status_code=410, detail="Link expirado ou inválido: baixe pelo app.")
    if r.status != "done":
        raise HTTPException(status_code=409, detail=f"Relatório ainda não disponível (status: {r.status}).")
    f = await db.get(ReportFile, r.id)
    if f is None or f.tenant_id != user.tenant_id:
        raise _404("Arquivo")
    from ...services.security.notify import audit            # Sprint 7: exports are audited
    await audit(db, user.tenant_id, user.id,
                "report.downloaded", "report_run", r.id, {"via_link": link is not None,
                                                          "api_token": bool(getattr(user, "api_token_id", None))})
    await db.commit()
    return _file_response(r, f.content)


# ══ schedules ════════════════════════════════════════════════════════════

class RecipientsIn(BaseModel):
    roles: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    external_ids: list[str] = Field(default_factory=list)


class ScheduleIn(BaseModel):
    report_type: str
    name: Optional[str] = Field(default=None, max_length=255)
    scope_type: Optional[Literal["tenant", "plant", "line"]] = None
    scope_id: Optional[str] = None
    params: dict = Field(default_factory=dict)
    formats: Optional[list[Literal["pdf", "xlsx"]]] = None
    deliver_at_local: Optional[str] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    month_day: Optional[int] = Field(default=None, ge=1, le=28)
    enabled: bool = True
    recipients: Optional[RecipientsIn] = None

    @field_validator("deliver_at_local")
    @classmethod
    def _hhmm(cls, v):
        if v is not None and not HHMM.match(v):
            raise ValueError("deliver_at_local: HH:MM")
        return v


class SchedulePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    params: Optional[dict] = None
    formats: Optional[list[Literal["pdf", "xlsx"]]] = None
    deliver_at_local: Optional[str] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    month_day: Optional[int] = Field(default=None, ge=1, le=28)
    enabled: Optional[bool] = None
    recipients: Optional[RecipientsIn] = None

    @field_validator("deliver_at_local")
    @classmethod
    def _hhmm(cls, v):
        if v is not None and not HHMM.match(v):
            raise ValueError("deliver_at_local: HH:MM")
        return v


async def _check_scope(db: AsyncSession, tenant_id: str, report_type: str, scope_type: Optional[str],
                       scope_id: Optional[str]) -> tuple[str, Optional[str]]:
    c = cat.BY_CODE[report_type]
    scope_type = scope_type or c["scope"][0]
    if scope_type not in c["scope"]:
        raise HTTPException(status_code=422, detail=f"escopo inválido para {report_type}: {', '.join(c['scope'])}")
    if scope_type == "tenant":
        return scope_type, None
    if not scope_id:
        raise HTTPException(status_code=422, detail="scope_id obrigatório")
    model = Plant if scope_type == "plant" else Line
    return scope_type, await ensure_owned(db, model, scope_id, tenant_id, "scope_id")


async def _check_recipients(db: AsyncSession, tenant_id: str, rec: RecipientsIn) -> dict:
    bad = set(rec.roles) - ROLE_VALUES
    if bad:
        raise HTTPException(status_code=422, detail=f"papéis inválidos: {sorted(bad)}")
    uids = []
    for u in rec.user_ids:
        uids.append(await ensure_owned(db, User, u, tenant_id, "user_id"))
    eids = []
    if rec.external_ids:
        s = await dl.settings_for(db, tenant_id)
        if not s.external_recipients_enabled:
            raise HTTPException(status_code=409, detail="Destinatários externos desabilitados (reports_external_recipients).")
        for e in rec.external_ids:
            eids.append(await ensure_owned(db, ReportExternalRecipient, e, tenant_id, "external_id"))
    return {"roles": sorted(set(rec.roles)), "user_ids": uids, "external_ids": eids}


async def _check_params(db: AsyncSession, tenant_id: str, report_type: str, scope_type: str, scope_id: Optional[str],
                        params: dict) -> dict:
    out = {}
    if report_type == "shift" and params.get("shift_id"):
        sid = await ensure_owned(db, Shift, params["shift_id"], tenant_id, "shift_id")
        plant_of_line = (await db.execute(select(Line.plant_id).where(Line.id == scope_id))).scalar_one()
        sp = (await db.execute(select(Shift.plant_id).where(Shift.id == sid))).scalar_one()
        if sp != plant_of_line:
            raise HTTPException(status_code=422, detail="shift_id de outra planta")
        out["shift_id"] = sid
    if report_type == "reliability_life" and params.get("component_type"):
        out["component_type"] = str(params["component_type"])[:32]
    if params.get("observations"):
        out["observations"] = str(params["observations"])[:4000]
    return out


async def _schedule_out(db: AsyncSession, s: ReportSchedule, now: Optional[datetime] = None) -> dict:
    now = now or clock.now()
    nxt = None
    try:
        cal, _, _ = await eng.schedule_calendar(db, s)
        nxt = _next_send(s, cal, now)
    except ScopeError:
        pass
    ch = mail_channel()
    return {"id": s.id, "name": s.name, "report_type": s.report_type, "report_name": cat.BY_CODE[s.report_type]["name"],
            "scope_type": s.scope_type, "scope_id": s.scope_id, "params": s.params, "formats": s.formats,
            "deliver_at_local": s.deliver_at_local, "weekday": s.weekday, "month_day": s.month_day,
            "enabled": s.enabled, "paused_at": clock.iso(s.paused_at), "recipients": s.recipients,
            "created_by": s.created_by, "created_at": clock.iso(s.created_at), "updated_at": clock.iso(s.updated_at),
            "next_send_at": clock.iso(nxt), "mail_configured": ch is not None,
            "warning": None if ch else "e-mail não configurado: os relatórios serão gerados e ficarão só para download"}


def _next_send(s: ReportSchedule, cal, now: datetime) -> Optional[datetime]:
    """First send_at after now (look-ahead of the current/next period)."""
    best = None
    for k in (0, 1, 7, 31, 92):
        for per in P.candidate_periods(s.report_type, cal, now + timedelta(days=k + 1), lookback_days=2,
                                       shift_id=(s.params or {}).get("shift_id")):
            snd = P.send_at(per, s.report_type, cal.tz, s.deliver_at_local or cat.BY_CODE[s.report_type]["deliver_at_local"])
            if snd > now and (best is None or snd < best):
                best = snd
        if best is not None:
            return best
    return best


async def _get_schedule(db: AsyncSession, sid: str, user: User, lock: bool = False) -> ReportSchedule:
    if not is_uuid(sid):
        raise _404("Agendamento")
    q = select(ReportSchedule).where(ReportSchedule.id == sid, ReportSchedule.tenant_id == user.tenant_id)
    if lock:
        q = q.with_for_update()
    s = (await db.execute(q)).scalar_one_or_none()
    if s is None:
        raise _404("Agendamento")
    return s


@schedules_router.get("/", dependencies=[Depends(require_any(*MANAGE))])
async def list_schedules(user: CurrentUser, db: DB, report_type: Optional[str] = None):
    q = select(ReportSchedule).where(ReportSchedule.tenant_id == user.tenant_id)
    if report_type:
        q = q.where(ReportSchedule.report_type == report_type)
    rows = (await db.execute(q.order_by(ReportSchedule.created_at))).scalars().all()
    return [await _schedule_out(db, s) for s in rows]


@schedules_router.post("/", status_code=201, dependencies=[Depends(require_any(*MANAGE))])
async def create_schedule(body: ScheduleIn, user: CurrentUser, db: DB):
    if body.report_type not in cat.BY_CODE:
        raise HTTPException(status_code=422, detail=f"relatório desconhecido: {body.report_type}")
    c = cat.BY_CODE[body.report_type]
    scope_type, scope_id = await _check_scope(db, user.tenant_id, body.report_type, body.scope_type, body.scope_id)
    formats = body.formats or list(c["formats"])
    bad = set(formats) - set(c["formats"])
    if bad:
        raise HTTPException(status_code=422, detail=f"{body.report_type} não tem o formato {sorted(bad)}")
    recipients = (await _check_recipients(db, user.tenant_id, body.recipients) if body.recipients is not None
                  else dl.default_recipients(body.report_type))
    s = ReportSchedule(
        tenant_id=user.tenant_id, name=body.name or c["name"], report_type=body.report_type, scope_type=scope_type,
        scope_id=scope_id, params=await _check_params(db, user.tenant_id, body.report_type, scope_type, scope_id,
                                                      body.params),
        formats=sorted(set(formats), key=["pdf", "xlsx"].index),
        deliver_at_local=None if body.report_type == "shift" else (body.deliver_at_local or c["deliver_at_local"]),
        weekday=(body.weekday if body.weekday is not None else c["weekday"]) if body.report_type == "weekly_oee" else None,
        month_day=(body.month_day or c["month_day"]) if body.report_type == "monthly_maintenance" else None,
        enabled=body.enabled, recipients=recipients, created_by=user.id)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return await _schedule_out(db, s)


@schedules_router.get("/{sid}", dependencies=[Depends(require_any(*MANAGE))])
async def get_schedule(sid: str, user: CurrentUser, db: DB):
    return await _schedule_out(db, await _get_schedule(db, sid, user))


@schedules_router.patch("/{sid}", dependencies=[Depends(require_any(*MANAGE))])
async def patch_schedule(sid: str, body: SchedulePatch, user: CurrentUser, db: DB):
    s = await _get_schedule(db, sid, user, lock=True)
    c = cat.BY_CODE[s.report_type]
    sent = body.model_fields_set
    if "name" in sent and body.name:
        s.name = body.name
    if "params" in sent and body.params is not None:
        s.params = await _check_params(db, user.tenant_id, s.report_type, s.scope_type, s.scope_id, body.params)
    if "formats" in sent and body.formats:
        bad = set(body.formats) - set(c["formats"])
        if bad:
            raise HTTPException(status_code=422, detail=f"{s.report_type} não tem o formato {sorted(bad)}")
        s.formats = sorted(set(body.formats), key=["pdf", "xlsx"].index)
    if "deliver_at_local" in sent and s.report_type != "shift" and body.deliver_at_local:
        s.deliver_at_local = body.deliver_at_local
    if "weekday" in sent and s.report_type == "weekly_oee" and body.weekday is not None:
        s.weekday = body.weekday
    if "month_day" in sent and s.report_type == "monthly_maintenance" and body.month_day is not None:
        s.month_day = body.month_day
    if "recipients" in sent and body.recipients is not None:
        s.recipients = await _check_recipients(db, user.tenant_id, body.recipients)
    if "enabled" in sent and body.enabled is not None:
        s.enabled = body.enabled
        s.paused_at = None if body.enabled else clock.now()
    await db.commit()
    await db.refresh(s)
    return await _schedule_out(db, s)


@schedules_router.delete("/{sid}", status_code=204, dependencies=[Depends(require_any(*MANAGE))])
async def delete_schedule(sid: str, user: CurrentUser, db: DB):
    s = await _get_schedule(db, sid, user)
    await db.delete(s)
    await db.commit()


@schedules_router.post("/{sid}/pause", dependencies=[Depends(require_any(*MANAGE))])
async def pause_schedule(sid: str, user: CurrentUser, db: DB):
    s = await _get_schedule(db, sid, user, lock=True)
    s.enabled, s.paused_at = False, clock.now()
    await db.commit()
    await db.refresh(s)
    return await _schedule_out(db, s)


@schedules_router.post("/{sid}/resume", dependencies=[Depends(require_any(*MANAGE))])
async def resume_schedule(sid: str, user: CurrentUser, db: DB):
    s = await _get_schedule(db, sid, user, lock=True)
    s.enabled, s.paused_at = True, None
    await db.commit()
    await db.refresh(s)
    return await _schedule_out(db, s)


@schedules_router.post("/{sid}/send-now", dependencies=[Depends(require_any(*MANAGE))])
async def send_now(sid: str, request: Request, user: CurrentUser, db: DB):
    """Generates and sends now the last **closed** period (never one whose data are not ready: ready_at ≤ now).
    It does not consume the scheduled slot of that period."""
    s = await _get_schedule(db, sid, user)
    now = clock.now()
    try:
        cal, _, _ = await eng.schedule_calendar(db, s)
    except ScopeError as e:
        raise _scope_error(e) from None
    ready = [(per, P.ready_at(per, s.report_type)) for per in
             P.candidate_periods(s.report_type, cal, now, lookback_days=8, shift_id=(s.params or {}).get("shift_id"))]
    ready = [(per, r) for per, r in ready if r <= now]
    if not ready:
        raise HTTPException(status_code=409, detail="Nenhum período fechado (ready_at) para enviar ainda.")
    per, r = max(ready, key=lambda x: x[1])
    d = ReportDispatch(tenant_id=user.tenant_id, schedule_id=s.id, period_key=f"manual:{uuid.uuid4().hex[:12]}:{per.key}",
                       period=per.as_dict(), ready_at=r, send_at=now, status="pending", attempts=1,
                       next_attempt_at=now + timedelta(minutes=eng.LEASE_MIN), manual=True, requested_by=user.id)
    db.add(d)
    await db.commit()
    status_ = await eng.process(db, d.id, now, app=request.app)
    return {"dispatch_id": d.id, "status": status_, "period_key": per.key, "period_label": per.label}


@schedules_router.get("/{sid}/history", dependencies=[Depends(require_any(*MANAGE))])
async def schedule_history(sid: str, user: CurrentUser, db: DB, limit: int = Query(50, ge=1, le=500)):
    """Dispatched periods (status, attempts, errors), their versions (report_runs) and the sends (report_deliveries)."""
    s = await _get_schedule(db, sid, user)
    ds = (await db.execute(select(ReportDispatch).where(ReportDispatch.schedule_id == s.id,
                                                        ReportDispatch.tenant_id == user.tenant_id)
                           .order_by(ReportDispatch.send_at.desc()).limit(limit))).scalars().all()
    runs = (await db.execute(select(ReportRun).where(ReportRun.schedule_id == s.id,
                                                     ReportRun.tenant_id == user.tenant_id)
                             .order_by(ReportRun.created_at.desc()).limit(limit * 6))).scalars().all()
    dels = (await db.execute(select(ReportDelivery).where(ReportDelivery.schedule_id == s.id,
                                                          ReportDelivery.tenant_id == user.tenant_id)
                             .order_by(ReportDelivery.created_at.desc()).limit(limit * 20))).scalars().all()
    return {
        "schedule": await _schedule_out(db, s),
        "dispatches": [{"id": d.id, "period_key": d.period_key, "period_label": (d.period or {}).get("label"),
                        "ready_at": clock.iso(d.ready_at), "send_at": clock.iso(d.send_at), "status": d.status,
                        "attempts": d.attempts, "last_error": d.last_error, "manual": d.manual,
                        "completed_at": clock.iso(d.completed_at)} for d in ds],
        "runs": [run_out(r) for r in runs],
        "deliveries": [{"id": x.id, "dispatch_id": x.dispatch_id, "email": x.email, "status": x.status,
                        "channel": x.channel, "provider_id": x.provider_id, "link_only": x.link_only,
                        "error": x.error, "run_ids": x.run_ids, "sent_at": clock.iso(x.sent_at),
                        "created_at": clock.iso(x.created_at)} for x in dels],
    }
