"""
Mapa de dispositivos/tags do gateway (ex.: edge/config/plant.json, gerado pela planta virtual e
completado com os ids do servidor por `python -m edge.src.provision`).

Formato (version 1):
    {
      "version": 1, "gateway_id": "gw-...", "tenant_id": "<uuid>|null",
      "heartbeat_s": 10, "pulse_poll_ms": 20,
      "sources": [
        {"id": "opcua-plc",  "protocol": "opcua",  "endpoint": "opc.tcp://host:4840/path/", "publishing_interval_ms": 250,
         "timestamps": "source", "max_clock_skew_s": 2},   # política de timestamp (edge/src/clock.py)
        {"id": "modbus-plc", "protocol": "modbus", "host": "host", "port": 502}
      ],
      "tags": [
        {"key": "linha.maquina.sinal", "tag_id": "<uuid do servidor>|null", "source": "modbus-plc",
         "semantic_role": "cycle_counter", "unit": "ciclos", "counter_max": 65535,
         "address": 10,                      # OPC UA: NodeId "ns=2;s=..."; Modbus: endereço 0-based
                                             #   (ou "unit=2;hr=10:uint16", formato do Tag.address do servidor)
         "unit_id": 2, "register_type": "holding", "data_type_plc": "uint16", "word_order": "big",
         "scale": 1.0, "offset": 0.0, "poll_interval_ms": 1000}
      ],
      "assets": {...}                        # hierarquia, usada só pelo provisionamento
    }

Strings podem conter `${VAR}` / `${VAR:-padrão}` (expandidos do ambiente na carga), para o mesmo arquivo
servir em localhost e no docker compose.

Regras (docs/domain.md §3):
- Contadores (`cycle_counter`, `part_counter_*`), sinais digitais (`run_state`, `fault_state`,
  `cycle_pulse`) e `stroke_time` **não** passam por deadband.
- `cycle_pulse` é contado por borda no edge (polling rápido, `pulse_poll_ms`) e publicado como contador
  acumulado persistido em SQLite; só é aceito em bit Modbus (coil/discrete).
- Tag sem `tag_id` (ainda não provisionada) é ignorada com aviso — o servidor recusaria o tópico.
- `sources[].timestamps`: `gateway` (padrão) ou `source` (relógio do CLP confiável; só OPC UA);
  `sources[].device_heartbeat` (OPC UA): o dispositivo re-carimba valores parados → assinatura notifica também
  mudança só de timestamp (DataChangeTrigger StatusValueTimestamp);
  `sources[].max_clock_skew_s`: número > 0 (padrão 2) ou `off`. Ver edge/src/clock.py.
- `signal_role` (opcional): papel de borda quando o servidor ainda não tem o papel (Sprint 3: `product_code`
  = código do produto no CLP, `starved`, `blocked`); esses sinais discretos também não passam por deadband.

Sprint 5 (preditiva, docs/predictive.md P-1 — as características por janela são calculadas no SERVIDOR; o edge
entrega as leituras certas):
- `stroke_time` é um **evento por ciclo**: cada golpe vira uma leitura, mesmo com o mesmo valor do golpe anterior
  (o servidor tira a mediana do bloco e exige leitura em ≥ 80 % dos ciclos). OPC UA: cada notificação é um golpe.
  Modbus: o gateway publica o tempo de curso quando o **contador da mesma posição** avança (`machine`+`position`
  ou `position_id` iguais; papel `cycle_counter`/`cycle_pulse`) — sem contador, só quando o valor muda.
- `deadband_pct` (opcional, por tag): substitui o DEADBAND_PCT global (ex.: 0,1 % para a corrente RMS usada na
  média ponderada da preditiva; 0 = publica toda mudança).
- `aggregate` (opcional, só grandezas contínuas): `{"window_s": 1.0, "stat": "rms"|"peak"|"mean"|"max"|"min"}` —
  o gateway amostra a tag (poll/notificação) e publica UM valor por janela (instante = fim da janela): RMS ou pico
  (máx |x|) de vibração/corrente quando o sensor entrega a forma de onda/amostras e não o valor RMS pronto.
  Em OPC UA cada NodeId só pode ter uma tag (para RMS e pico do mesmo sinal use Modbus ou dois nós).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from .clock import POLICIES, parse_max_skew

SEMANTIC_ROLES = {
    "analog", "cycle_counter", "cycle_pulse", "run_state", "part_counter_good", "part_counter_scrap",
    "part_counter_total", "fault_state", "stroke_time", "speed", "current",
}
SIGNAL_ROLES = {"product_code", "starved", "blocked"}   # `signal_role` de borda (discretos, sem deadband)
DEADBAND_ROLES = {"analog", "speed", "current"}      # só grandezas contínuas usam deadband
COUNTER_ROLES = {"cycle_counter", "cycle_pulse", "part_counter_good", "part_counter_scrap", "part_counter_total"}
MODBUS_REGISTER_TYPES = {"holding", "input", "coil", "discrete"}
MODBUS_BIT_TYPES = {"coil", "discrete"}
AGGREGATE_STATS = {"rms", "peak", "mean", "max", "min"}

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# Endereço Modbus no formato do servidor (Tag.address gravado pelo provisionamento): "unit=2;hr=10:uint16"
_MODBUS_ADDR = re.compile(r"^unit=(\d+);(hr|ir|di|co)=(\d+)(?::(\w+))?$")
_AREA = {"hr": "holding", "ir": "input", "di": "discrete", "co": "coil"}


class PlantMapError(ValueError):
    pass


def _flag(raw, sid: str) -> bool:
    if isinstance(raw, bool):
        return raw
    txt = str(raw).strip().lower()
    if txt in ("1", "true", "yes", "on"):
        return True
    if txt in ("0", "false", "no", "off", ""):
        return False
    raise PlantMapError(f"source {sid}: booleano inválido {raw!r}")


def expand_env(value, env: Mapping[str, str]):
    """Expande ${VAR} e ${VAR:-padrão} em strings (recursivo em listas/dicts)."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            v = env.get(name)
            if v in (None, ""):
                if default is None:
                    raise PlantMapError(f"variável de ambiente {name} não definida (sem padrão)")
                return default
            return v
        return _VAR.sub(repl, value)
    if isinstance(value, list):
        return [expand_env(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v, env) for k, v in value.items()}
    return value


