"""Reliability and life by manufacturer/model — Weibull with right censoring (docs/reports.md §1.5, §2).

Served by `GET /api/reports/reliability-life` (the `reliability_life` report reads that same endpoint).
Data (§2.1):
  * group = (component type, manufacturer, model); empty manufacturer/model → "—" (shown, not compared);
  * unit = one installation period of a component (installed → removed, or installed until the cut-off);
  * age = usage_since_install in the measure of the type's nominal life (S4-11): at removal for removed units,
    usage between installation and the cut-off for units in service at the cut-off;
  * failure (δ = 1) = removed with removal_reason = failure; every other removal and every unit still installed
    is a suspension; t ≤ 0 → excluded (no_usage); type without a life measure → excluded (no_measure).
All queries filter tenant_id.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Component, Line, Machine, Position, WorkOrder, WorkOrderReplacement
from ..maintenance import core
from ..usage import queries as uq
from ..usage.core import RUL_MIN_DAYS, rate_window
from . import weibull

RATE_WINDOW_DAYS = 14     # kpis.md §4.2
HORIZON_DAYS = 30         # §2.5
MEASURE_UNIT = {"cycles": "ciclos", "hours": "h", "starts": "partidas"}
TYPE_PT = {"pneumatic_cylinder": "Cilindro pneumático", "electric_motor": "Motor elétrico", "servo": "Servo",
           "robot_axis": "Eixo de robô", "valve": "Válvula", "gripper": "Garra", "bearing": "Rolamento",
           "belt": "Correia", "pump": "Bomba", "spindle": "Fuso/árvore", "other": "Outro"}


async def _scope_components(db: AsyncSession, tenant_id: str, plant_id: Optional[str],
                            component_type: Optional[str]) -> tuple[list[Component], dict]:
    q = select(Component).where(Component.tenant_id == tenant_id, Component.installed_at.is_not(None))
    if component_type:
        q = q.where(Component.component_type == component_type)
    comps = list((await db.execute(q)).scalars().all())
    pos_ids = {c.position_id or c.last_position_id for c in comps} - {None}
    where = {}
    if pos_ids:
        rows = (await db.execute(
            select(Position.id, Position.name, Machine.id, Machine.name, Line.plant_id)
            .join(Machine, Machine.id == Position.machine_id).join(Line, Line.id == Machine.line_id)
            .where(Position.tenant_id == tenant_id, Position.id.in_(list(pos_ids))))).all()
        where = {pid: {"position_name": pn, "machine_id": mid, "machine_name": mn, "plant_id": pl}
                 for pid, pn, mid, mn, pl in rows}
    if plant_id:
        comps = [c for c in comps if where.get(c.position_id or c.last_position_id, {}).get("plant_id") == plant_id]
    return comps, where


def _nominal_of(cs: list[Component], measure: str) -> tuple[Optional[float], bool]:
    vals = [float(c.nominal_life.get(measure)) for c in cs
            if isinstance((c.nominal_life or {}).get(measure), (int, float)) and c.nominal_life.get(measure) > 0]
    if not vals:
        return None, False
    cnt = Counter(vals)
    return cnt.most_common(1)[0][0], len(cnt) > 1


async def reliability_life(db: AsyncSession, tenant_id: str, cutoff: datetime, plant_id: Optional[str] = None,
                           component_type: Optional[str] = None, top_in_service: int = 20) -> dict:
    comps, where = await _scope_components(db, tenant_id, plant_id, component_type)
    comps = [c for c in comps if c.installed_at < cutoff]
    totals = await uq.component_usages(db, tenant_id, comps)
    in_service = [c for c in comps if c.removed_at is None or c.removed_at > cutoff]
    # in service at the cut-off: usage between installation and the cut-off (buckets); totals when the cut-off
    # is "now" give the same number
    at_cut: dict[str, dict[str, float]] = {}
    rate: dict[str, dict[str, float]] = {}
    window_days: dict[str, float] = {}
    if in_service:
        from ..maintenance import usage as mu
        by_install: dict[datetime, list[str]] = defaultdict(list)
        for c in in_service:
            by_install[c.installed_at].append(c.id)
        for inst_at, ids in by_install.items():
            at_cut.update(await mu.window(db, tenant_id, "component", ids, inst_at, cutoff))
        # S7-2 (audit A-18): the same pace rule as the nominal RUL — complete hours, days of history = min(14, since
        # installation, since the first data); < 3 days → no pace (p_fail null, insufficient_data); < 14 → low_confidence
        for c in in_service:
            since = await uq.component_data_since(db, tenant_id, c)
            ws, we, days = rate_window(cutoff, c.installed_at, since or cutoff)
            window_days[c.id] = days
            if days < RUL_MIN_DAYS:
                rate[c.id] = None
                continue
            w = await mu.window(db, tenant_id, "component", [c.id], ws, we)
            rate[c.id] = {m: v / days for m, v in (w.get(c.id) or {}).items()}

    # failures that were removals by confirmed defect in a predictive WO (§2.1: reported apart)
    failed_ids = [c.id for c in comps if c.removed_at is not None and c.removed_at <= cutoff
                  and c.removal_reason is not None and c.removal_reason.value == "failure"]
    predictive_removed: set[str] = set()
    if failed_ids:
        predictive_removed = set((await db.execute(
            select(WorkOrderReplacement.removed_component_id).join(
                WorkOrder, WorkOrder.id == WorkOrderReplacement.work_order_id).where(
                WorkOrderReplacement.tenant_id == tenant_id, WorkOrder.tenant_id == tenant_id,
                WorkOrder.type == "predictive", WorkOrderReplacement.removed_component_id.in_(failed_ids)))
        ).scalars().all())

    by_type: dict[str, list[Component]] = defaultdict(list)
    for c in comps:
        by_type[c.component_type.value].append(c)

    types_out = []
    risk: list[dict] = []
    for ctype, cs in sorted(by_type.items()):
        life = [core.ComponentLife(c.id, c.manufacturer, c.model, ctype, c.nominal_life or {}, {}, False, False, None)
                for c in cs]
        measure = core.life_measure_of(life)
        excluded = []
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for c in cs:
            loc = where.get(c.position_id or c.last_position_id, {})
            removed_before_cut = c.removed_at is not None and c.removed_at <= cutoff
            usage = totals[c.id] if removed_before_cut else at_cut.get(c.id, {"cycles": 0.0, "hours": 0.0, "starts": 0.0})
            unit = {"component_id": c.id, "serial_number": c.serial_number, "manufacturer": c.manufacturer,
                    "model": c.model, "component_type": ctype,
                    "installed_at": c.installed_at.isoformat() if c.installed_at else None,
                    "removed_at": c.removed_at.isoformat() if removed_before_cut else None,
                    "removal_reason": c.removal_reason.value if (removed_before_cut and c.removal_reason) else None,
                    "in_service": not removed_before_cut, "position_name": loc.get("position_name"),
                    "machine_id": loc.get("machine_id"), "machine_name": loc.get("machine_name"),
                    "measure": measure, "age": None, "failed": False, "excluded_reason": None}
            if measure is None:
                unit["excluded_reason"] = "no_measure"
                excluded.append(unit)
                continue
            age = float(usage.get(measure, 0.0) or 0.0)
            unit["age"] = age
            unit["failed"] = bool(removed_before_cut and c.removal_reason is not None
                                  and c.removal_reason.value == "failure")
            if age <= 0:
                unit["excluded_reason"] = "no_usage"
                excluded.append(unit)
                continue
            if not removed_before_cut:
                r_c = rate.get(c.id, {})
                unit["rate_per_day"] = None if r_c is None else r_c.get(measure, 0.0)
                unit["rate_window_days"] = window_days.get(c.id)
                unit["rate_low_confidence"] = (window_days.get(c.id) or 0.0) < RATE_WINDOW_DAYS
            groups[(c.manufacturer or "—", c.model or "—")].append(unit)

        grows = []
        for (man, mod), units in sorted(groups.items()):
            fails = [u["age"] for u in units if u["failed"]]
            susp = [u["age"] for u in units if not u["failed"]]
            nominal, mixed = _nominal_of([c for c in cs if (c.manufacturer or "—", c.model or "—") == (man, mod)],
                                         measure)
            res = weibull.analyze(fails, susp, nominal)
            notes = []
            if res["low_confidence"]:
                notes.append("low_confidence")
            if res["reason"] == "insufficient_failures":
                notes.append("insufficient_failures")
            removed_no_fail = sum(1 for u in units if not u["failed"] and not u["in_service"])
            from_pred = sum(1 for u in units if u["failed"] and u["component_id"] in predictive_removed)
            if from_pred:
                notes.append("predictive_failures")
            g = {"key": f"{ctype}|{man}|{mod}", "component_type": ctype, "manufacturer": man, "model": mod,
                 "comparable": man != "—" and mod != "—", "measure": measure, "unit": MEASURE_UNIT.get(measure),
                 **res, "suspensions_removed": removed_no_fail,
                 "suspensions_in_service": sum(1 for u in units if u["in_service"]),
                 "failures_from_predictive": from_pred,
                 "nominal_life_mixed": mixed, "notes": notes,
                 "plot": weibull.plot_points(fails, susp),
                 "fit_line": (weibull.fit_line(res["beta"], res["eta"], min(u["age"] for u in units),
                                               max(u["age"] for u in units), steps=2)
                              if res["beta"] is not None else []),
                 "units": sorted(units, key=lambda u: u["age"])}
            grows.append(g)
            if res["beta"] is not None:
                for u in units:
                    if u["in_service"]:
                        cp = weibull.conditional_failure(u["age"], u.get("rate_per_day"), res["beta"], res["eta"],
                                                         HORIZON_DAYS)
                        risk.append({**{k: u[k] for k in ("component_id", "serial_number", "manufacturer", "model",
                                                          "component_type", "position_name", "machine_id",
                                                          "machine_name", "measure", "age")},
                                     "rate_per_day": u.get("rate_per_day"), "days": HORIZON_DAYS,
                                     "rate_window_days": u.get("rate_window_days"),
                                     "low_confidence": bool(u.get("rate_low_confidence")),
                                     "p_fail": cp["p_fail"],
                                     "p_reason": "insufficient_data" if u.get("rate_per_day") is None else cp["reason"],
                                     "group_key": g["key"]})
        types_out.append({"component_type": ctype, "label": TYPE_PT.get(ctype, ctype), "measure": measure,
                          "unit": MEASURE_UNIT.get(measure), "groups": grows, "excluded": excluded,
                          "comparison": compare_groups(grows)})
    risk.sort(key=lambda r: (r["p_fail"] is None, -(r["p_fail"] or 0.0)))
    return {"cutoff": cutoff.isoformat(), "plant_id": plant_id, "component_type": component_type,
            "horizon_days": HORIZON_DAYS, "z": weibull.Z90, "confidence": 0.90, "types": types_out,
            "in_service_risk": risk[:top_in_service], "in_service_risk_total": len(risk),
            "method": "MLE com censura à direita; IC 90 % pela matriz de Fisher (transformação log); "
                      "ranks de Johnson e aproximação de Bernard no papel de Weibull (IEC 61649)"}


def compare_groups(groups: list[dict]) -> list[dict]:
    """§1.5: 'significant' only when the η 90 % CIs do not overlap (conservative); groups '—' are left out."""
    fitted = [g for g in groups if g["comparable"] and g["eta"] is not None and g.get("eta_lo") is not None]
    out = []
    for i in range(len(fitted)):
        for j in range(i + 1, len(fitted)):
            a, b = fitted[i], fitted[j]
            overlap = weibull.eta_ci_overlap(a, b)
            if overlap is None:
                continue
            if overlap:
                text = "sem diferença demonstrada com os dados atuais"
            else:
                longer = a if a["eta"] > b["eta"] else b
                text = (f"diferença significativa: {longer['manufacturer']} {longer['model']} dura mais "
                        f"(IC 90 % de η sem sobreposição)")
            out.append({"a": a["key"], "b": b["key"], "significant": not overlap, "text": text})
    return out
