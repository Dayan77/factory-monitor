"""Report generation: scope + period resolution, content (builders via the API), rendering, versioned snapshot
(`report_runs`, docs/reports.md §0.4). All queries filter tenant_id."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import LineTarget, Plant, ReportFile, ReportRun, User
from ..oee.engine import load_calendar
from ..usage import queries as uq
from . import pdf, periods as P, xlsx
from .builders import BUILDERS, BuildCtx
from .catalog import BY_CODE, GENERATOR_VERSION
from .doc import Doc
from .source import ApiSource

CONTENT_TYPES = {"pdf": "application/pdf",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ScopeError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def profile_key(user: User, area: bool = True) -> str:
    """Permission profile of a viewer (§0.5): users with the same roles and lines get the same content."""
    roles = ",".join(sorted(user.role_set))
    lines = ",".join(sorted(str(x) for x in (user.line_ids or []))) if area else "*"
    return f"{roles}|{lines or '*'}"[:255]


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()[:60] or "relatorio"


async def resolve_scope(src: ApiSource, report_type: str, scope_type: Optional[str], scope_id: Optional[str]
                        ) -> tuple[str, Optional[str], Optional[dict], Optional[dict]]:
    """(scope_type, scope_id, plant tree entry, line tree entry). The tree comes from the API with the viewer's
    token — another tenant's id is simply not found (404, no existence leak)."""
    cat = BY_CODE[report_type]
    tree = await src.get_ok("/api/plants/tree")
    if scope_type is None and scope_id:
        # infer the kind of the id among the scopes the report accepts
        kinds = [k for k in cat["scope"] if k != "tenant"]
        for p in tree:
            if "plant" in kinds and p["id"] == scope_id:
                scope_type = "plant"
            elif "line" in kinds and any(ln["id"] == scope_id for ln in p.get("lines", [])):
                scope_type = "line"
        if scope_type is None:
            raise ScopeError(404, "Escopo não encontrado.")
    scope_type = scope_type or cat["scope"][0]
    if scope_type not in cat["scope"]:
        raise ScopeError(422, f"escopo '{scope_type}' inválido para {report_type}: {', '.join(cat['scope'])}")
    if scope_type == "tenant":
        return "tenant", None, None, None
    if not scope_id:
        if scope_type == "plant" and len(tree) == 1:
            return "plant", tree[0]["id"], tree[0], None
        raise ScopeError(422, "scope_id obrigatório")
    for p in tree:
        if scope_type == "plant" and p["id"] == scope_id:
            return "plant", p["id"], p, None
        for ln in p.get("lines", []):
            if scope_type == "line" and ln["id"] == scope_id:
                return "line", ln["id"], p, ln
    raise ScopeError(404, {"plant": "Planta", "line": "Linha"}.get(scope_type, "Escopo") + " não encontrada.")


async def line_targets(db: AsyncSession, tenant_id: str, line_ids: list[str], day: Optional[date]) -> dict:
    if not line_ids or day is None:
        return {}
    rows = (await db.execute(select(LineTarget).where(
        LineTarget.tenant_id == tenant_id, LineTarget.line_id.in_(line_ids), LineTarget.valid_from <= day)
        .order_by(LineTarget.valid_from))).scalars().all()
    out = {}
    for r in rows:                                  # latest valid_from wins
        out[r.line_id] = {"oee": r.oee, "parts_per_shift": r.parts_per_shift, "valid_from": r.valid_from.isoformat()}
    return out


async def build(db: AsyncSession, tenant_id: str, report_type: str, scope_type: Optional[str],
                scope_id: Optional[str], params: dict, viewer: User, now: datetime, *,
                period: Optional[P.Period] = None, app=None, observations: Optional[str] = None) -> tuple[Doc, ApiSource]:
    if report_type not in BUILDERS:
        raise ScopeError(422, f"relatório desconhecido: {report_type}")
    async with ApiSource(viewer, app=app) as src:
        scope_type, scope_id, plant, line = await resolve_scope(src, report_type, scope_type, scope_id)
        if plant is not None:
            tz = uq.tzinfo(plant["timezone"])
            cal = await load_calendar(db, tenant_id, plant["id"], tz)
        else:
            first = (await db.execute(select(Plant.timezone).where(Plant.tenant_id == tenant_id)
                                      .order_by(Plant.created_at).limit(1))).scalar_one_or_none()
            tz = uq.tzinfo(first)
            cal = None
        if period is None:
            try:
                if cal is None:
                    from ..oee.calendar import Calendar
                    cal_for_period = Calendar(tz, [])
                else:
                    cal_for_period = cal
                period = P.resolve_period(report_type, cal_for_period, params or {}, now)
            except (ValueError, TypeError) as e:
                raise ScopeError(422, f"período inválido: {e}") from None
        ids = [ln["id"] for ln in (plant or {}).get("lines", [])]
        targets = await line_targets(db, tenant_id, ids, period.first_day)
        ctx = BuildCtx(tenant_id=tenant_id, report_type=report_type, scope_type=scope_type, scope_id=scope_id,
                       period=period, tz=tz, cal=cal, now=now, viewer=viewer, params=params or {}, plant=plant,
                       line=line, targets=targets, app_url=settings.app_public_url, observations=observations)
        doc = await BUILDERS[report_type](src, ctx)
    doc.generated_at = now
    doc.data_until = min(period.end, now) if period.end else now
    doc.summary["_generator"] = GENERATOR_VERSION
    doc.summary["_scope"] = {"type": scope_type, "id": scope_id}
    doc.summary["_period"] = period.as_dict()
    return doc, src