@dataclass
class SourceConfig:
    id: str
    protocol: str                   # "opcua" | "modbus"
    endpoint: str = ""
    host: str = ""
    port: int = 502
    publishing_interval_ms: int = 500
    timestamps: str = "gateway"     # "gateway" | "source" (edge/src/clock.py)
    device_heartbeat: bool = False  # OPC UA: o dispositivo re-carimba valores parados (notificar por timestamp)
    max_clock_skew_s: Optional[float] = 2.0


@dataclass
class MapTag:
    key: str
    source: str
    semantic_role: str
    address: object                 # str (OPC UA NodeId) | int (Modbus)
    tag_id: Optional[str] = None
    unit: str = ""
    counter_max: Optional[int] = None
    unit_id: int = 1
    register_type: str = "holding"
    data_type_plc: str = "uint16"
    word_order: str = "big"
    scale: float = 1.0
    offset: float = 0.0
    poll_interval_ms: int = 1000
    signal_role: Optional[str] = None
    machine: str = ""               # nome da máquina (mapa da planta virtual) ou machine_id (mapa do servidor)
    position: Optional[str] = None  # nome da posição ou position_id
    deadband_pct: Optional[float] = None
    aggregate: Optional[dict] = None    # {"window_s": float, "stat": "rms"|...}

    @property
    def position_group(self) -> Optional[str]:
        """Identidade da posição para parear tempo de curso ↔ contador (None = tag da máquina)."""
        return f"{self.machine}/{self.position}" if self.position else None

    @property
    def bypass_deadband(self) -> bool:
        return self.semantic_role not in DEADBAND_ROLES or self.signal_role in SIGNAL_ROLES

    @property
    def is_pulse(self) -> bool:
        return self.semantic_role == "cycle_pulse"


