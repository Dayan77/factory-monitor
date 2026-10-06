"""
Field gateways (Sprint 5): fleet management, enrollment and remote configuration.

Admin (JWT of the tenant):
  GET    /api/gateways/                     fleet with effective status (manager+)
  POST   /api/gateways/                     create (admin)
  GET    /api/gateways/{id}                 detail (manager+)
  PATCH  /api/gateways/{id}                 name / description / devices / heartbeat_s (admin)
  DELETE /api/gateways/{id}                 remove; its tags lose the assignment (admin)
  POST   /api/gateways/{id}/enroll-token    one-time enrollment code, 24 h (admin)
  POST   /api/gateways/{id}/revoke          invalidates the device token (admin)
  PUT    /api/gateways/{id}/tags            tag assignment + edge fields (admin)
  GET    /api/gateways/{id}/config-preview  the map the device would receive, with warnings (admin)
  GET    /api/gateways/{id}/mqtt            MQTT user + gateways.conf line for the broker (admin)

Device (no JWT):
  POST   /api/gateways/enroll               {code} → {gateway_id, token, mqtt{username, password, topic_prefix}}
  GET    /api/gateways/{id}/config          Authorization: Gateway <token>; ETag / If-None-Match → 304
  POST   /api/gateways/{id}/heartbeat       Authorization: Gateway <token>; {version, uptime_s, buffer_depth, errors}

Every admin query filters tenant_id (another tenant's gateway → 404). The device token, the enrollment code and the
MQTT password are returned exactly once and stored only as hashes.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Gateway, Line, Machine, Plant, Tag, Tenant, User, UserRole
from ...services import gateways as gs
from ...services.usage.cache import topology_cache
from ...services.validation import is_uuid
from ..tenant_refs import ensure_owned

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
ADMIN = [Depends(require_roles(UserRole.admin))]
MANAGER = [Depends(require_roles(UserRole.manager))]


# ── schemas ─────────────────────────────────────────────────────────────────

class GatewayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plant_id: str
    description: Optional[str] = Field(default=None, max_length=500)
    devices: list[dict] = Field(default_factory=list)
    heartbeat_s: float = Field(default=10.0, ge=1, le=3600)
    pulse_poll_ms: int = Field(default=20, ge=1, le=1000)


class GatewayUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    devices: Optional[list[dict]] = None
    heartbeat_s: Optional[float] = Field(default=None, ge=1, le=3600)
    pulse_poll_ms: Optional[int] = Field(default=None, ge=1, le=1000)


class GatewayOut(BaseModel):
    id: str
    tenant_id: str
    plant_id: str
    name: str
    description: Optional[str]
    status: Literal["pending", "online", "degraded", "offline"]
    last_seen_at: Optional[datetime]
    version: Optional[str]
    config_version: int
    config_fetched_at: Optional[datetime]
    enrolled: bool
    token_prefix: Optional[str]
    enroll_pending: bool
    enroll_expires_at: Optional[datetime]
    heartbeat_s: float
    pulse_poll_ms: int
    devices: list[dict]
    tag_count: int
    last_heartbeat: Optional[dict]
    mqtt_username: Optional[str]
    # the gateway was revoked: its MQTT credential no longer exists on the server and the readings of its tags
    # are discarded; the broker line (gateways.conf) must still be removed by the operator of the broker
    mqtt_revoked: bool = False
    mqtt_action: Optional[str] = None
    created_at: datetime


class TagAssignment(BaseModel):
    tag_id: str
    source: Optional[str] = None
    key: Optional[str] = Field(default=None, max_length=200)
    unit_id: Optional[int] = Field(default=None, ge=0, le=255)
    register_type: Optional[str] = None
    data_type_plc: Optional[str] = Field(default=None, max_length=32)
    word_order: Optional[Literal["big", "little"]] = None
    signal_role: Optional[str] = None


class TagsIn(BaseModel):
    tags: list[TagAssignment] = Field(max_length=5000)


class EnrollIn(BaseModel):
    code: str = Field(min_length=4, max_length=40)


class HeartbeatIn(BaseModel):
    version: Optional[str] = Field(default=None, max_length=64)
    uptime_s: Optional[float] = Field(default=None, ge=0)
    uptime: Optional[float] = Field(default=None, ge=0)          # alias accepted from older edges
    buffer_depth: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list, max_length=50)
    config_version: Optional[int] = None


def _404() -> HTTPException:
    return HTTPException(status_code=404, detail="Gateway não encontrado.")


async def _get(db: AsyncSession, gateway_id: str, tenant_id: str, lock: bool = False) -> Gateway:
    if not is_uuid(gateway_id):
        raise _404()
    q = select(Gateway).where(Gateway.id == gateway_id, Gateway.tenant_id == tenant_id)
    if lock:
        q = q.with_for_update()
    gw = (await db.execute(q)).scalar_one_or_none()
    if gw is None:
        raise _404()
    return gw


async def _out(db: AsyncSession, gw: Gateway, counts: Optional[dict] = None, now: Optional[datetime] = None) -> GatewayOut:
    if counts is None:
        counts = {gw.id: len(await gs.gateway_tags(db, gw))}
    now = now or datetime.now(timezone.utc)
    return GatewayOut(
        id=gw.id, tenant_id=gw.tenant_id, plant_id=gw.plant_id, name=gw.name, description=gw.description,
        status=gs.effective_status(gw, now), last_seen_at=gw.last_seen_at, version=gw.version,
        config_version=gw.config_version, config_fetched_at=gw.config_fetched_at, enrolled=gw.token_hash is not None,
        token_prefix=gw.token_prefix,
        enroll_pending=gw.enroll_code_hash is not None and gw.enroll_expires_at is not None and gw.enroll_expires_at > now,
        enroll_expires_at=gw.enroll_expires_at, heartbeat_s=gw.heartbeat_s, pulse_poll_ms=gw.pulse_poll_ms,
        devices=list(gw.devices or []), tag_count=counts.get(gw.id, 0), last_heartbeat=gw.last_heartbeat,
        mqtt_username=gw.mqtt_username, mqtt_revoked=gs.mqtt_revoked(gw),
        mqtt_action=gs.mqtt_revoke_action(gw) if gs.mqtt_revoked(gw) else None, created_at=gw.created_at)


def _422(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=msg)


async def fleet(db: AsyncSession, tenant_id: str, plant_id: Optional[str] = None) -> list[Gateway]:
    q = select(Gateway).where(Gateway.tenant_id == tenant_id)
    if plant_id:
        q = q.where(Gateway.plant_id == plant_id)
    return list((await db.execute(q.order_by(Gateway.name))).scalars())


# ── admin ───────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[GatewayOut], dependencies=MANAGER)
async def list_gateways(user: CurrentUser, db: DB, plant_id: Optional[str] = None):
    if plant_id and not is_uuid(plant_id):
        return []
    gws = await fleet(db, user.tenant_id, plant_id)
    from sqlalchemy import func
    counts = dict((await db.execute(select(Tag.gateway_id, func.count()).where(
        Tag.tenant_id == user.tenant_id, Tag.is_active.is_(True), Tag.gateway_id.is_not(None))
        .group_by(Tag.gateway_id))).all())
    now = datetime.now(timezone.utc)
    return [await _out(db, g, counts, now) for g in gws]


@router.post("/", response_model=GatewayOut, status_code=201, dependencies=ADMIN)
async def create_gateway(body: GatewayCreate, user: CurrentUser, db: DB):
    plant_id = await ensure_owned(db, Plant, body.plant_id, user.tenant_id, "plant_id")
    try:
        devices = gs.validate_devices(body.devices)
    except gs.ConfigError as e:
        raise _422(str(e))
    gw = Gateway(tenant_id=user.tenant_id, plant_id=plant_id, name=body.name.strip(), description=body.description,
                 devices=devices, heartbeat_s=body.heartbeat_s, pulse_poll_ms=body.pulse_poll_ms, status="pending")
    db.add(gw)
    await db.commit()
    await db.refresh(gw)
    return await _out(db, gw)


@router.get("/{gateway_id}", response_model=GatewayOut, dependencies=MANAGER)
async def get_gateway(gateway_id: str, user: CurrentUser, db: DB):
    return await _out(db, await _get(db, gateway_id, user.tenant_id))


@router.patch("/{gateway_id}", response_model=GatewayOut, dependencies=ADMIN)
async def patch_gateway(gateway_id: str, body: GatewayUpdate, user: CurrentUser, db: DB):
    gw = await _get(db, gateway_id, user.tenant_id, lock=True)
    data = body.model_dump(exclude_unset=True)
    if "devices" in data and data["devices"] is not None:
        try:
            data["devices"] = gs.validate_devices(data["devices"])
        except gs.ConfigError as e:
            raise _422(str(e))
    for k, v in data.items():
        if v is None and k != "description":
            continue
        setattr(gw, k, v.strip() if k == "name" else v)
    await db.commit()
    await db.refresh(gw)
    return await _out(db, gw)


@router.delete("/{gateway_id}", status_code=204, dependencies=ADMIN)
async def delete_gateway(gateway_id: str, user: CurrentUser, db: DB):
    gw = await _get(db, gateway_id, user.tenant_id)
    if gw.token_hash is not None or gw.mqtt_password_hash is not None:
        # deleting first would unassign the tags and the device's readings (MQTT credential still valid on the
        # broker) would be accepted again: revoke first — it tells which broker line to remove
        raise HTTPException(status_code=409, detail="Revogue o gateway antes de removê-lo (a credencial MQTT "
                                                    "ainda está ativa).")
    await db.execute(update(Tag).where(Tag.tenant_id == user.tenant_id, Tag.gateway_id == gw.id)
                     .values(gateway_id=None))
    await db.delete(gw)
    await db.commit()


@router.post("/{gateway_id}/enroll-token", dependencies=ADMIN)
async def enroll_token(gateway_id: str, user: CurrentUser, db: DB):
    """One-time enrollment code (24 h). Typed on the gateway (`POST /api/gateways/enroll`). A new code replaces the
    previous one; the current device token keeps working until the new enrollment happens."""
    gw = await _get(db, gateway_id, user.tenant_id, lock=True)
    code = gs.new_enroll_code()
    now = datetime.now(timezone.utc)
    gw.enroll_code_hash = gs.sha256(gs.normalize_code(code))
    gw.enroll_expires_at = gs.enroll_expiry(now)
    await db.commit()
    return {"gateway_id": gw.id, "code": code, "expires_at": gw.enroll_expires_at.isoformat(),
            "enroll_url": "/api/gateways/enroll"}


@router.post("/{gateway_id}/revoke", response_model=GatewayOut, dependencies=ADMIN)
async def revoke(gateway_id: str, user: CurrentUser, db: DB):
    gw = await _get(db, gateway_id, user.tenant_id, lock=True)
    gw.token_hash = gw.token_prefix = None
    gw.enroll_code_hash = gw.enroll_expires_at = None
    gw.mqtt_password_hash = None       # the MQTT credential dies too: readings of its tags are discarded
    gw.status = "pending"
    await db.commit()
    topology_cache.invalidate()        # the MQTT hot path sees the revocation at the next reading
    await db.refresh(gw)
    return await _out(db, gw)


@router.put("/{gateway_id}/tags", dependencies=ADMIN)
async def assign_tags(gateway_id: str, body: TagsIn, user: CurrentUser, db: DB):
    """Replaces the gateway's tag set: listed tags are assigned (with their edge fields); tags of this gateway not
    listed lose the assignment. Every tag must belong to the tenant and to a machine of the gateway's plant."""
    gw = await _get(db, gateway_id, user.tenant_id, lock=True)
    ids = [a.tag_id for a in body.tags]
    if len(set(ids)) != len(ids):
        raise _422("tag_id repetido.")
    if any(not is_uuid(i) for i in ids):
        raise _422("tag_id inválido.")
    tags = {t.id: t for t in (await db.execute(select(Tag).where(
        Tag.tenant_id == user.tenant_id, Tag.id.in_(ids)))).scalars()} if ids else {}
    missing = [i for i in ids if i not in tags]
    if missing:
        raise HTTPException(status_code=404, detail=f"tag_id não encontrado(a): {missing[0]}")
    plant_of = dict((await db.execute(select(Machine.id, Line.plant_id).join(Line, Line.id == Machine.line_id).where(
        Machine.tenant_id == user.tenant_id))).all())
    errors = []
    for a in body.tags:
        t = tags[a.tag_id]
        if plant_of.get(t.machine_id) != gw.plant_id:
            errors.append(f"tag {t.name}: não pertence a uma máquina da planta do gateway")
            continue
        edge = a.model_dump(exclude_none=True, exclude={"tag_id"})
        try:
            t.edge = gs.validate_tag_edge(t, edge, list(gw.devices or []))
        except gs.ConfigError as e:
            errors.append(str(e))
            continue
        t.gateway_id = gw.id
    if errors:
        await db.rollback()
        raise _422("; ".join(errors[:20]))
    q = update(Tag).where(Tag.tenant_id == user.tenant_id, Tag.gateway_id == gw.id)
    if ids:
        q = q.where(Tag.id.not_in(ids))
    await db.execute(q.values(gateway_id=None).execution_options(synchronize_session=False))
    await db.commit()
    gw = await _get(db, gateway_id, user.tenant_id)
    body_, etag, warnings = await gs.config_document(db, gw)
    await db.commit()
    return {"assigned": len(ids), "config_version": body_["config_version"], "warnings": warnings}


