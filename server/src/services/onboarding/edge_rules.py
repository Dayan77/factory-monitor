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


def tag_error(raw: dict, sources: dict[str, str], keys_seen: Optional[set] = None) -> Optional[str]:
    """Error message the edge parser would raise for this map-v1 tag entry, or None.

    `sources`: {source id: protocol ("opcua" | "modbus")}. `keys_seen`: keys of the previous tags of the same
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
    if proto == "opcua":
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
