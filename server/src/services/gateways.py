"""Field gateways (Sprint 5): device credentials, enrollment codes, effective status and the remote tag map.

Remote config contract = the edge client (edge/src/remote_config.py + edge/src/plant_map.py, map v1):

    GET /api/gateways/{gateway_id}/config      Authorization: Gateway <token>   [If-None-Match: "<etag>"]
    200 {"gateway_id", "revision", "config_version", "plant_map": {version: 1, gateway_id, tenant_id, heartbeat_s,
         pulse_poll_ms, sources: [...], tags: [...]}}   + ETag      |   304 when the ETag matches

`plant_map.gateway_id` is the gateway's id (the edge runs with GATEWAY_ID = that id). Tags are the active tags of
the gateway's plant assigned to it (`tags.gateway_id`); their edge-only fields (source, key, Modbus unit_id /
register_type / data_type_plc / word_order, signal_role) live in `tags.edge`. Tags with protocol `http` assigned to a
`ueapd` device (box ueaPowerDriver) go with `address` = the selector string (edge_rules.parse_ueapd_selector). `config_version` increases whenever
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
from .onboarding.edge_rules import UEAPD_BACKFILL, ueapd_selector_error

TOKEN_PREFIX = "fcg_"
# enrollment code alphabet: no 0/O/1/I/L (read aloud / typed on a gateway console)
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 12                                   # 31^12 ≈ 2^59, valid 24 h, single use
# protocol `http` = API HTTP of a ueaPowerDriver box (source `ueapd`, edge/src/protocols/ueapd.py)
EDGE_PROTOCOL = {ProtocolType.modbus_tcp: "modbus", ProtocolType.opc_ua: "opcua", ProtocolType.http: "ueapd"}
DEVICE_KEYS = {"id", "protocol", "endpoint", "host", "port", "publishing_interval_ms", "timestamps",
               "device_heartbeat", "max_clock_skew_s"}
UEAPD_DEVICE_KEYS = {"id", "protocol", "base_url", "line", "poll_interval_s", "timeout_s", "timestamps",
                     "max_clock_skew_s", "backfill", "default_fault_code", "fault_codes"}
MODBUS_EDGE_KEYS = {"unit_id", "register_type", "data_type_plc", "word_order"}
_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# base_url of a ueapd box: http(s)://host[:port][/] — no user/password, no path. A ${VAR} / ${VAR:-default} may only
# be the WHOLE URL, the WHOLE host or the WHOLE port (never part of a label, userinfo or path)
_VAR_TOKEN = r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}"
_HOST_LIT = r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])"
_BASE_URL = re.compile(rf"^https?://(?P<host>{_HOST_LIT}|{_VAR_TOKEN})(?::(?P<port>\d{{1,5}}|{_VAR_TOKEN}))?/?$")
_HOST_ONLY = re.compile(rf"^{_HOST_LIT}$")
MODBUS_REGISTER_TYPES = {"holding", "input", "coil", "discrete"}
SIGNAL_ROLES = {"product_code", "starved", "blocked"}
EDGE_TAG_KEYS = {"source", "key", "unit_id", "register_type", "data_type_plc", "word_order", "signal_role"}
# Modbus Tag.address as provisioned/seeded (edge/src/provision.py, seed_dev.py) and parsed by the edge
# (edge/src/plant_map.py): "unit=2;hr=10:uint16" — or a bare register number with the fields in `tags.edge`.
_MODBUS_ADDR = re.compile(r"^unit=(\d+);(hr|ir|di|co)=(\d+)(?::(\w+))?$")
_AREA = {"hr": "holding", "ir": "input", "di": "discrete", "co": "coil"}


def modbus_address(tag: Tag, edge: dict) -> tuple[int, dict]:
    """(register number, edge fields) of a Modbus tag. Fields set explicitly in `tags.edge` take precedence over
    the ones encoded in the address."""
    raw = str(tag.address or "").strip()
    m = _MODBUS_ADDR.match(raw)
    if m:
        fields = {"unit_id": int(m.group(1)), "register_type": _AREA[m.group(2)]}
        if m.group(4):
            fields["data_type_plc"] = m.group(4)
        return int(m.group(3)), {**fields, **{k: v for k, v in edge.items() if v is not None}}
    try:
        n = int(raw)
    except ValueError:
        n = -1
    if n < 0:
        raise ConfigError(f"tag {tag.name}: endereço Modbus deve ser um registrador inteiro ≥ 0 ou "
                          f"'unit=U;hr|ir|di|co=N[:tipo]' ({tag.address!r})")
    return n, dict(edge)


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


def mqtt_revoked(gw: Gateway) -> bool:
    """Enrolled once (has an MQTT user) and revoked since (no password hash): the subscriber discards the readings
    of its tags until it is enrolled again."""
    return gw.mqtt_username is not None and gw.mqtt_password_hash is None


def mqtt_managed() -> bool:
    """The broker's gateway credentials are synchronized by the server (MQTT_APP_GATEWAYS_FILE, services/mqtt_app_file)."""
    return bool((settings.mqtt_app_gateways_file or "").strip())


