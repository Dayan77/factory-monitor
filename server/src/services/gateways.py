"""Field gateways (Sprint 5): device credentials, enrollment codes, effective status and the remote tag map.

Remote config contract = the edge client (edge/src/remote_config.py + edge/src/plant_map.py, map v1):

    GET /api/gateways/{gateway_id}/config      Authorization: Gateway <token>   [If-None-Match: "<etag>"]
    200 {"gateway_id", "revision", "config_version", "plant_map": {version: 1, gateway_id, tenant_id, heartbeat_s,
         pulse_poll_ms, sources: [...], tags: [...]}}   + ETag      |   304 when the ETag matches

`plant_map.gateway_id` is the gateway's id (the edge runs with GATEWAY_ID = that id). Tags are the active tags of
the gateway's plant assigned to it (`tags.gateway_id`); their edge-only fields (source, key, Modbus unit_id /
register_type / data_type_plc / word_order, signal_role) live in `tags.edge`. `config_version` increases whenever
the effective map changes (content hash), whoever changed it (tags, positions, gateway devices).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Gateway, Line, Machine, Tag
from ..models.tag import ProtocolType

TOKEN_PREFIX = "fcg_"
# enrollment code alphabet: no 0/O/1/I/L (read aloud / typed on a gateway console)
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 12                                   # 31^12 ≈ 2^59, valid 24 h, single use
EDGE_PROTOCOL = {ProtocolType.modbus_tcp: "modbus", ProtocolType.opc_ua: "opcua"}
DEVICE_KEYS = {"id", "protocol", "endpoint", "host", "port", "publishing_interval_ms", "timestamps",
               "device_heartbeat", "max_clock_skew_s"}
MODBUS_REGISTER_TYPES = {"holding", "input", "coil", "discrete"}
SIGNAL_ROLES = {"product_code", "starved", "blocked"}
EDGE_TAG_KEYS = {"source", "key", "unit_id", "register_type", "data_type_plc", "word_order", "signal_role"}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_enroll_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LEN))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(code: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (code or "").upper())


def mosquitto_hash(password: str, iterations: int = 101) -> str:
    """Password hash in the format of `mosquitto_passwd` (sha512-pbkdf2): $7$<iter>$<b64 salt>$<b64 hash>
    — the third field of a gateways.conf line (infra/mosquitto/prod/render.sh)."""
    salt = secrets.token_bytes(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, dklen=64)
    return f"$7${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def effective_status(gw: Gateway, now: Optional[datetime] = None) -> str:
    """pending (never enrolled) | offline (no contact for max(3 × heartbeat_s, gateway_offline_after_s)) |
    degraded (last heartbeat reported errors or a buffer above gateway_degraded_buffer) | online."""
    now = now or datetime.now(timezone.utc)
    if gw.token_hash is None:
        return "pending"
    if gw.last_seen_at is None:
        return "offline"
    limit = max(3 * (gw.heartbeat_s or 10.0), settings.gateway_offline_after_s)
    if (now - gw.last_seen_at).total_seconds() > limit:
        return "offline"
    return gw.status if gw.status in ("online", "degraded") else "online"


def heartbeat_status(buffer_depth: int, errors: list[str]) -> str:
    return "degraded" if errors or buffer_depth > settings.gateway_degraded_buffer else "online"


# ── devices / tag edge fields validation ─────────────────────────────────────

class ConfigError(ValueError):
    pass


def validate_devices(devices: list[dict]) -> list[dict]:
    out, ids = [], set()
    for d in devices or []:
        if not isinstance(d, dict):
            raise ConfigError("dispositivo deve ser um objeto")
        extra = set(d) - DEVICE_KEYS
        if extra:
            raise ConfigError(f"campos desconhecidos no dispositivo: {sorted(extra)}")
        did, proto = d.get("id"), d.get("protocol")
        if not did or not isinstance(did, str) or did in ids:
            raise ConfigError(f"dispositivo sem id ou duplicado: {did!r}")
        ids.add(did)
        if proto == "opcua":
            ep = d.get("endpoint")
            if not isinstance(ep, str) or not ep.startswith("opc.tcp://"):
                raise ConfigError(f"dispositivo {did}: endpoint OPC UA 'opc.tcp://…' obrigatório")
            if "@" in ep.split("//", 1)[1].split("/", 1)[0]:
                raise ConfigError(f"dispositivo {did}: credencial na URL não é permitida (use ${{VAR}} no gateway)")
        elif proto == "modbus":
            if not d.get("host"):
                raise ConfigError(f"dispositivo {did}: host obrigatório")
            if d.get("timestamps") == "source":
                raise ConfigError(f"dispositivo {did}: Modbus não traz timestamp do CLP (timestamps=gateway)")
        else:
            raise ConfigError(f"dispositivo {did}: protocolo inválido {proto!r} (opcua | modbus)")
        if d.get("timestamps") not in (None, "gateway", "source"):
            raise ConfigError(f"dispositivo {did}: timestamps inválido")
        out.append(dict(d))
    return out


def validate_tag_edge(tag: Tag, edge: dict, devices: list[dict]) -> dict:
    extra = set(edge) - EDGE_TAG_KEYS
    if extra:
        raise ConfigError(f"tag {tag.name}: campos desconhecidos {sorted(extra)}")
    proto = EDGE_PROTOCOL.get(tag.protocol)
    if proto is None:
        raise ConfigError(f"tag {tag.name}: protocolo {tag.protocol.value} não é lido por gateway (modbus_tcp | opc_ua)")
    dev_ids = {d["id"]: d["protocol"] for d in devices}
    src = edge.get("source") or next((d["id"] for d in devices if d["protocol"] == proto), None)
    if src is None or src not in dev_ids:
        raise ConfigError(f"tag {tag.name}: dispositivo (source) desconhecido {edge.get('source')!r}")
    if dev_ids[src] != proto:
        raise ConfigError(f"tag {tag.name}: protocolo da tag ({proto}) diferente do dispositivo {src}")
    if proto == "modbus":
        try:
            if int(str(tag.address)) < 0:
                raise ValueError
        except ValueError:
            raise ConfigError(f"tag {tag.name}: endereço Modbus deve ser inteiro ≥ 0 ({tag.address!r})") from None
        rt = edge.get("register_type", "holding")
        if rt not in MODBUS_REGISTER_TYPES:
            raise ConfigError(f"tag {tag.name}: register_type inválido {rt!r}")
        if tag.semantic_role.value == "cycle_pulse" and rt not in ("coil", "discrete"):
            raise ConfigError(f"tag {tag.name}: cycle_pulse exige register_type coil/discrete")
    else:
        if not str(tag.address).startswith("ns="):
            raise ConfigError(f"tag {tag.name}: endereço OPC UA deve ser um NodeId 'ns=..;s=..'")
        if tag.semantic_role.value == "cycle_pulse":
            raise ConfigError(f"tag {tag.name}: cycle_pulse só é suportado em bit Modbus")
    sr = edge.get("signal_role")
    if sr is not None and sr not in SIGNAL_ROLES:
        raise ConfigError(f"tag {tag.name}: signal_role inválido {sr!r}")
    if "unit_id" in edge and not isinstance(edge["unit_id"], int):
        raise ConfigError(f"tag {tag.name}: unit_id deve ser inteiro")
    return {**{k: v for k, v in edge.items() if v is not None}, "source": src}


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "x"


# ── remote config document ───────────────────────────────────────────────────

async def gateway_tags(db: AsyncSession, gw: Gateway) -> list[Tag]:
    return list((await db.execute(select(Tag).where(
        Tag.tenant_id == gw.tenant_id, Tag.gateway_id == gw.id, Tag.is_active.is_(True))
        .order_by(Tag.created_at, Tag.id))).scalars())


async def build_plant_map(db: AsyncSession, gw: Gateway) -> tuple[dict, list[str]]:
    """Map v1 of the gateway (edge/src/plant_map.py) + warnings (tags left out, with the reason)."""
    tags = await gateway_tags(db, gw)
    mids = {t.machine_id for t in tags if t.machine_id}
    machines = {m.id: m for m in (await db.execute(select(Machine).where(
        Machine.tenant_id == gw.tenant_id, Machine.id.in_(mids)))).scalars()} if mids else {}
    lids = {m.line_id for m in machines.values()}
    lines = {ln.id: ln for ln in (await db.execute(select(Line).where(
        Line.tenant_id == gw.tenant_id, Line.id.in_(lids)))).scalars()} if lids else {}
    devices = list(gw.devices or [])
    out_tags, warnings, keys = [], [], set()
    for t in tags:
        m = machines.get(t.machine_id)
        ln = lines.get(m.line_id) if m else None
        if m is None or ln is None or ln.plant_id != gw.plant_id:
            warnings.append(f"tag {t.name}: fora da planta do gateway — ignorada")
            continue
        try:
            edge = validate_tag_edge(t, dict(t.edge or {}), devices)
        except ConfigError as e:
            warnings.append(f"{e} — ignorada")
            continue
        key = edge.get("key") or f"{_slug(ln.name)}.{_slug(m.name)}.{_slug(t.name)}"
        base, n = key, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys.add(key)
        proto = EDGE_PROTOCOL[t.protocol]
        entry = {"key": key, "tag_id": t.id, "source": edge["source"], "semantic_role": t.semantic_role.value,
                 "unit": t.unit or "", "address": int(str(t.address)) if proto == "modbus" else str(t.address),
                 "scale": float(t.scale_factor), "offset": float(t.offset), "poll_interval_ms": int(t.poll_interval_ms),
                 # informative (ignored by the edge parser): where the tag sits in the server's asset tree
                 "name": t.name, "machine_id": t.machine_id, "position_id": t.position_id}
        if t.counter_max is not None:
            entry["counter_max"] = int(t.counter_max)
        if proto == "modbus":
            entry.update(unit_id=int(edge.get("unit_id", 1)), register_type=edge.get("register_type", "holding"),
                         data_type_plc=edge.get("data_type_plc", "uint16"), word_order=edge.get("word_order", "big"))
        if edge.get("signal_role"):
            entry["signal_role"] = edge["signal_role"]
        out_tags.append(entry)
    doc = {"version": 1, "gateway_id": gw.id, "tenant_id": gw.tenant_id, "heartbeat_s": float(gw.heartbeat_s),
           "pulse_poll_ms": int(gw.pulse_poll_ms), "sources": devices, "tags": out_tags}
    return doc, warnings


async def config_document(db: AsyncSession, gw: Gateway) -> tuple[dict, str, list[str]]:
    """{gateway_id, revision, config_version, plant_map} + ETag. Bumps config_version when the map changed."""
    doc, warnings = await build_plant_map(db, gw)
    h = sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False))
    if h != gw.config_hash:
        gw.config_version = (gw.config_version or 0) + 1
        gw.config_hash = h
    body = {"gateway_id": gw.id, "revision": str(gw.config_version), "config_version": gw.config_version,
            "plant_map": doc}
    return body, f'"{gw.config_version}-{h[:16]}"', warnings


def enroll_expiry(now: datetime) -> datetime:
    return now + timedelta(hours=settings.gateway_enroll_ttl_h)
