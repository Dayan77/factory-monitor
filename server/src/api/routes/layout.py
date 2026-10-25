"""
Plant map (Sprint 6 — docs/sprint6.md §1): floor-plan layouts with markers and the live state of every machine.

  GET    /api/plants/{plant_id}/layouts            versions of the plant (any role)
  POST   /api/plants/{plant_id}/layouts            new draft (copy of the published one, version + 1) (manager+)
  GET    /api/plants/{plant_id}/layout/published   published layout + markers + visual state per machine (§1.2)
  GET    /api/plants/{plant_id}/layout/auto        automatic layout (§1.5), same state rules
  GET    /api/layouts/{layout_id}                  layout + markers (any role)
  PATCH  /api/layouts/{layout_id}                  scale_m_per_px (draft only, manager+)
  POST   /api/layouts/{layout_id}/image            multipart `file`: PNG/JPEG/SVG ≤ 10 MB, side ≤ 8 000 px (manager+)
  GET    /api/layouts/{layout_id}/image            the image (JWT in Authorization or ?token=), Cache-Control
  PUT    /api/layouts/{layout_id}/markers          replaces every marker of a draft (manager+)
  POST   /api/layouts/{layout_id}/publish          draft → published, previous → archived; audited (manager+)

Rules: a machine at most once per layout (409); machine of another plant → 422; coordinates outside [0, 1] /
rotation outside [0, 360) → 422; layout or plant of another tenant → 404 (never leaks existence). Editors:
admin and manager. Viewers: every role of the tenant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import LayoutMarker, Line, Machine, Plant, PlantLayout, User, UserRole
from ...services import audit
from ...services import operator_scope as opscope
from ...services.layout import images
from ...services.layout.service import auto_view, layout_out, published_view
from ...services.validation import is_uuid

plants_router = APIRouter()
layouts_router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
EDITOR = [Depends(require_roles(UserRole.manager))]


class LayoutPatch(BaseModel):
    scale_m_per_px: Optional[float] = Field(default=None, gt=0, le=1000)


class MarkerIn(BaseModel):
    kind: Literal["machine", "line_label", "area_label"]
    machine_id: Optional[str] = None
    line_id: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=120)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    rotation_deg: float = Field(default=0.0, ge=0, lt=360)
    size: Literal["s", "m", "l"] = "m"

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "machine" and not self.machine_id:
            raise ValueError("marcador de máquina exige machine_id")
        if self.kind == "line_label" and not self.line_id:
            raise ValueError("rótulo de linha exige line_id")
        if self.kind == "area_label" and not (self.label or "").strip():
            raise ValueError("rótulo de área exige label")
        return self


class MarkersIn(BaseModel):
    markers: list[MarkerIn] = Field(max_length=2000)


def _404(what: str = "Layout") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} não encontrado(a).")


async def _plant(db: AsyncSession, plant_id: str, tenant_id: str) -> Plant:
    if not is_uuid(plant_id):
        raise _404("Planta")
    p = (await db.execute(select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tenant_id))).scalar_one_or_none()
    if p is None:
        raise _404("Planta")
    return p


async def _layout(db: AsyncSession, layout_id: str, tenant_id: str, lock: bool = False) -> PlantLayout:
    if not is_uuid(layout_id):
        raise _404()
    q = select(PlantLayout).where(PlantLayout.id == layout_id, PlantLayout.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    lay = (await db.execute(q)).scalar_one_or_none()
    if lay is None:
        raise _404()
    return lay


async def _markers(db: AsyncSession, lay: PlantLayout) -> list[LayoutMarker]:
    return list((await db.execute(select(LayoutMarker).where(
        LayoutMarker.tenant_id == lay.tenant_id, LayoutMarker.layout_id == lay.id)
        .order_by(LayoutMarker.kind, LayoutMarker.created_at))).scalars())


def _draft_only(lay: PlantLayout) -> None:
    if lay.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Só um rascunho pode ser editado: crie um rascunho a partir do layout publicado.")


# ── plant level ──────────────────────────────────────────────────────────────

@plants_router.get("/{plant_id}/layouts")
async def list_layouts(plant_id: str, user: CurrentUser, db: DB):
    p = await _plant(db, plant_id, user.tenant_id)
    rows = (await db.execute(select(PlantLayout).where(
        PlantLayout.tenant_id == user.tenant_id, PlantLayout.plant_id == p.id)
        .order_by(PlantLayout.version.desc()))).scalars().all()
    return [layout_out(r) for r in rows]


@plants_router.post("/{plant_id}/layouts", status_code=201, dependencies=EDITOR)
async def create_layout(plant_id: str, user: CurrentUser, db: DB):
    """New draft, version + 1. Copies the markers and the image of the published layout (editing a published
    layout = working on a draft). One draft per plant at a time (409 with its id)."""
    p = await _plant(db, plant_id, user.tenant_id)
    await db.execute(select(Plant.id).where(Plant.id == p.id).with_for_update())     # serialise per plant
    draft = (await db.execute(select(PlantLayout).where(
        PlantLayout.tenant_id == user.tenant_id, PlantLayout.plant_id == p.id, PlantLayout.status == "draft")
    )).scalar_one_or_none()
    if draft is not None:
        raise HTTPException(status_code=409, detail={"message": "A planta já tem um rascunho de layout.",
                                                     "draft_id": draft.id})
    version = ((await db.execute(select(func.max(PlantLayout.version)).where(
        PlantLayout.plant_id == p.id))).scalar_one() or 0) + 1
    pub = (await db.execute(select(PlantLayout).where(
        PlantLayout.tenant_id == user.tenant_id, PlantLayout.plant_id == p.id, PlantLayout.status == "published")
    )).scalar_one_or_none()
    lay = PlantLayout(tenant_id=user.tenant_id, plant_id=p.id, version=version, status="draft", created_by=user.id)
    if pub is not None:
        for k in ("image_asset_id", "image_content_type", "image_bytes", "image_width_px", "image_height_px",
                  "scale_m_per_px"):
            setattr(lay, k, getattr(pub, k))
    db.add(lay)
    await db.flush()
    if pub is not None:
        for m in await _markers(db, pub):
            db.add(LayoutMarker(tenant_id=user.tenant_id, layout_id=lay.id, kind=m.kind, machine_id=m.machine_id,
                                line_id=m.line_id, label=m.label, x=m.x, y=m.y, rotation_deg=m.rotation_deg,
                                size=m.size))
    await db.commit()
    await db.refresh(lay)
    return layout_out(lay, await _markers(db, lay))


@plants_router.get("/{plant_id}/layout/published")
async def get_published(plant_id: str, user: CurrentUser, db: DB, kpis: bool = True):
    p = await _plant(db, plant_id, user.tenant_id)
    lay = (await db.execute(select(PlantLayout).where(
        PlantLayout.tenant_id == user.tenant_id, PlantLayout.plant_id == p.id, PlantLayout.status == "published")
    )).scalar_one_or_none()
    if lay is None:
        raise HTTPException(status_code=404, detail={
            "message": "Nenhum layout publicado para esta planta.", "code": "no_published_layout",
            "auto_url": f"/api/plants/{p.id}/layout/auto"})
    return await published_view(db, user.tenant_id, p, lay, with_kpis=kpis, line_ids=opscope.operator_lines(user))


@plants_router.get("/{plant_id}/layout/auto")
async def get_auto(plant_id: str, user: CurrentUser, db: DB, kpis: bool = True):
    p = await _plant(db, plant_id, user.tenant_id)
    return await auto_view(db, user.tenant_id, p, with_kpis=kpis, line_ids=opscope.operator_lines(user))


# ── layout level ─────────────────────────────────────────────────────────────

@layouts_router.get("/{layout_id}")
async def get_layout(layout_id: str, user: CurrentUser, db: DB):
    lay = await _layout(db, layout_id, user.tenant_id)
    return layout_out(lay, await _markers(db, lay))


@layouts_router.patch("/{layout_id}", dependencies=EDITOR)
async def patch_layout(layout_id: str, body: LayoutPatch, user: CurrentUser, db: DB):
    lay = await _layout(db, layout_id, user.tenant_id, lock=True)
    _draft_only(lay)
    if "scale_m_per_px" in body.model_fields_set:
        lay.scale_m_per_px = body.scale_m_per_px
    await db.commit()
    await db.refresh(lay)
    return layout_out(lay, await _markers(db, lay))


@layouts_router.post("/{layout_id}/image", dependencies=EDITOR)
async def upload_image(layout_id: str, user: CurrentUser, db: DB, file: UploadFile = File(...)):
    lay = await _layout(db, layout_id, user.tenant_id, lock=True)
    _draft_only(lay)
    data = await file.read(images.settings.layout_image_max_bytes + 1)
    try:
        info = images.inspect(data)
    except images.ImageError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    asset_id = images.store(user.tenant_id, data, info)
    old = (lay.image_asset_id, lay.image_content_type)
    review = images.aspect_changed(lay.image_width_px, lay.image_height_px, info.width, info.height)
    has_markers = (await db.execute(select(func.count()).select_from(LayoutMarker).where(
        LayoutMarker.layout_id == lay.id))).scalar_one() > 0
    lay.image_asset_id, lay.image_content_type = asset_id, info.content_type
    lay.image_bytes, lay.image_width_px, lay.image_height_px = info.size, info.width, info.height
    if review and has_markers:
        lay.needs_review = True          # markers kept; "revisar posições" until published again
    await db.commit()
    if old[0]:
        still = (await db.execute(select(func.count()).select_from(PlantLayout).where(
            PlantLayout.tenant_id == user.tenant_id, PlantLayout.image_asset_id == old[0]))).scalar_one()
        if not still:
            images.remove(user.tenant_id, *old)
    await db.refresh(lay)
    return layout_out(lay, await _markers(db, lay))


async def _image_user(request: Request, db: DB, token: Optional[str] = Query(default=None)) -> User:
    """JWT from the Authorization header or ?token= (an <img src> cannot send headers)."""
    auth = request.headers.get("authorization") or ""
    scheme, _, value = auth.partition(" ")
    tok = value.strip() if scheme.lower() == "bearer" and value.strip() else token
    if not tok:
        raise HTTPException(status_code=401, detail="Não autenticado.", headers={"WWW-Authenticate": "Bearer"})
    return await get_current_user(request, tok, db)


@layouts_router.get("/{layout_id}/image")
async def get_image(layout_id: str, db: DB, user: Annotated[User, Depends(_image_user)],
                    if_none_match: Optional[str] = None):
    lay = await _layout(db, layout_id, user.tenant_id)
    if not lay.image_asset_id:
        raise _404("Imagem")
    path = images.asset_path(user.tenant_id, lay.image_asset_id, lay.image_content_type)
    if not path.exists():
        raise _404("Imagem")
    headers = {"Cache-Control": "private, max-age=86400, immutable", "ETag": f'"{lay.image_asset_id}"',
               "X-Content-Type-Options": "nosniff"}
    if lay.image_content_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return FileResponse(path, media_type=lay.image_content_type, headers=headers)


@layouts_router.put("/{layout_id}/markers", dependencies=EDITOR)
async def put_markers(layout_id: str, body: MarkersIn, user: CurrentUser, db: DB):
    lay = await _layout(db, layout_id, user.tenant_id, lock=True)
    _draft_only(lay)
    mids = [m.machine_id for m in body.markers if m.kind == "machine"]
    if len(set(mids)) != len(mids):
        dup = next(m for m in mids if mids.count(m) > 1)
        raise HTTPException(status_code=409, detail={"message": "Máquina posicionada mais de uma vez no layout.",
                                                     "machine_id": dup})
    if any(not is_uuid(m) for m in mids):
        raise HTTPException(status_code=422, detail="machine_id inválido.")
    lids = [m.line_id for m in body.markers if m.kind == "line_label"]
    if any(not is_uuid(x) for x in lids):
        raise HTTPException(status_code=422, detail="line_id inválido.")
    plant_of_m = dict((await db.execute(select(Machine.id, Line.plant_id).join(Line, Line.id == Machine.line_id).where(
        Machine.tenant_id == user.tenant_id, Machine.id.in_(mids)))).all()) if mids else {}
    plant_of_l = dict((await db.execute(select(Line.id, Line.plant_id).where(
        Line.tenant_id == user.tenant_id, Line.id.in_(lids)))).all()) if lids else {}
    for mid in mids:
        if mid not in plant_of_m:
            raise HTTPException(status_code=422, detail=f"Máquina não encontrada: {mid}.")
        if plant_of_m[mid] != lay.plant_id:
            raise HTTPException(status_code=422, detail={"message": "Máquina de outra planta não pode ser "
                                                                    "posicionada neste layout.", "machine_id": mid})
    for lid in lids:
        if plant_of_l.get(lid) != lay.plant_id:
            raise HTTPException(status_code=422, detail={"message": "Linha não pertence à planta do layout.",
                                                         "line_id": lid})
    await db.execute(delete(LayoutMarker).where(LayoutMarker.tenant_id == user.tenant_id,
                                                LayoutMarker.layout_id == lay.id))
    for m in body.markers:
        db.add(LayoutMarker(tenant_id=user.tenant_id, layout_id=lay.id, kind=m.kind,
                            machine_id=m.machine_id if m.kind == "machine" else None,
                            line_id=m.line_id if m.kind == "line_label" else None,
                            label=(m.label or "").strip() or None, x=m.x, y=m.y, rotation_deg=m.rotation_deg,
                            size=m.size))
    lay.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Marcadores em conflito (máquina repetida).")
    await db.refresh(lay)
    return layout_out(lay, await _markers(db, lay))


@layouts_router.post("/{layout_id}/publish", dependencies=EDITOR)
async def publish(layout_id: str, user: CurrentUser, db: DB):
    lay = await _layout(db, layout_id, user.tenant_id, lock=True)
    _draft_only(lay)
    if not lay.image_asset_id:
        raise HTTPException(status_code=422, detail="Envie a planta baixa (imagem) antes de publicar; sem ela a "
                                                    "tela usa o layout automático.")
    await db.execute(select(Plant.id).where(Plant.id == lay.plant_id).with_for_update())
    prev = (await db.execute(select(PlantLayout).where(
        PlantLayout.tenant_id == user.tenant_id, PlantLayout.plant_id == lay.plant_id,
        PlantLayout.status == "published"))).scalar_one_or_none()
    if prev is not None:
        prev.status = "archived"
        await db.flush()
    now = datetime.now(timezone.utc)
    lay.status, lay.published_by, lay.published_at, lay.needs_review = "published", user.id, now, False
    n_markers = len(await _markers(db, lay))
    await audit.record(db, user.tenant_id, user.id, "layout.publish", "plant_layout", lay.id, {
        "plant_id": lay.plant_id, "version": lay.version, "markers": n_markers,
        "archived_layout_id": prev.id if prev else None})
    await db.commit()
    await db.refresh(lay)
    return layout_out(lay, await _markers(db, lay))
