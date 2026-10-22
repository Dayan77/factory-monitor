"""Read side of the usage accumulators (all queries filter tenant_id)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Component, Line, Machine, MachineDataSpan, Plant, Position, Tag, UsageBucket, UsageTotal
from ...models.plant import DEFAULT_TIMEZONE
from ...models.tag import SemanticRole
from .core import RUL_WINDOW_DAYS, life_consumed, rate_window, rul

HOURS = "operating_seconds"


def tzinfo(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_midnight_utc(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def machine_timezone(db: AsyncSession, tenant_id: str, machine_id: str) -> ZoneInfo:
    name = (await db.execute(
        select(Plant.timezone).join(Line, Line.plant_id == Plant.id).join(Machine, Machine.line_id == Line.id)
        .where(Machine.id == machine_id, Machine.tenant_id == tenant_id)
    )).scalar_one_or_none()
    return tzinfo(name)


async def totals(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str]) -> dict[tuple[str, str], float]:
    ids = list(ids)
    if not ids:
        return {}
    rows = (await db.execute(
        select(UsageTotal.entity_id, UsageTotal.measure, UsageTotal.value)
        .where(UsageTotal.tenant_id == tenant_id, UsageTotal.scope == scope, UsageTotal.entity_id.in_(ids))
    )).all()
    return {(eid, m): v for eid, m, v in rows}


async def window_sums(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str],
                      start: datetime, stop: datetime) -> dict[tuple[str, str], float]:
    ids = list(ids)
    if not ids:
        return {}
    rows = (await db.execute(
        select(UsageBucket.entity_id, UsageBucket.measure, func.sum(UsageBucket.value))
        .where(UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope,
               UsageBucket.entity_id.in_(ids), UsageBucket.bucket_start >= _hour(start),
               UsageBucket.bucket_start < stop)
        .group_by(UsageBucket.entity_id, UsageBucket.measure)
    )).all()
    return {(eid, m): float(v) for eid, m, v in rows}


def _hour(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


async def history(db: AsyncSession, tenant_id: str, scope: str, entity_id: str, measures: list[str],
                  start: datetime, stop: datetime, bucket: str, tz: ZoneInfo) -> dict[datetime, dict[str, float]]:
    """{bucket_start_utc: {measure: value}} — only buckets that have data (sparse; a missing
    bucket means no recorded usage, which can also mean no data)."""
    if bucket == "1d":
        rows = (await db.execute(text("""
            SELECT (date_trunc('day', bucket_start AT TIME ZONE :tz) AT TIME ZONE :tz) AS t,
                   measure, sum(value)
              FROM usage_buckets
             WHERE tenant_id = CAST(:tid AS uuid) AND scope = :scope AND entity_id = CAST(:eid AS uuid)
               AND measure = ANY(:measures) AND bucket_start >= :start AND bucket_start < :stop
             GROUP BY 1, 2 ORDER BY 1
        """), {"tz": tz.key, "tid": tenant_id, "scope": scope, "eid": entity_id,
               "measures": measures, "start": _hour(start), "stop": stop})).all()
    else:
        rows = (await db.execute(
            select(UsageBucket.bucket_start, UsageBucket.measure, UsageBucket.value)
            .where(UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope,
                   UsageBucket.entity_id == entity_id, UsageBucket.measure.in_(measures),
                   UsageBucket.bucket_start >= _hour(start), UsageBucket.bucket_start < stop)
            .order_by(UsageBucket.bucket_start)
        )).all()
    out: dict[datetime, dict[str, float]] = defaultdict(dict)
    for t, m, v in rows:
        out[t.astimezone(timezone.utc)][m] = float(v)
    return dict(sorted(out.items()))


# ── production (machine scope) ──────────────────────────────────────────────

async def machine_has_good_tag(db: AsyncSession, tenant_id: str, machine_id: str) -> bool:
    return (await db.execute(
        select(func.count()).select_from(Tag).where(
            Tag.tenant_id == tenant_id, Tag.machine_id == machine_id, Tag.is_active.is_(True),
            Tag.semantic_role == SemanticRole.part_counter_good)
    )).scalar_one() > 0


def production(values: dict[str, float], has_good_tag: bool) -> dict[str, float]:
    """good/scrap/total from stored measures. Without a part_counter_good tag, good = total − scrap."""
    scrap = values.get("scrap", 0.0)
    total = values.get("total", 0.0)
    good = values.get("good", 0.0) if has_good_tag else max(total - scrap, 0.0)
    return {"good": good, "scrap": scrap, "total": total}


# ── component usage / life / RUL ───────────────────────────────────────────

def _usage_dict(vals: dict[str, float]) -> dict[str, float]:
    return {"cycles": vals.get("cycles", 0.0), "hours": vals.get(HOURS, 0.0) / 3600.0,
            "starts": vals.get("starts", 0.0)}


def _nominal(component: Component) -> dict[str, float]:
    return {k: v for k, v in (component.nominal_life or {}).items()
            if k in ("cycles", "hours", "starts") and isinstance(v, (int, float)) and v > 0}


async def component_usages(db: AsyncSession, tenant_id: str, components: list[Component]
                           ) -> dict[str, dict[str, float]]:
    t = await totals(db, tenant_id, "component", [c.id for c in components])
    out = {}
    for c in components:
        out[c.id] = _usage_dict({m: v for (eid, m), v in t.items() if eid == c.id})
    return out


def component_life(component: Component, usage: dict[str, float]) -> tuple[Optional[float], Optional[str]]:
    return life_consumed(_nominal(component), usage)


async def data_since(db: AsyncSession, tenant_id: str, position_id: Optional[str] = None,
                     machine_id: Optional[str] = None) -> Optional[datetime]:
    """Since when the platform has data of a position / machine (audit A-2, S7-2): the earliest of the first usage
    bucket of the position, of the machine and the first data span of the machine. None: no data at all."""
    cands: list[datetime] = []
    for scope, eid in (("position", position_id), ("machine", machine_id)):
        if eid:
            v = (await db.execute(select(func.min(UsageBucket.bucket_start)).where(
                UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id == eid)
            )).scalar_one_or_none()
            if v is not None:
                cands.append(v)
    if machine_id:
        v = (await db.execute(select(func.min(MachineDataSpan.start)).where(
            MachineDataSpan.tenant_id == tenant_id, MachineDataSpan.machine_id == machine_id))).scalar_one_or_none()
        if v is not None:
            cands.append(v)
    return min(cands) if cands else None


async def component_data_since(db: AsyncSession, tenant_id: str, component: Component) -> Optional[datetime]:
    pid = component.position_id or component.last_position_id
    mid = None
    if pid:
        mid = (await db.execute(select(Position.machine_id).where(Position.id == pid, Position.tenant_id == tenant_id))
               ).scalar_one_or_none()
    first_own = (await db.execute(select(func.min(UsageBucket.bucket_start)).where(
        UsageBucket.tenant_id == tenant_id, UsageBucket.scope == "component", UsageBucket.entity_id == component.id)
    )).scalar_one_or_none()
    since = await data_since(db, tenant_id, pid, mid)
    return min(x for x in (since, first_own) if x is not None) if (since or first_own) else None


async def component_usage_detail(db: AsyncSession, tenant_id: str, component: Component,
                                 tz: ZoneInfo, now: Optional[datetime] = None) -> dict:
    return (await component_usage_details(db, tenant_id, [component], {component.id: tz}, now))[component.id]


_FIRST_BUCKETS_SQL = text("""
SELECT e.sc, e.eid::text,
       (SELECT min(b.bucket_start) FROM usage_buckets b
         WHERE b.scope = e.sc AND b.entity_id = e.eid AND b.tenant_id = CAST(:tid AS uuid)) AS first
  FROM unnest(CAST(:sc AS varchar[]), CAST(:eid AS uuid[])) AS e(sc, eid)