def mqtt_revoke_action(gw: Gateway) -> str:
    if mqtt_managed():
        return (f"A credencial MQTT do usuário {gw.mqtt_username} foi retirada automaticamente do broker: em até "
                f"~10 s ele recusa novas conexões deste gateway e derruba a sessão já aberta. Nenhum comando no "
                f"broker é necessário; o servidor já descarta as leituras das tags deste gateway. Se este "
                f"usuário também estiver no gateways.conf manual do broker, remova a linha de lá (a linha manual "
                f"tem prioridade).")
    return (f"Remova a linha do usuário {gw.mqtt_username} do gateways.conf do broker e recarregue-o "
            f"(make mqtt-gateway-remove GATEWAY={gw.mqtt_username} && make mqtt-reload). Até lá o broker ainda "
            f"aceita a conexão, mas o servidor descarta as leituras das tags deste gateway.")


def heartbeat_status(buffer_depth: int, errors: list[str]) -> str:
    return "degraded" if errors or buffer_depth > settings.gateway_degraded_buffer else "online"


# ── devices / tag edge fields validation ─────────────────────────────────────

class ConfigError(ValueError):
    pass


def _check_base_url(did: str, raw) -> str:
    """base_url of a ueapd device: http(s)://host[:port] (optional trailing /), no credential nor path.
    `${VAR}` / `${VAR:-default}` (expanded on the gateway, edge plant_map.expand_env) are accepted only as the whole
    URL, the whole host or the whole port; a default must itself be a valid URL / host / port."""
    bad = ConfigError(f"dispositivo {did}: base_url inválido {raw!r} (use http(s)://host[:porta], sem usuário/senha "
                      f"nem caminho; ${{VAR}} só como URL, host ou porta inteiros)")
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"dispositivo {did}: base_url 'http(s)://host[:porta]' obrigatório")
    url = raw.strip()
    whole = _ENV_VAR.fullmatch(url)
    if whole:
        if whole.group(2) is not None:
            if _ENV_VAR.search(whole.group(2)) or "$" in whole.group(2):
                raise bad
            _check_base_url(did, whole.group(2))
        return url
    if "@" in url:
        raise ConfigError(f"dispositivo {did}: credencial na URL não é permitida (use ${{VAR}} no gateway)")
    m = _BASE_URL.match(url)
    if not m:
        raise bad
    host, port = m.group("host"), m.group("port")
    hv = _ENV_VAR.fullmatch(host)
    if hv and hv.group(2) is not None and not _HOST_ONLY.match(hv.group(2)):
        raise bad
    if port is not None:
        pv = _ENV_VAR.fullmatch(port)
        value = (pv.group(2) if pv else port)
        if value is not None and (not value.isdigit() or not 1 <= int(value) <= 65535):
            raise bad
    return url


def _number(did: str, d: dict, key: str, default: float, lo: float, hi: float) -> None:
    v = d.get(key, default)
    if isinstance(v, str):
        if _ENV_VAR.search(v):
            return                                   # expanded on the gateway
        try:
            v = float(v)
        except ValueError:
            pass
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ConfigError(f"dispositivo {did}: {key} deve ser um número entre {lo:g} e {hi:g} ({v!r})")


def _validate_ueapd(did: str, d: dict) -> dict:
    d = dict(d)
    d["base_url"] = _check_base_url(did, d.get("base_url"))
    _number(did, d, "poll_interval_s", 2.0, 0.2, 300)
    _number(did, d, "timeout_s", 5.0, 0.5, 120)
    if d.get("backfill", "none") not in UEAPD_BACKFILL:
        raise ConfigError(f"dispositivo {did}: backfill inválido {d.get('backfill')!r} "
                          f"({' | '.join(sorted(UEAPD_BACKFILL))})")
    dflt = d.get("default_fault_code", 301)
    if isinstance(dflt, bool) or not isinstance(dflt, int) or dflt <= 0:
        raise ConfigError(f"dispositivo {did}: default_fault_code deve ser inteiro > 0")
    codes = d.get("fault_codes", {})
    if not isinstance(codes, dict) or not all(
            isinstance(k, str) and k and isinstance(v, int) and not isinstance(v, bool) and v > 0
            for k, v in codes.items()):
        raise ConfigError(f"dispositivo {did}: fault_codes deve ser {{tag de falha: código inteiro > 0}}")
    line = d.get("line")
    if line is not None and (not isinstance(line, str) or len(line) > 64):
        raise ConfigError(f"dispositivo {did}: line deve ser texto (até 64 caracteres)")
    return d