@router.get("/{gateway_id}/config-preview", dependencies=ADMIN)
async def config_preview(gateway_id: str, user: CurrentUser, db: DB):
    gw = await _get(db, gateway_id, user.tenant_id, lock=True)
    body, etag, warnings = await gs.config_document(db, gw)
    await db.commit()
    return {**body, "etag": etag, "warnings": warnings}


@router.get("/{gateway_id}/mqtt", dependencies=ADMIN)
async def mqtt_credentials(gateway_id: str, user: CurrentUser, db: DB):
    """MQTT user of the gateway and the `gateways.conf` line for the production broker (infra/mosquitto/prod:
    `<user> <tenant_uuid> <hash>` → ACL `topic readwrite factory/<tenant_uuid>/#`). The password itself was shown
    once, to the device, at enrollment."""
    gw = await _get(db, gateway_id, user.tenant_id)
    if not gw.mqtt_username:
        raise HTTPException(status_code=409, detail="Gateway ainda não cadastrado (sem credencial MQTT).")
    if gs.mqtt_revoked(gw):
        return {"username": gw.mqtt_username, "topic_prefix": f"factory/{gw.tenant_id}/", "revoked": True,
                "acl": None, "gateways_conf_line": None, "action": gs.mqtt_revoke_action(gw)}
    return {"username": gw.mqtt_username, "topic_prefix": f"factory/{gw.tenant_id}/",
            "acl": f"topic readwrite factory/{gw.tenant_id}/#",
            "gateways_conf_line": f"{gw.mqtt_username} {gw.tenant_id} {gw.mqtt_password_hash}", "revoked": False}


