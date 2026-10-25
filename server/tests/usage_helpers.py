"""Helpers for asset/usage tests: build a hierarchy via the API and publish MQTT readings."""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


@dataclass
class Plant:
    plant: dict
    line: dict
    machine: dict
    positions: dict = field(default_factory=dict)   # name -> position
    components: dict = field(default_factory=dict)  # name -> component
    tags: dict = field(default_factory=dict)        # name -> tag


async def api(client: httpx.AsyncClient, method: str, url: str, headers: dict, expect: int = 200, **kw):
    r = await client.request(method, url, headers=headers, **kw)
    assert r.status_code == expect, f"{method} {url} → {r.status_code}: {r.text}"
    return r.json() if r.content else None


async def build_plant(client, t, label: Optional[str] = None, tz: str = "America/Manaus",
                      ideal_cycle_time_s: Optional[float] = None) -> Plant:
    h = t.headers("admin")
    sfx = label or uuid.uuid4().hex[:6]
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": f"Planta {sfx}", "timezone": tz})
    line = await api(client, "POST", "/api/lines/", h, 201, json={"name": f"Linha {sfx}", "plant_id": plant["id"]})
    body = {"line_id": line["id"], "name": f"Máquina {sfx}"}
    if ideal_cycle_time_s:
        body["ideal_cycle_time_s"] = ideal_cycle_time_s
    machine = await api(client, "POST", "/api/machines/", h, 201, json=body)
    return Plant(plant=plant, line=line, machine=machine)


async def add_position(client, t, p: Plant, name: str, component_type: str = "pneumatic_cylinder",
                       cycles_per_part: int = 1) -> dict:
    pos = await api(client, "POST", "/api/positions/", t.headers("admin"), 201, json={
        "machine_id": p.machine["id"], "name": name, "component_type": component_type,
        "cycles_per_part": cycles_per_part})
    p.positions[name] = pos
    return pos


async def add_component(client, t, p: Plant, name: str, position: Optional[dict] = None,
                        component_type: str = "pneumatic_cylinder", nominal_life: Optional[dict] = None,
                        installed_at: Optional[datetime] = None) -> dict:
    body = {"component_type": component_type, "manufacturer": "Festo", "model": "DSBC-32",
            "serial_number": f"SN-{uuid.uuid4().hex[:8]}", "nominal_life": nominal_life or {}}
    if position is not None:
        body["position_id"] = position["id"]
    if installed_at is not None:
        body["installed_at"] = installed_at.isoformat()
    comp = await api(client, "POST", "/api/components/", t.headers("admin"), 201, json=body)
    p.components[name] = comp
    return comp


async def add_tag(client, t, p: Plant, name: str, role: str, position: Optional[dict] = None,
                  counter_max: Optional[int] = None, max_rate_per_s: Optional[float] = None,
                  data_type: str = "int") -> dict:
    body = {"machine_id": p.machine["id"], "name": name, "protocol": "opc_ua",
            "address": f"ns=2;s=Test.{uuid.uuid4().hex[:6]}.{name}", "data_type": data_type,
            "semantic_role": role}
    if position is not None:
        body["position_id"] = position["id"]
    if counter_max is not None:
        body["counter_max"] = counter_max
    if max_rate_per_s is not None:
        body["max_rate_per_s"] = max_rate_per_s
    tag = await api(client, "POST", "/api/tags/", t.headers("admin"), 201, json=body)
    p.tags[name] = tag
    return tag


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def publish(tenant_id: str, tag_id: str, value, ts: datetime,
                  received_at: Optional[datetime] = None) -> None:
    """Runs the real MQTT handler (Influx + Postgres) for one reading."""
    from src.services.mqtt_subscriber import _handle_message
    payload = json.dumps({"v": 1, "value": value, "unit": "", "ts": iso(ts)}).encode()
    await _handle_message(f"factory/{tenant_id}/sensors/{tag_id}", payload,
                          received_at=received_at or (ts + timedelta(seconds=1)))


async def totals_of(db, scope: str, entity_id: str) -> dict:
    from sqlalchemy import select
    from src.models import UsageTotal
    db.expire_all()
    rows = (await db.execute(select(UsageTotal.measure, UsageTotal.value)
                             .where(UsageTotal.scope == scope, UsageTotal.entity_id == entity_id))).all()
    return {m: v for m, v in rows}