def _check_skew(did: str, raw) -> None:
    """max_clock_skew_s: number > 0 or 'off' (edge/src/clock.py parse_max_skew); ${VAR} left to the gateway."""
    if raw is None or isinstance(raw, bool):
        if isinstance(raw, bool):
            raise ConfigError(f"dispositivo {did}: max_clock_skew_s inválido {raw!r}")
        return
    if isinstance(raw, str):
        txt = raw.strip().lower()
        if txt in ("", "off", "none", "null", "false", "disabled") or _ENV_VAR.search(raw):
            return
        try:
            raw = float(txt)
        except ValueError:
            raise ConfigError(f"dispositivo {did}: max_clock_skew_s inválido {raw!r} (número > 0 ou 'off')") from None
    if not isinstance(raw, (int, float)) or raw <= 0:
        raise ConfigError(f"dispositivo {did}: max_clock_skew_s deve ser > 0 ou 'off' ({raw!r})")


def validate_devices(devices: list[dict]) -> list[dict]:
    out, ids = [], set()
    for d in devices or []:
        if not isinstance(d, dict):
            raise ConfigError("dispositivo deve ser um objeto")
        did, proto = d.get("id"), d.get("protocol")
        allowed = UEAPD_DEVICE_KEYS if proto == "ueapd" else DEVICE_KEYS
        extra = set(d) - allowed
        if extra:
            raise ConfigError(f"campos desconhecidos no dispositivo: {sorted(extra)}")
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
        elif proto == "ueapd":
            d = _validate_ueapd(did, d)
            _check_skew(did, d.get("max_clock_skew_s"))
        else:
            raise ConfigError(f"dispositivo {did}: protocolo inválido {proto!r} (opcua | modbus | ueapd)")
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
        raise ConfigError(f"tag {tag.name}: protocolo {tag.protocol.value} não é lido por gateway "
                          f"(modbus_tcp | opc_ua | http com dispositivo ueapd)")
    dev_ids = {d["id"]: d["protocol"] for d in devices}
    src = edge.get("source") or next((d["id"] for d in devices if d["protocol"] == proto), None)
    if src is None and proto == "ueapd":
        raise ConfigError(f"tag {tag.name}: tag http sem dispositivo ueapd no gateway")
    if src is None or src not in dev_ids:
        raise ConfigError(f"tag {tag.name}: dispositivo (source) desconhecido {edge.get('source')!r}")
    if dev_ids[src] != proto:
        raise ConfigError(f"tag {tag.name}: protocolo da tag ({proto}) diferente do dispositivo {src}")
    stored = edge
    if proto == "modbus":
        _, edge = modbus_address(tag, edge)          # checks below see the fields encoded in the address too
        rt = edge.get("register_type", "holding")
        if rt not in MODBUS_REGISTER_TYPES:
            raise ConfigError(f"tag {tag.name}: register_type inválido {rt!r}")
        if tag.semantic_role.value == "cycle_pulse" and rt not in ("coil", "discrete"):
            raise ConfigError(f"tag {tag.name}: cycle_pulse exige register_type coil/discrete")
    elif proto == "ueapd":
        modbus = sorted(k for k in MODBUS_EDGE_KEYS if edge.get(k) is not None)
        if modbus:
            raise ConfigError(f"tag {tag.name}: campos Modbus não se aplicam a fonte ueapd {modbus}")
        msg = ueapd_selector_error(tag.address, tag.semantic_role.value)
        if msg:
            raise ConfigError(f"tag {tag.name}: {msg}")
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
    # stored: only what was set explicitly (the address keeps its own unit/area/type)
    return {**{k: v for k, v in stored.items() if v is not None}, "source": src}


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
                 "unit": t.unit or "", "address": str(t.address),
                 "scale": float(t.scale_factor), "offset": float(t.offset), "poll_interval_ms": int(t.poll_interval_ms),
                 # informative (ignored by the edge parser): where the tag sits in the server's asset tree
                 "name": t.name, "machine_id": t.machine_id, "position_id": t.position_id}
        if t.counter_max is not None:
            entry["counter_max"] = int(t.counter_max)
        if proto == "ueapd":
            entry["address"] = str(t.address).strip()           # selector (state:run, cycles:hipot_1, ...)
        elif proto == "modbus":
            entry["address"], edge = modbus_address(t, edge)
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
