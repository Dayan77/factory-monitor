"""Plant map payloads (docs/sprint6.md §1): published layout with the live state of every machine, the
automatic layout (§1.5) and the current-shift KPIs shown on the markers (§1.4).

KPIs are always aggregated from components (kpis.md §1.6) — the line label shows the OEE of the line computed
from its output machine, never a mean of machine OEEs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import LayoutMarker, Line, Machine, Plant, PlantLayout
from ..oee import kpi
from ..oee.engine import components as machine_components, load_calendar, load_machine_data, machine_infos
from ..oee.service import day_components, load_units, resolve_scope
from ..usage import queries as uq
from .state import describe, load_facts

AUTO_BANNER = "Layout automático — envie a planta baixa para ver o mapa real"


def layout_out(lay: PlantLayout, markers: Optional[list[LayoutMarker]] = None) -> dict:
    return {
        "id": lay.id, "plant_id": lay.plant_id, "version": lay.version, "status": lay.status,
        "has_image": lay.image_asset_id is not None,
        "image_url": f"/api/layouts/{lay.id}/image" if lay.image_asset_id else None,
        "image_content_type": lay.image_content_type, "image_bytes": lay.image_bytes,
        "image_width_px": lay.image_width_px, "image_height_px": lay.image_height_px,
        "scale_m_per_px": lay.scale_m_per_px, "needs_review": bool(lay.needs_review),
        "created_by": lay.created_by, "published_by": lay.published_by, "published_at": lay.published_at,
        "created_at": lay.created_at, "updated_at": lay.updated_at,
        **({"markers": [marker_out(m) for m in markers]} if markers is not None else {}),
    }


def marker_out(m: LayoutMarker) -> dict:
    return {"id": m.id, "kind": m.kind, "machine_id": m.machine_id, "line_id": m.line_id, "label": m.label,
            "x": m.x, "y": m.y, "rotation_deg": m.rotation_deg, "size": m.size}


async def plant_assets(db: AsyncSession, tenant_id: str, plant_id: str) -> tuple[list[Line], list[Machine]]:
    lines = list((await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant_id)
                                   .order_by(Line.created_at, Line.name))).scalars())
    machines = list((await db.execute(select(Machine).join(Line, Line.id == Machine.line_id).where(
        Machine.tenant_id == tenant_id, Line.plant_id == plant_id)
        .order_by(Machine.created_at, Machine.name))).scalars())
    return lines, machines


async def shift_kpis(db: AsyncSession, tenant_id: str, plant: Plant, lines: list[Line], machines: list[Machine],
                     now: datetime) -> tuple[Optional[dict], dict[str, dict], dict[str, dict]]:
    """(current shift instance, OEE of the current shift per machine, per line) — the line from its output
    machine with the line PRI (kpis.md §1.6). No shift now → empty."""
    tz = uq.tzinfo(plant.timezone)
    cal = await load_calendar(db, tenant_id, plant.id, tz)
    inst = cal.instance_at(now)
    if inst is None:
        return None, {}, {}
    shift = {"name": inst.name, "start": inst.start, "end": inst.end, "productive_day": inst.productive_day}
    per_m: dict[str, dict] = {}
    infos = await machine_infos(db, tenant_id, [m.id for m in machines])
    for m in machines:
        if m.id not in infos:
            continue
        md = await load_machine_data(db, infos[m.id], cal, inst.start, max(inst.end, now), now)
        c = machine_components(md, inst.start, inst.end, now)
        r = kpi.oee(c)
        per_m[m.id] = {"oee": r["oee"], "cadence_per_hour": r["cadence_planned_per_hour"], "good": c.good,
                       "scrap": c.scrap, "reason": r.get("reason")}
    per_l: dict[str, dict] = {}
    for ln in lines:
        sc = await resolve_scope(db, tenant_id, "line", ln.id)
        units = await load_units(db, sc, inst.start, max(inst.end, now), now)
        c = day_components(units, [inst], now)
        r = kpi.oee(c)
        per_l[ln.id] = {"oee": r["oee"], "cadence_per_hour": r["cadence_planned_per_hour"], "good": c.good,
                        "scrap": c.scrap, "reason": r.get("reason") or sc.reason}
    return shift, per_m, per_l


async def machine_states(db: AsyncSession, tenant_id: str, plant: Plant, lines: list[Line],
                         machines: list[Machine], now: datetime, with_kpis: bool = True) -> dict:
    facts = await load_facts(db, tenant_id, plant, machines, now)
    shift, kpm, kpl = (await shift_kpis(db, tenant_id, plant, lines, machines, now)) if with_kpis else (None, {}, {})
    line_name = {ln.id: ln.name for ln in lines}
    out_m = []
    for m in machines:
        out_m.append({"machine_id": m.id, "name": m.name, "line_id": m.line_id, "line_name": line_name.get(m.line_id),
                      **describe(facts[m.id]), "kpis": kpm.get(m.id)})
    by_id = {x["machine_id"]: x for x in out_m}
    out_l = []
    for ln in lines:
        om = by_id.get(ln.output_machine_id) if ln.output_machine_id else None
        out_l.append({"line_id": ln.id, "name": ln.name, "output_machine_id": ln.output_machine_id,
                      "output_state": om["state"] if om else None, "output_text": om["text"] if om else None,
                      "kpis": kpl.get(ln.id)})
    return {"machines": out_m, "lines": out_l, "shift": shift}


async def published_view(db: AsyncSession, tenant_id: str, plant: Plant, lay: PlantLayout,
                         now: Optional[datetime] = None, with_kpis: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    markers = list((await db.execute(select(LayoutMarker).where(
        LayoutMarker.tenant_id == tenant_id, LayoutMarker.layout_id == lay.id)
        .order_by(LayoutMarker.kind, LayoutMarker.created_at))).scalars())
    lines, machines = await plant_assets(db, tenant_id, plant.id)
    st = await machine_states(db, tenant_id, plant, lines, machines, now, with_kpis)
    placed = {m.machine_id for m in markers if m.machine_id}
    return {"kind": "published", "plant_id": plant.id, "plant_name": plant.name, "generated_at": now,
            "layout": layout_out(lay, markers), **st,
            "unplaced_machine_ids": [m.id for m in machines if m.id not in placed]}


async def auto_view(db: AsyncSession, tenant_id: str, plant: Plant, now: Optional[datetime] = None,
                    with_kpis: bool = True) -> dict:
    """§1.5: one lane per line (creation order), machines in flow order with the output machine last; markers
    with normalized coordinates of that grid, same state rules."""
    now = now or datetime.now(timezone.utc)
    lines, machines = await plant_assets(db, tenant_id, plant.id)
    st = await machine_states(db, tenant_id, plant, lines, machines, now, with_kpis)
    by_line: dict[str, list[Machine]] = {}
    for m in machines:
        by_line.setdefault(m.line_id, []).append(m)
    lanes, markers = [], []
    n = max(1, len(lines))
    for i, ln in enumerate(lines):
        ms = by_line.get(ln.id, [])
        ms = [m for m in ms if m.id != ln.output_machine_id] + [m for m in ms if m.id == ln.output_machine_id]
        y = (i + 0.5) / n
        lanes.append({"line_id": ln.id, "name": ln.name, "machine_ids": [m.id for m in ms], "y": y})
        markers.append({"kind": "line_label", "line_id": ln.id, "machine_id": None, "label": None,
                        "x": 0.04, "y": y, "rotation_deg": 0.0, "size": "m"})
        k = max(1, len(ms))
        for j, m in enumerate(ms):
            markers.append({"kind": "machine", "machine_id": m.id, "line_id": None, "label": None,
                            "x": round(0.15 + 0.8 * (j + 0.5) / k, 4), "y": y, "rotation_deg": 0.0, "size": "m"})
    return {"kind": "auto", "banner": AUTO_BANNER, "plant_id": plant.id, "plant_name": plant.name,
            "generated_at": now, "lanes": lanes, "markers": markers, **st, "unplaced_machine_ids": []}
