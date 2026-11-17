"""
Onboarding wizard (Sprint 6 — docs/sprint6.md §4). Admin of the tenant only.

  GET  /api/onboarding                       progress of the 9 steps (resumable, stored per tenant)
  PUT  /api/onboarding                       {step, state: pending|in_progress|done, data} — `done` is validated
                                             (422 {message, errors, warnings}; nothing changes on error)
  GET  /api/onboarding/readiness?plant_id=   step 9 checklist (block s6-readiness-checks, 15 min of data)
  POST /api/onboarding/assets/import         {plant_id, csv, confirm} — asset CSV (preview / confirm, idempotent)
  POST /api/onboarding/tags/import           {gateway_id, csv, confirm} — tag CSV §4.1 (preview / confirm, idempotent;
                                             per-line results with the edge parser's messages)
  GET  /api/onboarding/templates/{assets|tags}.csv   CSV template with the header and one example row
  GET  /api/onboarding/line-templates        line templates (e.g. ueaPowerDriver M1 Hipot + M2 ATS2)
  POST /api/onboarding/line-templates/{key}/apply   {plant_id, line_name?, gateway_id?, devices?: {id: {base_url}}}
                                             → line, machines, positions, tags (idempotent by name); with a gateway:
                                             ueapd devices merged + tags assigned (services/onboarding/line_templates)
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Gateway, Plant, User, UserRole
from ...services.onboarding import assets_csv, line_templates, readiness, steps, tags_csv
from ...services.onboarding.csvutil import CsvError
from ...services.validation import is_uuid

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
ADMIN = [Depends(require_roles(UserRole.admin))]
MAX_CSV = 2_000_000

TAGS_TEMPLATE = (
    "chave;linha;maquina;posicao;papel;fonte;endereco;unit_id;tipo_registro;tipo_dado;ordem_palavras;contador_max;"
    "unidade;escala;offset;intervalo_ms\n"
    "montagem.fixacao.ciclos_a1;Linha de Montagem;Estação de Fixação;Cilindro A1;cycle_counter;modbus-plc;10;2;"
    "holding;uint16;big;65535;;1;0;1000\n"
    "montagem.transportador.rodando;Linha de Montagem;Transportador de Saída;;run_state;opcua-plc;"
    "\"ns=2;s=Transportador.Run\";;;;;;;;;500\n")


class TemplateDeviceIn(BaseModel):
    base_url: Optional[str] = Field(default=None, max_length=300)


class LineTemplateApplyIn(BaseModel):
    plant_id: str = Field(min_length=1)
    line_name: Optional[str] = Field(default=None, max_length=255)
    gateway_id: Optional[str] = None
    devices: dict[str, TemplateDeviceIn] = Field(default_factory=dict)


class LineTemplateDeviceOut(BaseModel):
    id: str
    label: str
    default_base_url: str


class LineTemplateOut(BaseModel):
    key: str
    name: str
    description: str
    line_name: str
    machines: list[str]
    devices: list[LineTemplateDeviceOut]
    tag_count: int


class AppliedMachineOut(BaseModel):
    id: str
    name: str
    created: bool


class AppliedGatewayOut(BaseModel):
    id: str
    devices: list[dict]
    tags_assigned: int
    config_version: int


class LineTemplateApplyOut(BaseModel):
    template: str
    line_id: str
    line_name: str
    line_created: bool
    machines: list[AppliedMachineOut]
    tags_created: int
    tags_updated: int
    tags_unchanged: int
    gateway: Optional[AppliedGatewayOut]
    warnings: list[str]


class StepIn(BaseModel):
    step: int = Field(ge=1, le=9)
    state: Literal["pending", "in_progress", "done"]
    data: Optional[dict] = None


class AssetsImportIn(BaseModel):
    plant_id: str
    csv: str = Field(max_length=MAX_CSV)
    confirm: bool = False


class TagsImportIn(BaseModel):
    gateway_id: str
    csv: str = Field(max_length=MAX_CSV)
    confirm: bool = False


async def _plant(db: AsyncSession, tenant_id: str, plant_id: Optional[str]) -> Plant:
    if plant_id:
        if not is_uuid(plant_id):
            raise HTTPException(status_code=404, detail="Planta não encontrada.")
        p = (await db.execute(select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tenant_id))).scalar_one_or_none()
    else:
        prog = await steps.get_progress(db, tenant_id)
        p = None
        if prog.plant_id:
            p = (await db.execute(select(Plant).where(Plant.id == prog.plant_id,
                                                      Plant.tenant_id == tenant_id))).scalar_one_or_none()
        if p is None:
            p = (await db.execute(select(Plant).where(Plant.tenant_id == tenant_id).order_by(Plant.created_at)
                                  .limit(1))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Planta não encontrada.")
    return p


@router.get("", dependencies=ADMIN)
@router.get("/", dependencies=ADMIN, include_in_schema=False)
async def get_onboarding(user: CurrentUser, db: DB):
    p = await steps.get_progress(db, user.tenant_id)
    await db.commit()
    return steps.progress_out(p)


@router.put("", dependencies=ADMIN)
@router.put("/", dependencies=ADMIN, include_in_schema=False)
async def put_onboarding(body: StepIn, user: CurrentUser, db: DB):
    try:
        p = await steps.put_step(db, user, body.step, body.state, body.data)
    except steps.StepError as e:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"message": f"Passo {body.step} ainda não pode ser concluído.",
                                                     "errors": e.errors, "warnings": e.warnings})
    await db.commit()
    await db.refresh(p)
    return steps.progress_out(p)


@router.get("/readiness", dependencies=ADMIN)
async def get_readiness(user: CurrentUser, db: DB, plant_id: Optional[str] = None):
    plant = await _plant(db, user.tenant_id, plant_id)
    out = await readiness.evaluate(db, user.tenant_id, plant)
    await db.commit()
    return out


def _csv_422(e: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


@router.post("/assets/import", dependencies=ADMIN)
async def import_assets(body: AssetsImportIn, user: CurrentUser, db: DB):
    plant = await _plant(db, user.tenant_id, body.plant_id)
    try:
        if body.confirm:
            out = await assets_csv.confirm(db, user.tenant_id, plant, body.csv)
        else:
            out = await assets_csv.preview(db, user.tenant_id, plant, body.csv)
    except CsvError as e:
        raise _csv_422(e)
    if body.confirm and not out.get("confirmed"):
        raise HTTPException(status_code=422, detail={"message": "O arquivo tem erros: nada foi gravado.", **out})
    return out


@router.post("/tags/import", dependencies=ADMIN)
async def import_tags(body: TagsImportIn, user: CurrentUser, db: DB):
    if not is_uuid(body.gateway_id):
        raise HTTPException(status_code=404, detail="Gateway não encontrado.")
    gw = (await db.execute(select(Gateway).where(Gateway.id == body.gateway_id, Gateway.tenant_id == user.tenant_id)
                           .with_for_update())).scalar_one_or_none()
    if gw is None:
        raise HTTPException(status_code=404, detail="Gateway não encontrado.")
    try:
        if body.confirm:
            out = await tags_csv.confirm(db, gw, body.csv)
        else:
            out = await tags_csv.preview(db, gw, body.csv)
            out.pop("_results", None)
    except CsvError as e:
        raise _csv_422(e)
    except ValueError as e:
        raise _csv_422(e)
    if body.confirm and not out.get("confirmed"):
        raise HTTPException(status_code=422, detail={"message": "O arquivo tem erros: nada foi gravado.", **out})
    return out


@router.get("/templates/{name}", dependencies=ADMIN)
async def template(name: Literal["assets.csv", "tags.csv"]):
    body = assets_csv.TEMPLATE if name == "assets.csv" else TAGS_TEMPLATE
    return Response(content="﻿" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/line-templates", response_model=list[LineTemplateOut], dependencies=ADMIN)
async def list_line_templates():
    return line_templates.list_public()


@router.post("/line-templates/{key}/apply", response_model=LineTemplateApplyOut, dependencies=ADMIN)
async def apply_line_template(key: str, body: LineTemplateApplyIn, user: CurrentUser, db: DB):
    if not is_uuid(body.plant_id):
        raise HTTPException(status_code=404, detail="Planta não encontrada.")
    if body.gateway_id and not is_uuid(body.gateway_id):
        raise HTTPException(status_code=404, detail="Gateway não encontrado.")
    try:
        out = await line_templates.apply(
            db, user.tenant_id, key, body.plant_id, line_name=body.line_name, gateway_id=body.gateway_id or None,
            devices={k: v.base_url for k, v in body.devices.items()})
    except line_templates.TemplateError as e:
        await db.rollback()
        raise HTTPException(status_code=e.status, detail=e.detail)
    await db.commit()
    from ...services.usage.cache import topology_cache
    topology_cache.invalidate()            # new tags / counter links reach the MQTT hot path
    return out