@dataclass
class PlantMap:
    version: int
    gateway_id: str
    tenant_id: Optional[str]
    heartbeat_s: float
    pulse_poll_ms: int
    sources: dict[str, SourceConfig]
    tags: list[MapTag]
    skipped: list[str] = field(default_factory=list)   # chaves sem tag_id

    def tags_for(self, source_id: str, pulse: Optional[bool] = None) -> list[MapTag]:
        return [t for t in self.tags if t.source == source_id and (pulse is None or t.is_pulse == pulse)]


def parse_plant_map(doc: dict, env: Optional[Mapping[str, str]] = None) -> PlantMap:
    env = os.environ if env is None else env
    if doc.get("version") != 1:
        raise PlantMapError(f"versão de mapa não suportada: {doc.get('version')!r}")
    gateway_id = doc.get("gateway_id") or ""
    if not gateway_id:
        raise PlantMapError("gateway_id obrigatório")
    pulse_poll_ms = int(doc.get("pulse_poll_ms", 20))
    if not 1 <= pulse_poll_ms <= 1000:
        raise PlantMapError("pulse_poll_ms deve estar entre 1 e 1000")

    sources: dict[str, SourceConfig] = {}
    for raw in expand_env(doc.get("sources", []), env):
        sid, proto = raw.get("id"), raw.get("protocol")
        if not sid or sid in sources:
            raise PlantMapError(f"source sem id ou duplicada: {sid!r}")
        policy = raw.get("timestamps") or "gateway"
        if policy not in POLICIES:
            raise PlantMapError(f"source {sid}: timestamps inválido {policy!r} (use gateway|source)")
        try:
            max_skew = parse_max_skew(raw.get("max_clock_skew_s", 2.0))
        except ValueError as e:
            raise PlantMapError(f"source {sid}: {e}") from None
        if proto == "opcua":
            if not raw.get("endpoint"):
                raise PlantMapError(f"source {sid}: endpoint obrigatório")
            sources[sid] = SourceConfig(sid, proto, endpoint=raw["endpoint"],
                                        publishing_interval_ms=int(raw.get("publishing_interval_ms", 500)),
                                        timestamps=policy, max_clock_skew_s=max_skew,
                                        device_heartbeat=_flag(raw.get("device_heartbeat", False), sid))
        elif proto == "modbus":
            if not raw.get("host"):
                raise PlantMapError(f"source {sid}: host obrigatório")
            if policy == "source":
                raise PlantMapError(f"source {sid}: Modbus não traz timestamp do CLP (use timestamps=gateway)")
            sources[sid] = SourceConfig(sid, proto, host=raw["host"], port=int(raw.get("port", 502)),
                                        max_clock_skew_s=max_skew)
        else:
            raise PlantMapError(f"source {sid}: protocolo inválido {proto!r}")

    tags: list[MapTag] = []
    skipped: list[str] = []
    keys: set[str] = set()
    tag_ids: set[str] = set()
    for raw in doc.get("tags", []):
        key = raw.get("key")
        if not key or key in keys:
            raise PlantMapError(f"tag sem key ou duplicada: {key!r}")
        keys.add(key)
        role = raw.get("semantic_role", "analog")
        if role not in SEMANTIC_ROLES:
            raise PlantMapError(f"tag {key}: semantic_role inválido {role!r}")
        src = sources.get(raw.get("source"))
        if src is None:
            raise PlantMapError(f"tag {key}: source desconhecida {raw.get('source')!r}")
        address = raw.get("address")
        if src.protocol == "opcua":
            if not isinstance(address, str) or not address.startswith("ns="):
                raise PlantMapError(f"tag {key}: endereço OPC UA deve ser um NodeId 'ns=..;s=..' ({address!r})")
            if role == "cycle_pulse":
                raise PlantMapError(f"tag {key}: cycle_pulse só é suportado em bit Modbus (coil/discrete)")
        else:
            m = _MODBUS_ADDR.match(address.strip()) if isinstance(address, str) else None
            if m:          # "unit=2;hr=10:uint16" (Tag.address do servidor) → campos do mapa v1
                raw = {**raw, "unit_id": raw.get("unit_id", int(m.group(1))),
                       "register_type": raw.get("register_type", _AREA[m.group(2)])}
                if m.group(4):
                    raw.setdefault("data_type_plc", m.group(4))
                address = int(m.group(3))
            if not isinstance(address, int) or isinstance(address, bool) or address < 0:
                raise PlantMapError(f"tag {key}: endereço Modbus deve ser inteiro >= 0 ({address!r})")
            rt = raw.get("register_type", "holding")
            if rt not in MODBUS_REGISTER_TYPES:
                raise PlantMapError(f"tag {key}: register_type inválido {rt!r}")
            if role == "cycle_pulse" and rt not in MODBUS_BIT_TYPES:
                raise PlantMapError(f"tag {key}: cycle_pulse exige register_type coil/discrete")
        signal_role = raw.get("signal_role") or None
        if signal_role is not None and signal_role not in SIGNAL_ROLES:
            raise PlantMapError(f"tag {key}: signal_role inválido {signal_role!r}")
        counter_max = raw.get("counter_max")
        if counter_max is not None and (not isinstance(counter_max, int) or counter_max <= 0):
            raise PlantMapError(f"tag {key}: counter_max inválido {counter_max!r}")
        deadband_pct = raw.get("deadband_pct")
        if deadband_pct is not None and (isinstance(deadband_pct, bool) or not isinstance(deadband_pct, (int, float))
                                         or deadband_pct < 0):
            raise PlantMapError(f"tag {key}: deadband_pct inválido {deadband_pct!r}")
        aggregate = raw.get("aggregate") or None
        if aggregate is not None:
            if role not in DEADBAND_ROLES or signal_role in SIGNAL_ROLES:
                raise PlantMapError(f"tag {key}: aggregate só vale para grandezas contínuas (não {role})")
            w, stat = aggregate.get("window_s") if isinstance(aggregate, dict) else None, \
                (aggregate.get("stat") if isinstance(aggregate, dict) else None)
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0 or stat not in AGGREGATE_STATS:
                raise PlantMapError(f"tag {key}: aggregate inválido {aggregate!r} "
                                    f"(window_s > 0, stat em {sorted(AGGREGATE_STATS)})")
            aggregate = {"window_s": float(w), "stat": stat}

        tag = MapTag(
            key=key, source=src.id, semantic_role=role, address=address,
            tag_id=raw.get("tag_id") or None, unit=raw.get("unit") or "", counter_max=counter_max,
            unit_id=int(raw.get("unit_id", 1)), register_type=raw.get("register_type", "holding"),
            data_type_plc=raw.get("data_type_plc", "uint16"), word_order=raw.get("word_order", "big"),
            scale=float(raw.get("scale", 1.0)), offset=float(raw.get("offset", 0.0)),
            poll_interval_ms=int(raw.get("poll_interval_ms", 1000)), signal_role=signal_role,
            machine=str(raw.get("machine_id") or raw.get("machine") or ""),
            position=(raw.get("position_id") or raw.get("position") or None),
            deadband_pct=None if deadband_pct is None else float(deadband_pct), aggregate=aggregate,
        )
        if tag.tag_id is None:
            skipped.append(key)
            continue
        if tag.tag_id in tag_ids:
            raise PlantMapError(f"tag_id repetido no mapa: {tag.tag_id}")
        tag_ids.add(tag.tag_id)
        tags.append(tag)

    return PlantMap(
        version=1, gateway_id=gateway_id, tenant_id=doc.get("tenant_id") or None,
        heartbeat_s=float(doc.get("heartbeat_s", 10.0)), pulse_poll_ms=pulse_poll_ms,
        sources=sources, tags=tags, skipped=skipped,
    )


def load_plant_map(path: str | Path, env: Optional[Mapping[str, str]] = None) -> PlantMap:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return parse_plant_map(doc, env)
