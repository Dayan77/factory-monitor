"""Plant map payloads (docs/sprint6.md §1): published layout with the live state of every machine, the
automatic layout (§1.5) and the current-shift KPIs shown on the markers (§1.4).

KPIs are always aggregated from components (kpis.md §1.6) — the line label shows the OEE of the line computed
from its output machine, never a mean of machine OEEs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import LayoutMarker, Line, Machine, Plant, PlantLayout
from ..oee import kpi
from ..oee.engine import components as machine_components, load_calendar, load_machine_data, machine_infos
from ..oee.service import day_components, load_units, resolve_scope, total_components
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


async def plant_assets(db: AsyncSession, tenant_id: str, plant_id: str,
                       line_ids: Optional[set[str]] = None) -> tuple[list[Line], list[Machine]]:
    """Lines and machines of the plant; `line_ids` (operator line scope, services/operator_scope.py) keeps only
    those lines — an operator never receives the state of another line (§3.2)."""
    lines = list((await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant_id)
                                   .order_by(Line.created_at, Line.name))).scalars())
    machines = list((await db.execute(select(Machine).join(Line, Line.id == Machine.line_id).where(
        Machine.tenant_id == tenant_id, Line.plant_id == plant_id)
        .order_by(Machine.created_at, Machine.name))).scalars())
    if line_ids is not None:
        lines = [ln for ln in lines if str(ln.id) in line_ids]
        machines = [m for m in machines if str(m.line_id) in line_ids]
    return lines, machines


LIVE_CADENCE_MIN = 15                 # kpis.md §1.9 "cadência ao vivo": peças dos últimos 15 min × 4
OUTSIDE_WINDOW_H = 12                 # §1.2 selo "peças fora do calendário" (S3-7): últimas 12 h
COVERAGE_BADGE_BELOW = 0.95           # §1.2 selo "dados incompletos": data_coverage do turno < 0,95


def _kpi_entry(r: dict, c: kpi.Components, live: kpi.Components, reason: Optional[str]) -> dict:
    """Marker KPIs of the current shift + live cadence. `cadence_per_hour` is the live cadence (§1.9: parts of the
    last 15 min × 4 — hourly buckets spread over their covered time, D7), `cadence_shift_per_hour` the realized
    cadence of the shift (PQ/PBT). `ideal_cycle_time_suspect` (P > 1, S3-2) travels so the UI warns."""
    live_parts = live.produced + live.out_produced
    return {"oee": r["oee"], "performance": r["performance"],
            "cadence_per_hour": live_parts * (60 / LIVE_CADENCE_MIN),
            "cadence_shift_per_hour": r["cadence_planned_per_hour"],
            "cadence_prorated": bool(live.counts_prorated),
            "good": c.good, "scrap": c.scrap, "reason": reason,
            "ideal_cycle_time_suspect": bool(r["ideal_cycle_time_suspect"]),
            "data_coverage": r["data_coverage"], "counts_prorated": bool(c.counts_prorated)}


async def shift_kpis(db: AsyncSession, tenant_id: str, plant: Plant, lines: list[Line], machines: list[Machine],
                     now: datetime) -> tuple[Optional[dict], dict[str, dict], dict[str, dict], dict[str, list]]:
    """(current shift instance, KPIs of the current shift per machine, per line, extra badges per machine) — the
    line from its output machine with the line PRI (kpis.md §1.6). No shift now → no KPIs, but the
    "peças fora do calendário" badge is still computed (it matters most off shift)."""
    tz = uq.tzinfo(plant.timezone)
    cal = await load_calendar(db, tenant_id, plant.id, tz)
    inst = cal.instance_at(now)
    shift = ({"name": inst.name, "start": inst.start, "end": inst.end, "productive_day": inst.productive_day}
             if inst else None)
    live_start = now - timedelta(minutes=LIVE_CADENCE_MIN)
    out_start = now - timedelta(hours=OUTSIDE_WINDOW_H)
    load_start = min(inst.start, out_start) if inst else out_start
    load_stop = max(inst.end, now) if inst else now
    per_m: dict[str, dict] = {}
    badges: dict[str, list] = {}
    infos = await machine_infos(db, tenant_id, [m.id for m in machines])
    for m in machines:
        if m.id not in infos:
            continue
        md = await load_machine_data(db, infos[m.id], cal, load_start, load_stop, now)
        b = []
        outside = machine_components(md, out_start, now, now).out_produced
        if outside >= 0.5:
            b.append({"kind": "outside_calendar", "parts": round(outside), "window_h": OUTSIDE_WINDOW_H})
        if inst is not None:
            c = machine_components(md, inst.start, inst.end, now)
            r = kpi.oee(c)
            per_m[m.id] = _kpi_entry(r, c, machine_components(md, live_start, now, now), r.get("reason"))
            cov = r["data_coverage"]
            if cov is not None and cov < COVERAGE_BADGE_BELOW:
                b.append({"kind": "data_coverage", "value": cov})
        if b:
            badges[m.id] = b
    per_l: dict[str, dict] = {}
    if inst is not None:
        for ln in lines:
            sc = await resolve_scope(db, tenant_id, "line", ln.id)
            units = await load_units(db, sc, inst.start, max(inst.end, now), now)
            c = day_components(units, [inst], now)
            r = kpi.oee(c)
            per_l[ln.id] = _kpi_entry(r, c, total_components(units, live_start, now, now), r.get("reason") or sc.reason)
    return shift, per_m, per_l, badges


async def machine_states(db: AsyncSession, tenant_id: str, plant: Plant, lines: list[Line],
                         machines: list[Machine], now: datetime, with_kpis: bool = True) -> dict:
    facts = await load_facts(db, tenant_id, plant, machines, now)
    shift, kpm, kpl, extra = ((await shift_kpis(db, tenant_id, plant, lines, machines, now)) if with_kpis
                              else (None, {}, {}, {}))
    line_name = {ln.id: ln.name for ln in lines}
    out_m = []
    for m in machines:
        d = describe(facts[m.id])
        d["badges"] = d["badges"] + extra.get(m.id, [])
        out_m.append({"machine_id": m.id, "name": m.name, "line_id": m.line_id, "line_name": line_name.get(m.line_id),
                      **d, "kpis": kpm.get(m.id)})
    by_id = {x["machine_id"]: x for x in out_m}
    out_l = []
    for ln in lines:
        om = by_id.get(ln.output_machine_id) if ln.output_machine_id else None
        out_l.append({"line_id": ln.id, "name": ln.name, "output_machine_id": ln.output_machine_id,
                      "output_state": om["state"] if om else None, "output_text": om["text"] if om else None,
                      "kpis": kpl.get(ln.id)})
    return {"machines": out_m, "lines": out_l, "shift": shift}


async def published_view(db: AsyncSession, tenant_id: str, plant: Plant, lay: PlantLayout,
                         now: Optional[datetime] = None, with_kpis: bool = True,
                         line_ids: Optional[set[str]] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    markers = list((await db.execute(select(LayoutMarker).where(
        LayoutMarker.tenant_id == tenant_id, LayoutMarker.layout_id == lay.id)
        .order_by(LayoutMarker.kind, LayoutMarker.created_at))).scalars())
    lines, machines = await plant_assets(db, tenant_id, plant.id, line_ids)
    if line_ids is not None:
        mids = {m.id for m in machines}
        markers = [m for m in markers if (m.machine_id is None or m.machine_id in mids)
                   and (m.line_id is None or str(m.line_id) in line_ids)]
    st = await machine_states(db, tenant_id, plant, lines, machines, now, with_kpis)
    placed = {m.machine_id for m in markers if m.machine_id}
    return {"kind": "published", "plant_id": plant.id, "plant_name": plant.name, "generated_at": now,
            "layout": layout_out(lay, markers), **st,
            "unplaced_machine_ids": [m.id for m in machines if m.id not in placed]}


async def auto_view(db: AsyncSession, tenant_id: str, plant: Plant, now: Optional[datetime] = None,
                    with_kpis: bool = True, line_ids: Optional[set[str]] = None) -> dict:
    """§1.5: one lane per line (creation order), machines in flow order with the output machine last; markers
    with normalized coordinates of that grid, same state rules."""
    now = now or datetime.now(timezone.utc)
    lines, machines = await plant_assets(db, tenant_id, plant.id, line_ids)
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