# ── device ──────────────────────────────────────────────────────────────────

_enroll_failures: dict[str, list[float]] = {}
ENROLL_MAX_FAILURES = 10
ENROLL_WINDOW_S = 600.0


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/enroll")
async def enroll(body: EnrollIn, request: Request, db: DB):
    """Exchanges a one-time enrollment code for the gateway's credentials (token + MQTT user/password)."""
    ip = _client_ip(request)
    now_m = time.monotonic()
    fails = [t for t in _enroll_failures.get(ip, []) if now_m - t < ENROLL_WINDOW_S]
    _enroll_failures[ip] = fails
    if len(fails) >= ENROLL_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Muitas tentativas de cadastro. Tente mais tarde.")
    now = datetime.now(timezone.utc)
    h = gs.sha256(gs.normalize_code(body.code))
    gw = (await db.execute(select(Gateway).where(Gateway.enroll_code_hash == h).with_for_update())).scalar_one_or_none()
    tenant_ok = gw is not None and (await db.execute(select(Tenant.is_active).where(
        Tenant.id == gw.tenant_id))).scalar_one_or_none()
    if gw is None or not tenant_ok or gw.enroll_expires_at is None or gw.enroll_expires_at <= now:
        fails.append(now_m)
        raise HTTPException(status_code=401, detail="Código de cadastro inválido ou expirado.")
    token = gs.new_token()
    gw.token_hash, gw.token_prefix = gs.sha256(token), token[:12]
    gw.enroll_code_hash = gw.enroll_expires_at = None
    gw.enrolled_at = now
    gw.status = "online"
    gw.last_seen_at = now
    password = gs.secrets.token_urlsafe(24)
    gw.mqtt_username = gw.mqtt_username or f"gw-{gw.id[:8]}-{gw.id[-4:]}"
    gw.mqtt_password_hash = gs.mosquitto_hash(password)
    await db.commit()
    topology_cache.invalidate()
    return {"gateway_id": gw.id, "tenant_id": gw.tenant_id, "token": token,
            "config_url": f"/api/gateways/{gw.id}/config", "heartbeat_url": f"/api/gateways/{gw.id}/heartbeat",
            "mqtt": {"username": gw.mqtt_username, "password": password, "topic_prefix": f"factory/{gw.tenant_id}/"}}