def render(doc: Doc, fmt_: str) -> bytes:
    if fmt_ == "pdf":
        return pdf.render(doc)
    if fmt_ == "xlsx":
        return xlsx.render(doc)
    raise ScopeError(422, f"formato inválido: {fmt_}")


async def previous_run(db: AsyncSession, tenant_id: str, report_type: str, scope_id: Optional[str], period_key: str,
                       fmt_: str, profile: str) -> Optional[ReportRun]:
    q = select(ReportRun).where(ReportRun.tenant_id == tenant_id, ReportRun.report_type == report_type,
                                ReportRun.period_key == period_key, ReportRun.format == fmt_,
                                ReportRun.profile_key == profile, ReportRun.status == "done",
                                (ReportRun.scope_id == scope_id) if scope_id else ReportRun.scope_id.is_(None))
    return (await db.execute(q.order_by(ReportRun.version.desc()).limit(1))).scalar_one_or_none()


def supersedes_text(prev: Optional[ReportRun], tz) -> Optional[str]:
    if prev is None or prev.generated_at is None:
        return None
    return f"substitui a de {prev.generated_at.astimezone(tz):%d/%m %H:%M}"


async def store_run(db: AsyncSession, tenant_id: str, doc: Doc, fmt_: str, profile: str, viewer: User,
                    requested_by: Optional[str], schedule_id: Optional[str] = None,
                    dispatch_id: Optional[str] = None, params: Optional[dict] = None,
                    observations: Optional[str] = None) -> ReportRun:
    """Render + persist a new version (§0.4): the previous one stays available."""
    scope = doc.summary.get("_scope", {})
    per = doc.summary.get("_period", {})
    prev = await previous_run(db, tenant_id, doc.report_type, scope.get("id"), per.get("key"), fmt_, profile)
    from zoneinfo import ZoneInfo
    doc.version = (prev.version + 1) if prev else 1
    doc.supersedes_text = supersedes_text(prev, ZoneInfo(doc.tz))
    content = render(doc, fmt_)
    cat = BY_CODE[doc.report_type]
    fname = (f"{_slug(cat['name'])}_{_slug(doc.scope_label)}_{_slug(per.get('key') or '')}_v{doc.version}.{fmt_}")
    run = ReportRun(tenant_id=tenant_id, schedule_id=schedule_id, dispatch_id=dispatch_id,
                    report_type=doc.report_type, scope_type=scope.get("type") or "tenant", scope_id=scope.get("id"),
                    scope_name=doc.scope_label[:255], period_key=per.get("key") or "", period_label=doc.period_label,
                    period_start=_ts(per.get("start")), period_end=_ts(per.get("end")), params=params or {},
                    format=fmt_, version=doc.version, supersedes_id=prev.id if prev else None, profile_key=profile,
                    viewer_user_id=viewer.id, requested_by=requested_by, status="done", generated_at=doc.generated_at,
                    data_until=doc.data_until, generator_version=GENERATOR_VERSION,
                    file_sha256=hashlib.sha256(content).hexdigest(), file_size=len(content), filename=fname,
                    content_type=CONTENT_TYPES[fmt_], observations=observations,
                    summary=_jsonable({"numbers": {k: v for k, v in doc.summary.items() if not k.startswith("_")},
                                       "headline": doc.headline, "banners": doc.banners, "omitted": doc.omitted}))
    db.add(run)
    await db.flush()
    db.add(ReportFile(run_id=run.id, tenant_id=tenant_id, content=content))
    await db.flush()
    return run


def _ts(s) -> Optional[datetime]:
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v != v:
        return None
    return v


def run_out(r: ReportRun) -> dict:
    return {"id": r.id, "schedule_id": r.schedule_id, "dispatch_id": r.dispatch_id, "report_type": r.report_type,
            "scope_type": r.scope_type, "scope_id": r.scope_id, "scope_name": r.scope_name,
            "period_key": r.period_key, "period_label": r.period_label,
            "period_start": _iso(r.period_start), "period_end": _iso(r.period_end), "params": r.params,
            "format": r.format, "version": r.version, "supersedes_id": r.supersedes_id, "status": r.status,
            "error": r.error, "generated_at": _iso(r.generated_at), "data_until": _iso(r.data_until),
            "generator_version": r.generator_version, "file_sha256": r.file_sha256, "file_size": r.file_size,
            "filename": r.filename, "content_type": r.content_type, "requested_by": r.requested_by,
            "observations": r.observations, "summary": r.summary, "created_at": _iso(r.created_at),
            "download_url": f"/api/report-runs/{r.id}/download" if r.status == "done" else None}


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if t else None
