"""Read side of the usage accumulators (all queries filter tenant_id)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Component, Line, Machine, Plant, Tag, UsageBucket, UsageTotal
from ...models.plant import DEFAULT_TIMEZONE
from ...models.tag import SemanticRole
from .core import RUL_WINDOW_DAYS, life_consumed, rul

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


async def component_usage_detail(db: AsyncSession, tenant_id: str, component: Component,
                                 tz: ZoneInfo, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    usage = (await component_usages(db, tenant_id, [component]))[component.id]
    life, life_measure = component_life(component, usage)
    if component.installed_at is not None:
        history_days = (now - component.installed_at).total_seconds() / 86400.0
    else:
        history_days = 0.0
    window_days = min(float(RUL_WINDOW_DAYS), max(history_days, 0.0))
    wsum = await window_sums(db, tenant_id, "component", [component.id],
                             now - timedelta(days=window_days), now + timedelta(seconds=1))
    window_usage = _usage_dict({m: v for (eid, m), v in wsum.items()})
    r = rul(now.astimezone(tz).date(), _nominal(component), usage, window_usage, history_days)
    return {
        **usage,
        "life_consumed_pct": life,
        "life_measure": life_measure,
        "life_reason": None if life is not None else "no_nominal_life",
        "projected_end_date": r.date,
        "rul_days": r.days,
        "rul_measure": r.measure,
        "rul_reason": r.reason,
        "rate_per_day": r.rate_per_day,
        "low_confidence": r.low_confidence,
        "overdue": r.overdue,
    }


def position_usage(vals: dict[str, float]) -> dict[str, float]:
    return {"cycles_total": vals.get("cycles", 0.0), "hours_total": vals.get(HOURS, 0.0) / 3600.0,
            "starts_total": vals.get("starts", 0.0)}

