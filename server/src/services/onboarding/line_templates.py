"""Line templates of the onboarding wizard (sprint "onboarding sem passos manuais", C3).

A template (server/src/asset_templates/<name>.json) describes a whole line the way edge/src/provision.py provisions a
gateway map: line + output machine, machines (model, serial, ideal cycle time, fault_code_map), positions (component
type, cycles per part, counter tag), tags (protocol http + ueapd selector) and the ueapd devices (boxes) that read
them. `ueapd_led_driver.json` is derived from edge/config/ueapd.example.json (parity test: test_line_templates.py).

Apply is idempotent and matches by name like provision.py: line by name in the plant, machine by name in the line,
position by name in the machine, tag by name in the machine. Structural fields follow the template (tag role /
address / unit / position, counter tag of each position, output machine of the line); fault_code_map only gains
the missing codes (a code mapped differently is kept, with a warning); an existing tags.edge.key is kept (counter
continuity on the edge, warning if it differs); a same-name tag of another protocol read by another gateway is not
converted (warning, skipped); descriptive machine fields (model, serial, ideal cycle time) are only filled when empty.
With a gateway (same tenant and plant) the ueapd devices are merged into `gateway.devices` by id (base_url from the
request, else the current one, else the template default) and the tags are assigned to it (tags.gateway_id,
tags.edge = {source, key}); config_version is bumped through the same content hash as PUT /api/gateways/{id}/tags.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Gateway, Line, Machine, Plant, Position, Tag, Tenant
from ...models.asset import ComponentType
from ...models.tag import DataType, ProtocolType, SemanticRole
from .. import gateways as gs
from . import csvutil

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "asset_templates"
# fields of a template device that go to gateway.devices (label / default_base_url are UI-only)
DEVICE_FIELDS = ("protocol", "line", "poll_interval_s", "timeout_s", "timestamps", "max_clock_skew_s", "backfill",
                 "default_fault_code", "fault_codes")


class TemplateError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@lru_cache(maxsize=1)
def load_templates() -> dict[str, dict]:
    out = {}
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        tpl = json.loads(p.read_text(encoding="utf-8"))
        out[tpl["key"]] = tpl
    return out


def get_template(key: str) -> dict:
    tpl = load_templates().get(key)
    if tpl is None:
        raise TemplateError(404, "Modelo de linha não encontrado.")
    return tpl


def public(tpl: dict) -> dict:
    return {"key": tpl["key"], "name": tpl["name"], "description": tpl.get("description", ""),
            "line_name": tpl["line_name"], "machines": [m["name"] for m in tpl["machines"]],
            "devices": [{"id": d["id"], "label": d["label"], "default_base_url": d["default_base_url"]}
                        for d in tpl.get("devices", [])],
            "tag_count": len(tpl.get("tags", []))}


def list_public() -> list[dict]:
    return [public(t) for t in load_templates().values()]


def _merge_devices(gw: Gateway, tpl: dict, urls: dict[str, Optional[str]]) -> list[dict]:
    current = list(gw.devices or [])
    by_id = {d.get("id"): i for i, d in enumerate(current)}
    for td in tpl.get("devices", []):
        fields = {k: td[k] for k in DEVICE_FIELDS if k in td}
        i = by_id.get(td["id"])
        old = current[i] if i is not None else {}
        if old and old.get("protocol") != "ueapd":
            raise TemplateError(422, f"O gateway já tem um dispositivo {td['id']!r} com protocolo "
                                     f"{old.get('protocol')!r}: renomeie-o antes de aplicar o modelo.")
        merged = {**old, **fields, "id": td["id"],
                  "fault_codes": {**(old.get("fault_codes") or {}), **(fields.get("fault_codes") or {})},
                  "base_url": (urls.get(td["id"]) or old.get("base_url") or td["default_base_url"]).strip()}
        if i is None:
            by_id[td["id"]] = len(current)
            current.append(merged)
        else:
            current[i] = merged
    try:
        return gs.validate_devices(current)
    except gs.ConfigError as e:
        raise TemplateError(422, str(e)) from None


async def apply(db: AsyncSession, tenant_id: str, key: str, plant_id: str, line_name: Optional[str] = None,
                gateway_id: Optional[str] = None, devices: Optional[dict[str, Optional[str]]] = None) -> dict:
    """Creates / updates the template's assets in the plant (does not commit). Raises TemplateError."""
    tpl = get_template(key)
    devices = devices or {}
    warnings: list[str] = []
    unknown = sorted(set(devices) - {d["id"] for d in tpl.get("devices", [])})
    if unknown:
        raise TemplateError(422, f"Dispositivo(s) fora do modelo: {', '.join(unknown)}.")

    plant = (await db.execute(select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tenant_id))
             ).scalar_one_or_none()
    if plant is None:
        raise TemplateError(404, "Planta não encontrada.")
    gw = None
    if gateway_id:
        gw = (await db.execute(select(Gateway).where(Gateway.id == gateway_id, Gateway.tenant_id == tenant_id)
                               .with_for_update())).scalar_one_or_none()
        if gw is None:
            raise TemplateError(404, "Gateway não encontrado.")
        if gw.plant_id != plant.id:
            raise TemplateError(422, "O gateway é de outra planta.")
    elif devices:
        warnings.append("URLs dos boxes ignoradas: nenhum gateway informado (aplique de novo com o gateway).")

    lname = (line_name or tpl["line_name"]).strip()
    if not lname:
        raise TemplateError(422, "Nome da linha vazio.")
    lines = (await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant.id))).scalars().all()
    line = next((ln for ln in lines if csvutil.norm(ln.name) == csvutil.norm(lname)), None)
    plant_machines = (await db.execute(select(Machine).where(
        Machine.tenant_id == tenant_id, Machine.line_id.in_([ln.id for ln in lines])))).scalars().all() if lines else []
    line_names = {ln.id: ln.name for ln in lines}
    existing_m: dict[str, Machine] = {}
    for tm in tpl["machines"]:
        for m in plant_machines:
            if csvutil.norm(m.name) != csvutil.norm(tm["name"]):
                continue
            if line is not None and m.line_id == line.id:
                existing_m[tm["name"]] = m
            else:
                raise TemplateError(409, f"A máquina {tm['name']!r} já existe na linha "
                                         f"{line_names.get(m.line_id)!r} desta planta (nomes únicos por planta).")
    new_machines = len(tpl["machines"]) - len(existing_m)
    if new_machines:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        total = (await db.execute(select(func.count()).select_from(Machine).where(
            Machine.tenant_id == tenant_id))).scalar_one()
        if total + new_machines > tenant.max_machines:
            raise TemplateError(422, f"Limite de máquinas do plano: o modelo cria {new_machines} máquina(s) e a "
                                     f"empresa já tem {total} de {tenant.max_machines}. Peça o aumento do limite.")

    line_created = line is None
    if line is None:
        line = Line(tenant_id=tenant_id, plant_id=plant.id, name=lname)
        db.add(line)
        await db.flush()

    machines: dict[str, Machine] = {}
    machines_out = []
    positions: dict[tuple[str, str], Position] = {}
    for tm in tpl["machines"]:
        m = existing_m.get(tm["name"])
        created = m is None
        if m is None:
            m = Machine(tenant_id=tenant_id, line_id=line.id, name=tm["name"], model=tm.get("model"),
                        serial_number=tm.get("serial_number"), ideal_cycle_time_s=tm.get("ideal_cycle_time_s"),
                        fault_code_map=dict(tm.get("fault_code_map") or {}))
            db.add(m)
            await db.flush()
        else:
            m.model = m.model or tm.get("model")
            m.serial_number = m.serial_number or tm.get("serial_number")
            if m.ideal_cycle_time_s is None:
                m.ideal_cycle_time_s = tm.get("ideal_cycle_time_s")
            # only add the template's missing codes: a code the user mapped differently is kept (warning)
            cur = dict(m.fault_code_map or {})
            for code, reason in (tm.get("fault_code_map") or {}).items():
                if code not in cur:
                    cur[code] = reason
                elif cur[code] != reason:
                    warnings.append(f"máquina {m.name}: código de falha {code} mantido como {cur[code]} "
                                    f"(o modelo usa {reason})")
            if cur != (m.fault_code_map or {}):
                m.fault_code_map = cur
        machines[tm["name"]] = m
        machines_out.append({"id": m.id, "name": m.name, "created": created})
        current = {csvutil.norm(p.name): p for p in (await db.execute(select(Position).where(
            Position.tenant_id == tenant_id, Position.machine_id == m.id))).scalars()}
        for tp in tm.get("positions", []):
            p = current.get(csvutil.norm(tp["name"]))
            if p is None:
                p = Position(tenant_id=tenant_id, machine_id=m.id, name=tp["name"],
                             component_type=ComponentType(tp.get("component_type", "other")),
                             cycles_per_part=int(tp.get("cycles_per_part", 1)))
                db.add(p)
                await db.flush()
            positions[(tm["name"], tp["name"])] = p

    out_m = machines.get(tpl.get("output_machine") or "")
    if out_m is not None and line.output_machine_id != out_m.id:
        if line.output_machine_id is not None:
            warnings.append(f"linha {line.name}: máquina de saída trocada para {out_m.name} (modelo)")
        line.output_machine_id = out_m.id

    # tags (identity = name in the machine, like provision.py)
    mids = [m.id for m in machines.values()]
    existing_t: dict[tuple[str, str], Tag] = {}
    for t in (await db.execute(select(Tag).where(Tag.tenant_id == tenant_id, Tag.machine_id.in_(mids))
                               .order_by(Tag.created_at, Tag.id))).scalars():
        existing_t.setdefault((t.machine_id, t.name), t)
    created_n = updated_n = unchanged_n = 0
    by_key: dict[str, Tag] = {}              # template key → tag (skipped tags absent)
    map_key: dict[str, str] = {}             # template key → map key (an existing tags.edge.key wins)
    for tt in tpl["tags"]:
        m = machines[tt["machine"]]
        pos = positions[(tt["machine"], tt["position"])] if tt.get("position") else None
        want = {"position_id": pos.id if pos else None, "protocol": ProtocolType.http, "address": tt["address"],
                "data_type": DataType(tt.get("data_type", "float")), "unit": tt.get("unit") or None,
                "semantic_role": SemanticRole(tt["semantic_role"]), "counter_max": tt.get("counter_max"),
                "max_rate_per_s": tt.get("max_rate_per_s"), "poll_interval_ms": int(tt.get("poll_interval_ms", 1000)),
                "is_active": True}
        t = existing_t.get((m.id, tt["name"]))
        if (t is not None and t.protocol != ProtocolType.http and t.gateway_id is not None
                and (gw is None or t.gateway_id != gw.id)):
            # a same-name tag read by another gateway with another protocol: never converted behind its back
            warnings.append(f"tag {m.name} / {t.name}: já existe com protocolo {t.protocol.value} em outro gateway "
                            f"— não convertida (renomeie-a ou remova-a e aplique de novo)")
            continue
        if t is None:
            t = Tag(tenant_id=tenant_id, machine_id=m.id, name=tt["name"], scale_factor=1.0, offset=0.0, **want)
            db.add(t)
            existing_t[(m.id, tt["name"])] = t
            created_n += 1
        else:
            diff = {k: v for k, v in want.items() if getattr(t, k) != v}
            for k, v in diff.items():
                setattr(t, k, v)
            if diff:
                updated_n += 1
            else:
                unchanged_n += 1
        by_key[tt["key"]] = t
        old_key = (t.edge or {}).get("key")
        map_key[tt["key"]] = old_key or tt["key"]
        if old_key and old_key != tt["key"]:
            # the edge keys counter state by the map key: keep it (counter continuity)
            warnings.append(f"tag {m.name} / {t.name}: chave do mapa {old_key!r} mantida (o modelo usa {tt['key']!r})")
    await db.flush()

    for tm in tpl["machines"]:
        for tp in tm.get("positions", []):
            ck = tp.get("counter_tag")
            if not ck:
                continue
            p, tag = positions[(tm["name"], tp["name"])], by_key.get(ck)
            if tag is None:
                warnings.append(f"posição {tm['name']} / {tp['name']}: contador do modelo não aplicado (tag ignorada)")
                continue
            if p.counter_tag_id != tag.id:
                if p.counter_tag_id is not None:
                    warnings.append(f"posição {tm['name']} / {tp['name']}: contador trocado para {tag.name} (modelo)")
                p.counter_tag_id = tag.id

    gw_out = None
    if gw is not None:
        devs = _merge_devices(gw, tpl, devices)
        gw.devices = devs
        errors = []
        assigned = 0
        for tt in tpl["tags"]:
            t = by_key.get(tt["key"])
            if t is None:
                continue
            assigned += 1
            try:
                new_edge = gs.validate_tag_edge(t, {"source": tt["source"], "key": map_key[tt["key"]]}, devs)
            except gs.ConfigError as e:
                errors.append(str(e))
                continue
            if t.edge != new_edge or t.gateway_id != gw.id:
                t.edge, t.gateway_id = new_edge, gw.id
        if errors:
            raise TemplateError(422, "; ".join(errors[:20]))
        await db.flush()
        body, _, cfg_warnings = await gs.config_document(db, gw)      # bumps config_version when the map changed
        warnings.extend(cfg_warnings)
        gw_out = {"id": gw.id, "devices": devs, "tags_assigned": assigned,
                  "config_version": body["config_version"]}
    else:
        for tt in tpl["tags"]:          # edge fields ready for a later assignment (existing map key kept)
            t = by_key.get(tt["key"])
            if t is not None and t.gateway_id is None:
                new_edge = {"source": tt["source"], "key": map_key[tt["key"]]}
                if t.edge != new_edge:
                    t.edge = new_edge
    return {"template": tpl["key"], "line_id": line.id, "line_name": line.name, "line_created": line_created,
            "machines": machines_out, "tags_created": created_n, "tags_updated": updated_n,
            "tags_unchanged": unchanged_n, "tags_skipped": len(tpl["tags"]) - len(by_key), "gateway": gw_out, "warnings": warnings}
