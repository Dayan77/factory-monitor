"""Server-side replica of the per-tag checks of the edge map parser (edge/src/plant_map.py, `parse_plant_map`).

The tag CSV import (docs/sprint6.md §4.1) must answer with the SAME messages the edge would raise, because the
edge is what reads the map. The server image does not ship the edge package, so the rules are replicated here and
kept identical by tests/test_onboarding_tags_csv.py, which runs a shared test vector through both this module
and the real edge parser and asserts message equality. Change both together.
"""
from __future__ import annotations

import re
from typing import Optional

SEMANTIC_ROLES = {
    "analog", "cycle_counter", "cycle_pulse", "run_state", "part_counter_good", "part_counter_scrap",
    "part_counter_total", "fault_state", "stroke_time", "speed", "current",
}
SIGNAL_ROLES = {"product_code", "starved", "blocked"}
MODBUS_REGISTER_TYPES = {"holding", "input", "coil", "discrete"}
MODBUS_BIT_TYPES = {"coil", "discrete"}
_MODBUS_ADDR = re.compile(r"^unit=(\d+);(hr|ir|di|co)=(\d+)(?::(\w+))?$")
_AREA = {"hr": "holding", "ir": "input", "di": "discrete", "co": "coil"}
# box ueaPowerDriver (protocol `ueapd`): selector → accepted semantic roles (= edge UEAPD_SELECTOR_ROLES)
UEAPD_SELECTOR_ROLES = {
    "state:run": {"run_state"},
    "state:fault": {"fault_state"},
    "state:cadence": {"analog", "speed"},
    "passes:good": {"part_counter_good"},
    "passes:scrap": {"part_counter_scrap"},
    "passes:total": {"part_counter_total"},
    "cycles": {"cycle_counter"},
    "measure": {"analog", "current", "speed"},
}
UEAPD_BACKFILL = {"none", "latest_day", "all"}
_UEAPD_STAGE = re.compile(r"^[A-Za-z0-9_]+$")
_UEAPD_POSTO = re.compile(r"^[Pp]([1-9][0-9]?)$")


def parse_ueapd_selector(address) -> tuple[str, Optional[str], Optional[object]]:
    """Replica of edge/src/plant_map.py parse_ueapd_selector (same results and messages — test_ueapd_rules.py).
    Selector of a ueapd tag → (kind, stage, extra): `cycles:<stage>[:P<n>]` → extra = 0-based station index
    (None = whole stage); `measure:<stage>:<path>` → extra = list of keys in the event `detail`."""
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"seletor ueapd deve ser texto ({address!r})")
    a = address.strip()
    if a in UEAPD_SELECTOR_ROLES:
        return a, None, None
    head, _, rest = a.partition(":")
    if head == "cycles":
        stage, _, posto = rest.partition(":")
        if not _UEAPD_STAGE.match(stage or ""):
            raise ValueError(f"seletor {a!r}: estágio inválido")
        if not posto:
            return "cycles", stage, None
        m = _UEAPD_POSTO.match(posto)
        if not m:
            raise ValueError(f"seletor {a!r}: posto deve ser P1, P2, ...")
        return "cycles", stage, int(m.group(1)) - 1
    if head == "measure":
        stage, _, path = rest.partition(":")
        keys = [k for k in path.split(".") if k]
        if not _UEAPD_STAGE.match(stage or "") or not keys:
            raise ValueError(f"seletor {a!r}: use measure:<estágio>:<chave>[.<subchave>]")
        return "measure", stage, keys
    raise ValueError(f"seletor ueapd desconhecido {a!r} (use {', '.join(sorted(UEAPD_SELECTOR_ROLES))})")


def ueapd_selector_error(address, role: str) -> Optional[str]:
    """Message (without the `tag <key>: ` prefix) the edge raises for this selector/role pair, or None."""
    try:
        kind, _, _ = parse_ueapd_selector(address)
    except ValueError as e:
        return str(e)
    if role not in UEAPD_SELECTOR_ROLES[kind]:
        return f"seletor {address!r} exige semantic_role em {sorted(UEAPD_SELECTOR_ROLES[kind])} (não {role})"
    return None


def tag_error(raw: dict, sources: dict[str, str], keys_seen: Optional[set] = None) -> Optional[str]:
    """Error message the edge parser would raise for this map-v1 tag entry, or None.

    `sources`: {source id: protocol ("opcua" | "modbus" | "ueapd")}. `keys_seen`: keys of the previous tags of the same
    map (duplicate key check); updated in place when given."""
    key = raw.get("key")
    if not key or (keys_seen is not None and key in keys_seen):
        return f"tag sem key ou duplicada: {key!r}"
    if keys_seen is not None:
        keys_seen.add(key)
    role = raw.get("semantic_role", "analog")
    if role not in SEMANTIC_ROLES:
        return f"tag {key}: semantic_role inválido {role!r}"
    proto = sources.get(raw.get("source"))
    if proto is None:
        return f"tag {key}: source desconhecida {raw.get('source')!r}"
    address = raw.get("address")
    if proto == "ueapd":
        msg = ueapd_selector_error(address, role)
        if msg:
            return f"tag {key}: {msg}"
    elif proto == "opcua":
        if not isinstance(address, str) or not address.startswith("ns="):
            return f"tag {key}: endereço OPC UA deve ser um NodeId 'ns=..;s=..' ({address!r})"
        if role == "cycle_pulse":
            return f"tag {key}: cycle_pulse só é suportado em bit Modbus (coil/discrete)"
    else:
        m = _MODBUS_ADDR.match(address.strip()) if isinstance(address, str) else None
        if m:
            raw = {**raw, "unit_id": raw.get("unit_id", int(m.group(1))),
                   "register_type": raw.get("register_type", _AREA[m.group(2)])}
            address = int(m.group(3))
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            return f"tag {key}: endereço Modbus deve ser inteiro >= 0 ({address!r})"
        rt = raw.get("register_type", "holding")
        if rt not in MODBUS_REGISTER_TYPES:
            return f"tag {key}: register_type inválido {rt!r}"
        if role == "cycle_pulse" and rt not in MODBUS_BIT_TYPES:
            return f"tag {key}: cycle_pulse exige register_type coil/discrete"
    signal_role = raw.get("signal_role") or None
    if signal_role is not None and signal_role not in SIGNAL_ROLES:
        return f"tag {key}: signal_role inválido {signal_role!r}"
    counter_max = raw.get("counter_max")
    if counter_max is not None and (not isinstance(counter_max, int) or counter_max <= 0):
        return f"tag {key}: counter_max inválido {counter_max!r}"
    return None