""")


async def first_buckets(db: AsyncSession, tenant_id: str, entities: Iterable[tuple[str, str]]
                        ) -> dict[tuple[str, str], Optional[datetime]]:
    """First usage bucket of each (scope, entity_id) — one statement, one index probe per entity
    (ix_usage_buckets_entity_start, migration c6d7e8f9a0b1)."""
    ents = sorted({(sc, str(e)) for sc, e in entities if e})
    if not ents:
        return {}
    rows = (await db.execute(_FIRST_BUCKETS_SQL, {"tid": tenant_id, "sc": [x[0] for x in ents],
                                                  "eid": [x[1] for x in ents]})).all()
    return {(sc, eid): first for sc, eid, first in rows}


async def component_usage_details(db: AsyncSession, tenant_id: str, components: list[Component],
                                  tz_of: dict[str, ZoneInfo], now: Optional[datetime] = None) -> dict[str, dict]:
    """component_usage_detail for many components with a fixed number of queries (Sprint 7, performance: it was
    ~7 queries per component — the plan/maintenance engine and /api/maintenance/components evaluate every component
    of a tenant). Same numbers: S7-2 history days from installation and the first data of the component, its
    position and its machine; S7-5 complete hourly buckets."""
    now = now or datetime.now(timezone.utc)
    if not components:
        return {}
    usages = await component_usages(db, tenant_id, components)
    pid_of = {c.id: (c.position_id or c.last_position_id) for c in components}
    pids = sorted({p for p in pid_of.values() if p})
    mid_of_pos = {pid: mid for pid, mid in (await db.execute(select(Position.id, Position.machine_id).where(
        Position.id.in_(pids), Position.tenant_id == tenant_id)))} if pids else {}
    mids = sorted({m for m in mid_of_pos.values() if m})
    firsts = await first_buckets(db, tenant_id, [("component", c.id) for c in components if c.installed_at]
                                 + [("position", p) for p in pids] + [("machine", m) for m in mids])
    spans = {mid: v for mid, v in (await db.execute(select(MachineDataSpan.machine_id, func.min(MachineDataSpan.start))
                                                    .where(MachineDataSpan.tenant_id == tenant_id,
                                                           MachineDataSpan.machine_id.in_(mids))
                                                    .group_by(MachineDataSpan.machine_id))).all()} if mids else {}
    plan: dict[str, tuple[datetime, datetime, float]] = {}
    for c in components:
        if c.installed_at is None:
            plan[c.id] = (now, now, 0.0)
            continue
        pid = pid_of[c.id]
        mid = mid_of_pos.get(pid) if pid else None
        cands = [x for x in (firsts.get(("component", c.id)), firsts.get(("position", pid)) if pid else None,
                             firsts.get(("machine", mid)) if mid else None, spans.get(mid) if mid else None)
                 if x is not None]
        since = min(cands) if cands else None
        plan[c.id] = rate_window(now, c.installed_at, since or now)
    by_window: dict[tuple[datetime, datetime], list[str]] = {}
    for cid, (ws, we, days) in plan.items():
        if days > 0:
            by_window.setdefault((ws, we), []).append(cid)
    wsums: dict[str, dict[str, float]] = {}
    for (ws, we), ids in by_window.items():
        for (eid, m), v in (await window_sums(db, tenant_id, "component", ids, ws, we)).items():
            wsums.setdefault(eid, {})[m] = v
    out = {}
    for c in components:
        usage = usages[c.id]
        life, life_measure = component_life(c, usage)
        history_days = plan[c.id][2]
        window_usage = _usage_dict(wsums.get(c.id, {}) if history_days > 0 else {})
        tz = tz_of.get(c.id) or ZoneInfo(DEFAULT_TIMEZONE)
        r = rul(now.astimezone(tz).date(), _nominal(c), usage, window_usage, history_days)
        out[c.id] = {
            **usage,
            "life_consumed_pct": life,
            "life_measure": life_measure,
            "life_reason": None if life is not None else "no_nominal_life",
            "projected_end_date": r.date,
            "rul_days": r.days,
            "rul_measure": r.measure,
            "rul_reason": r.reason,
            "rate_per_day": r.rate_per_day,
            "rate_window_days": history_days,
            "low_confidence": r.low_confidence,
            "overdue": r.overdue,
        }
    return out


def position_usage(vals: dict[str, float]) -> dict[str, float]:
    return {"cycles_total": vals.get("cycles", 0.0), "hours_total": vals.get(HOURS, 0.0) / 3600.0,
            "starts_total": vals.get("starts", 0.0)}