async def gateway_device(gateway_id: str, authorization: Annotated[Optional[str], Header()] = None,
                         db: AsyncSession = Depends(get_db)) -> Gateway:
    exc = HTTPException(status_code=401, detail="Token de gateway inválido.",
                        headers={"WWW-Authenticate": "Gateway"})
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "gateway" or not token.strip().startswith(gs.TOKEN_PREFIX) or not is_uuid(gateway_id):
        raise exc
    gw = (await db.execute(select(Gateway).where(Gateway.id == gateway_id,
                                                 Gateway.token_hash == gs.sha256(token.strip()))
                           .with_for_update())).scalar_one_or_none()
    if gw is None:
        raise exc
    active = (await db.execute(select(Tenant.is_active).where(Tenant.id == gw.tenant_id))).scalar_one_or_none()
    if not active:
        raise HTTPException(status_code=403, detail="Tenant inativo.")
    return gw


Device = Annotated[Gateway, Depends(gateway_device)]


@router.get("/{gateway_id}/config")
async def device_config(gw: Device, db: DB, response: Response,
                        if_none_match: Annotated[Optional[str], Header()] = None):
    body, etag, _ = await gs.config_document(db, gw)
    now = datetime.now(timezone.utc)
    gw.last_seen_at = now
    gw.config_fetched_at = now
    await db.commit()
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return body


@router.post("/{gateway_id}/heartbeat")
async def device_heartbeat(body: HeartbeatIn, gw: Device, db: DB):
    now = datetime.now(timezone.utc)
    errors = [str(e)[:200] for e in body.errors][:20]
    gw.last_seen_at = now
    if body.version:
        gw.version = body.version
    gw.status = gs.heartbeat_status(body.buffer_depth, errors)
    gw.last_heartbeat = {"at": now.isoformat(), "uptime_s": body.uptime_s if body.uptime_s is not None else body.uptime,
                         "buffer_depth": body.buffer_depth, "errors": errors,
                         "config_version": body.config_version}
    doc, _, _ = await gs.config_document(db, gw)          # current version (tags may have changed since)
    cfg_version = doc["config_version"]
    await db.commit()
    return {"status": gw.status, "config_version": cfg_version,
            "config_changed": body.config_version is not None and body.config_version != cfg_version}
