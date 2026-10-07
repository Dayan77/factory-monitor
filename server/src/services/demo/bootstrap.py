"""Bootstrap of the public demo tenant (docs/sprint6.md §5): `python -m src.demo bootstrap [--map PATH]`.

Creates (idempotent — matched by slug / name / serial):
* tenant `settings.demo_tenant_slug` ("demo"), flagged `is_demo` (hidden from superadmin client listings);
* the plant of the virtual plant map (edge/config/plant.json, `--map` / DEMO_PLANT_MAP) in America/Sao_Paulo,
  with its lines, machines, output/bottleneck machines, positions, components, tags, products and PRI, fault →
  reason maps, 3 × 8 h shifts every day;
* a predictive monitor on the stroke time of every cylinder (scenario F: Cilindro B3 wears and raises the alert);
* the demo gateway (all tags assigned, edge fields from the map) with its device token and MQTT credential —
  printed ONCE (stored only as hashes; `--rotate-gateway` prints new ones);
* the system user `demo-bot` (executes the scripted replacement WO, audited) and the shared, password-less
  account of the demo sessions ("Visitante da demonstração", roles manager + demo_viewer — read-only).
No data is fabricated: readings, stops, OEE and alerts come from the normal pipeline fed by the virtual plant.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.security import hash_password
from ...config import settings
from ...models import (Component, ComponentType, DataType, Gateway, Line, Machine, MachineProduct, Plant, Position,
                       PredictiveMonitor, Product, ProductionRun, ProtocolType, SemanticRole, Shift, Tag, Tenant,
                       User, UserRole)
from ...models.tenant import TenantPlan
from .. import gateways as gs

DEMO_TZ = "America/Sao_Paulo"
DEMO_TENANT_NAME = "Demonstração FactoryConnect"
BOT_EMAIL = "demo-bot@demo.factoryconnect.invalid"
VIEWER_EMAIL = "visitante@demo.factoryconnect.invalid"
BOT_NAME = "demo-bot"
VIEWER_NAME = "Visitante da demonstração"
GATEWAY_NAME = "Gateway da planta virtual (demo)"
PROTOCOL = {"opcua": ProtocolType.opc_ua, "modbus": ProtocolType.modbus_tcp}
TYPE_ALIASES = {"servo_motor": "servo", "contactor": "other"}
DEFAULT_SHIFTS = [
    {"name": "Turno A", "start_local": "06:00", "end_local": "14:00",
     "breaks": [{"start_local": "10:30", "end_local": "11:30", "reason_code": "PLN-01", "label": "refeição"}]},
    {"name": "Turno B", "start_local": "14:00", "end_local": "22:00",
     "breaks": [{"start_local": "18:00", "end_local": "19:00", "reason_code": "PLN-01", "label": "refeição"}]},
    {"name": "Turno C", "start_local": "22:00", "end_local": "06:00",
     "breaks": [{"start_local": "02:00", "end_local": "03:00", "reason_code": "PLN-01", "label": "refeição"}]},
]
EDGE_KEYS = ("source", "key", "unit_id", "register_type", "data_type_plc", "word_order", "signal_role")


class BootstrapError(RuntimeError):
    pass


def default_map_path() -> Optional[Path]:
    env = os.environ.get("DEMO_PLANT_MAP")
    if env:
        return Path(env)
    p = Path(__file__).resolve().parents[4] / "edge" / "config" / "plant.json"
    return p if p.exists() else None


def load_map(path: Optional[str] = None) -> dict:
    p = Path(path) if path else default_map_path()
    if p is None or not p.exists():
        raise BootstrapError("Mapa da planta virtual não encontrado: use --map edge/config/plant.json "
                             "(gerado por `python -m edge.virtual_plant --write-map`) ou DEMO_PLANT_MAP.")
    return json.loads(p.read_text(encoding="utf-8"))


def _address(t: dict) -> str:
    """Same Tag.address format as edge/src/provision.py / scripts/seed_dev.py."""
    if isinstance(t.get("address"), str):
        return t["address"]
    area = {"discrete": "di", "coil": "co", "input": "ir"}.get(t.get("register_type"), "hr")
    dt = f":{t['data_type_plc']}" if t.get("data_type_plc") and area in ("hr", "ir") else ""
    return f"unit={t.get('unit_id', 1)};{area}={t['address']}{dt}"


async def _one(db: AsyncSession, model, **where):
    q = select(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return (await db.execute(q.limit(1))).scalars().first()


async def ensure_tenant(db: AsyncSession) -> Tenant:
    slug = settings.demo_tenant_slug
    t = await _one(db, Tenant, slug=slug)
    if t is not None and not t.is_demo:
        raise BootstrapError(f"O slug {slug!r} já pertence a um tenant que não é a demonstração. Defina "
                             f"DEMO_TENANT_SLUG com outro valor.")
    other = (await db.execute(select(Tenant).where(Tenant.is_demo.is_(True), Tenant.slug != slug))).scalars().first()
    if other is not None:
        raise BootstrapError(f"Já existe outro tenant de demonstração ({other.slug}).")
    if t is None:
        t = Tenant(name=DEMO_TENANT_NAME, slug=slug, is_demo=True, plan=TenantPlan.enterprise, max_users=1000,
                   max_machines=1000)
        db.add(t)
        await db.flush()
    return t


async def demo_tenant(db: AsyncSession) -> Optional[Tenant]:
    return (await db.execute(select(Tenant).where(Tenant.is_demo.is_(True), Tenant.is_active.is_(True))
                             .order_by(Tenant.created_at).limit(1))).scalars().first()


async def _user(db: AsyncSession, tenant_id: str, email: str, name: str, role: UserRole, roles: list[str]) -> User:
    u = await _one(db, User, tenant_id=tenant_id, email=email)
    if u is None:
        # random password never shown: the account cannot log in with a password
        u = User(tenant_id=tenant_id, email=email, name=name, role=role, roles=sorted(set(roles)),
                 password_hash=hash_password(secrets.token_urlsafe(32)), is_active=True)
        db.add(u)
        await db.flush()
    else:
        u.role, u.roles, u.is_active = role, sorted(set(roles)), True
    return u


async def bot_user(db: AsyncSession, tenant_id: str) -> Optional[User]:
    return await _one(db, User, tenant_id=tenant_id, email=BOT_EMAIL)


async def viewer_user(db: AsyncSession, tenant_id: str) -> Optional[User]:
    return await _one(db, User, tenant_id=tenant_id, email=VIEWER_EMAIL)


async def bootstrap(db: AsyncSession, doc: dict, rotate_gateway: bool = False) -> dict:
    """Creates/completes the demo tenant. Commits. Returns a summary (with credentials only when new)."""
    stats = {"created": 0}
    t = await ensure_tenant(db)
    tid = t.id
    await _user(db, tid, BOT_EMAIL, BOT_NAME, UserRole.admin, ["admin", "maintenance_planner", "technician"])
    await _user(db, tid, VIEWER_EMAIL, VIEWER_NAME, UserRole.manager, ["manager", "demo_viewer"])

    async def get_or_create(model, where: dict, values: dict):
        row = await _one(db, model, **where)
        if row is None:
            row = model(**where, **values)
            db.add(row)
            await db.flush()
            stats["created"] += 1
        return row

    assets = doc["assets"]
    p = assets["plant"]
    plant = await get_or_create(Plant, {"tenant_id": tid, "name": p["name"]},
                                {"timezone": DEMO_TZ, "address": "Planta simulada (planta virtual FactoryConnect)"})
    plant.timezone = DEMO_TZ
    for sd in p.get("shifts") or DEFAULT_SHIFTS:
        await get_or_create(Shift, {"tenant_id": tid, "plant_id": plant.id, "name": sd["name"]}, {
            "start_local": sd["start_local"], "end_local": sd["end_local"], "weekdays": list(range(7)),
            "is_active": True,
            "breaks": [{"start_local": b["start_local"], "end_local": b["end_local"],
                        "reason_code": b.get("reason_code") or "PLN-01", **({"label": b["label"]} if b.get("label") else {})}
                       for b in sd.get("breaks", [])]})
    products = {}
    for pr in assets.get("products") or []:
        products[pr["code"]] = await get_or_create(Product, {"tenant_id": tid, "code": pr["code"]}, {"name": pr["name"]})
    machines, positions = {}, {}
    for ln in assets["lines"]:
        line = await get_or_create(Line, {"tenant_id": tid, "plant_id": plant.id, "name": ln["name"]}, {})
        slowest = None
        for m in ln["machines"]:
            machine = await get_or_create(Machine, {"tenant_id": tid, "line_id": line.id, "name": m["name"]}, {
                "model": m.get("model"), "serial_number": m.get("serial_number"),
                "ideal_cycle_time_s": m.get("ideal_cycle_time_s"),
                "fault_code_map": {str(k): v for k, v in (m.get("fault_code_map") or {}).items()}})
            machines[(ln["name"], m["name"])] = machine
            for pos in m.get("positions", []):
                ctype = ComponentType(TYPE_ALIASES.get(pos["component_type"], pos["component_type"]))
                position = await get_or_create(Position, {"tenant_id": tid, "machine_id": machine.id,
                                                          "name": pos["name"]},
                                               {"component_type": ctype,
                                                "cycles_per_part": max(1, int(round(pos.get("cycles_per_part") or 1)))})
                positions[(ln["name"], m["name"], pos["name"])] = (position, pos)
                comp = pos.get("component")
                if comp:
                    serial = f"DEMO-{comp['serial_number']}"
                    c = await get_or_create(Component, {"tenant_id": tid, "serial_number": serial}, {
                        "component_type": ctype, "manufacturer": comp.get("manufacturer"), "model": comp.get("model"),
                        "nominal_life": {k: v for k, v in (comp.get("nominal_life") or {}).items()
                                         if k in ("cycles", "hours", "starts") and v}})
                    occupied = (await db.execute(select(Component.id).where(
                        Component.position_id == position.id, Component.removed_at.is_(None)))).scalar_one_or_none()
                    if c.position_id is None and c.removed_at is None and occupied is None:
                        c.position_id = c.last_position_id = position.id
                        c.installed_at = datetime.now(timezone.utc)
            mps = m.get("products") or []
            for mp in mps:
                prod = products.get(mp["product"]) or await get_or_create(
                    Product, {"tenant_id": tid, "code": mp["product"]}, {"name": mp["product"]})
                products[mp["product"]] = prod
                has_default = (await db.execute(select(MachineProduct.id).where(
                    MachineProduct.machine_id == machine.id, MachineProduct.is_default.is_(True)))).scalar_one_or_none()
                row = await get_or_create(MachineProduct, {"tenant_id": tid, "machine_id": machine.id,
                                                           "product_id": prod.id},
                                          {"ideal_cycle_time_s": float(mp["ideal_cycle_time_s"]),
                                           "parts_per_cycle": int(mp.get("parts_per_cycle") or 1),
                                           "is_default": bool(mp.get("is_default")) and has_default is None})
                if row.is_default:
                    per = row.ideal_cycle_time_s / max(1, row.parts_per_cycle)
                    if slowest is None or per > slowest[0]:
                        slowest = (per, machine.id)
                    if len(mps) == 1:
                        open_run = (await db.execute(select(ProductionRun.id).where(
                            ProductionRun.machine_id == machine.id, ProductionRun.ended_at.is_(None)))).scalar_one_or_none()
                        if open_run is None:
                            db.add(ProductionRun(tenant_id=tid, machine_id=machine.id, product_id=prod.id,
                                                 started_at=datetime.now(timezone.utc)))
        if ln.get("output_machine") and (ln["name"], ln["output_machine"]) in machines:
            line.output_machine_id = machines[(ln["name"], ln["output_machine"])].id
        if slowest and line.bottleneck_machine_id is None:
            line.bottleneck_machine_id = slowest[1]

    sources = [dict(s) for s in doc.get("sources", [])]
    gw = await _one(db, Gateway, tenant_id=tid, name=GATEWAY_NAME)
    if gw is None:
        gw = Gateway(tenant_id=tid, plant_id=plant.id, name=GATEWAY_NAME, status="pending",
                     description="Alimenta a demonstração pública (planta virtual).",
                     devices=gs.validate_devices(_devices(sources)), heartbeat_s=float(doc.get("heartbeat_s") or 10),
                     pulse_poll_ms=int(doc.get("pulse_poll_ms") or 20))
        db.add(gw)
        await db.flush()
    protos = {s["id"]: s["protocol"] for s in sources}
    key_to_tag = {}
    for td in doc.get("tags", []):
        machine = machines[(td["line"], td["machine"])]
        pos_id = positions[(td["line"], td["machine"], td["position"])][0].id if td.get("position") else None
        tag = await get_or_create(Tag, {"tenant_id": tid, "machine_id": machine.id, "name": td["name"]}, {
            "position_id": pos_id, "protocol": PROTOCOL.get(protos.get(td.get("source")), ProtocolType.mqtt),
            "address": _address(td), "data_type": DataType(td.get("data_type", "float")),
            "unit": td.get("unit") or None, "semantic_role": SemanticRole(td.get("semantic_role", "analog")),
            "counter_max": td.get("counter_max"), "max_rate_per_s": td.get("max_rate_per_s"),
            "scale_factor": float(td.get("scale", 1.0)), "poll_interval_ms": int(td.get("poll_interval_ms", 1000))})
        edge = {k: td[k] for k in EDGE_KEYS if td.get(k) is not None}
        if tag.protocol in gs.EDGE_PROTOCOL:
            tag.edge = gs.validate_tag_edge(tag, edge, list(gw.devices or []))
            tag.gateway_id = gw.id
        key_to_tag[td["key"]] = tag
    for (position, pos) in positions.values():
        key = pos.get("counter_tag")
        if key and key in key_to_tag and position.counter_tag_id is None:
            position.counter_tag_id = key_to_tag[key].id
    await db.flush()
    monitors = await _monitors(db, tid, positions, key_to_tag)

    creds = None
    if gw.token_hash is None or rotate_gateway:
        token = gs.new_token()
        password = secrets.token_urlsafe(24)
        gw.token_hash, gw.token_prefix = gs.sha256(token), token[:12]
        gw.mqtt_username = gw.mqtt_username or f"gw-demo-{gw.id[:8]}"
        gw.mqtt_password_hash = gs.mosquitto_hash(password)
        gw.enrolled_at = datetime.now(timezone.utc)
        creds = {"gateway_id": gw.id, "token": token, "config_url": f"/api/gateways/{gw.id}/config",
                 "mqtt": {"username": gw.mqtt_username, "password": password, "topic_prefix": f"factory/{tid}/"},
                 "gateways_conf_line": f"{gw.mqtt_username} {tid} {gw.mqtt_password_hash}",
                 "acl": f"topic readwrite factory/{tid}/#"}
    await gs.config_document(db, gw)
    await db.commit()
    from ..usage.cache import topology_cache
    topology_cache.invalidate()
    return {"tenant_id": tid, "slug": t.slug, "plant_id": plant.id, "gateway_id": gw.id, "tags": len(key_to_tag),
            "monitors": monitors, "rows_created": stats["created"], "gateway_credentials": creds}


_VAR = __import__("re").compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# fields the server validates as values: their ${VAR:-default} is resolved here (bootstrap environment, else the
# default); host / endpoint keep the placeholder — the gateway expands it at load (edge/src/plant_map.py)
_RESOLVED = ("timestamps", "max_clock_skew_s", "port", "publishing_interval_ms", "device_heartbeat")


def _resolve(v):
    if not isinstance(v, str):
        return v
    out = _VAR.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), v)
    try:
        return int(out) if out.isdigit() else out
    except ValueError:
        return out


def _devices(sources: list[dict]) -> list[dict]:
    """Map sources → gateway devices."""
    out = []
    for s in sources:
        d = {k: (_resolve(v) if k in _RESOLVED else v) for k, v in s.items() if k in gs.DEVICE_KEYS}
        out.append(d)
    return out


async def _monitors(db: AsyncSession, tid: str, positions: dict, key_to_tag: dict) -> int:
    """A stroke-time monitor (scenario F) with the suggested rules on every position that has a stroke_time tag —
    created through the app's own API as demo-bot (same validation and defaults as a user would get)."""
    import httpx

    from ...auth.security import create_access_token
    from ...main import app
    bot = await bot_user(db, tid)
    todo = []
    for tag in key_to_tag.values():
        if tag.semantic_role != SemanticRole.stroke_time or not tag.position_id:
            continue
        exists = (await db.execute(select(PredictiveMonitor.id).where(
            PredictiveMonitor.tenant_id == tid, PredictiveMonitor.position_id == tag.position_id,
            PredictiveMonitor.feature == "stroke_time"))).scalar_one_or_none()
        if not exists:
            todo.append({"position_id": tag.position_id, "feature": "stroke_time", "tag_id": tag.id})
    if not todo:
        return 0
    await db.commit()                     # the API runs in its own sessions
    token = create_access_token({"sub": bot.id, "tenant_id": tid, "role": "admin"})
    n = 0
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo.internal") as c:
        for body in todo:
            r = await c.post("/api/predictive/monitors", json=body, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 201:
                n += 1
            elif r.status_code != 409:
                raise BootstrapError(f"monitor preditivo não criado ({r.status_code}): {r.text[:300]}")
    return n
