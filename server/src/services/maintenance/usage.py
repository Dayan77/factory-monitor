"""Usage of plan anchors (position or machine) read from the usage accumulators (domain.md §3.5).
API units: cycles, hours (stored as operating_seconds), starts. All queries filter tenant_id."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Line, Machine, Plant, Position, UsageBucket, UsageTotal
from ..usage.queries import tzinfo, window_sums
from .core import RATE_WINDOW_DAYS

HOURS = "operating_seconds"
STORED = {"cycles": "cycles", "hours": HOURS, "starts": "starts"}


def to_api(vals: dict[str, float]) -> dict[str, float]:
    return {"cycles": vals.get("cycles", 0.0), "hours": vals.get(HOURS, 0.0) / 3600.0,
            "starts": vals.get("starts", 0.0)}


def _group(rows: dict[tuple[str, str], float], ids: Iterable[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {i: {} for i in ids}
    for (eid, m), v in rows.items():
        out.setdefault(eid, {})[m] = v
    return {k: to_api(v) for k, v in out.items()}


async def totals(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str]) -> dict[str, dict[str, float]]:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = (await db.execute(
        select(UsageTotal.entity_id, UsageTotal.measure, UsageTotal.value)
        .where(UsageTotal.tenant_id == tenant_id, UsageTotal.scope == scope, UsageTotal.entity_id.in_(ids))
    )).all()
    return _group({(e, m): float(v) for e, m, v in rows}, ids)


async def window(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str], start: datetime,
                 stop: datetime) -> dict[str, dict[str, float]]:
    ids = list(set(ids))
    return _group(await window_sums(db, tenant_id, scope, ids, start, stop), ids)


async def measures_present(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str], start: datetime,
                           stop: datetime) -> dict[str, set[str]]:
    """Which measures have any bucket row in the window (distinguishes 'no cycle source' from 0)."""
    ids = list(set(ids))
    if not ids:
        return {}
    rows = (await db.execute(
        select(UsageBucket.entity_id, UsageBucket.measure).distinct()
        .where(UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id.in_(ids),
               UsageBucket.bucket_start >= start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0),
               UsageBucket.bucket_start < stop)
    )).all()
    out: dict[str, set[str]] = {i: set() for i in ids}
    inv = {v: k for k, v in STORED.items()}
    for e, m in rows:
        if m in inv:
            out.setdefault(e, set()).add(inv[m])
    return out


async def rate_window(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str], now: datetime
                      ) -> dict[str, dict[str, float]]:
    """Usage of the last 14 × 24 h ending now (C17)."""
    return await window(db, tenant_id, scope, ids, now - timedelta(days=RATE_WINDOW_DAYS), now + timedelta(seconds=1))


async def data_days(db: AsyncSession, tenant_id: str, scope: str, ids: Iterable[str], now: datetime
                    ) -> dict[str, float]:
    """Days with data behind the rate window (kpis.md §4.2, audit A-3): min(14, now − first usage bucket of the
    entity). An entity without any bucket → 0 (no pace yet)."""
    ids = list(set(ids))
    if not ids:
        return {}
    rows = (await db.execute(
        select(UsageBucket.entity_id, func.min(UsageBucket.bucket_start))
        .where(UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id.in_(ids))
        .group_by(UsageBucket.entity_id))).all()
    first = {e: b for e, b in rows}
    out = {}
    for i in ids:
        b = first.get(i)
        out[i] = 0.0 if b is None else min(float(RATE_WINDOW_DAYS), max((now - b).total_seconds() / 86400.0, 0.0))
    return out


def _ceil_hour(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    floor = ts.replace(minute=0, second=0, microsecond=0)
    return floor if floor == ts else floor + timedelta(hours=1)


async def usage_at(db: AsyncSession, tenant_id: str, scope: str, entity_id: str, at: datetime,
                   now: datetime) -> dict[str, float]:
    """usage_total of the entity at instant `at` = total now − buckets revealed after `at`
    (hourly buckets, C9: a bucket holds the deltas revealed by readings within that hour)."""
    total = (await totals(db, tenant_id, scope, [entity_id])).get(entity_id, to_api({}))
    if at >= now:
        return total
    later = (await db.execute(
        select(UsageBucket.measure, func.sum(UsageBucket.value))
        .where(UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id == entity_id,
               UsageBucket.bucket_start >= _ceil_hour(at))
        .group_by(UsageBucket.measure)
    )).all()
    after = to_api({m: float(v) for m, v in later})
    return {m: total[m] - after.get(m, 0.0) for m in total}


async def machine_tz(db: AsyncSession, tenant_id: str, machine_ids: Iterable[str]) -> dict[str, ZoneInfo]:
    ids = list(set(machine_ids))
    if not ids:
        return {}
    rows = (await db.execute(
        select(Machine.id, Plant.timezone).join(Line, Line.id == Machine.line_id).join(Plant, Plant.id == Line.plant_id)
        .where(Machine.tenant_id == tenant_id, Machine.id.in_(ids))
    )).all()
    return {mid: tzinfo(tz) for mid, tz in rows}


async def tenant_tz(db: AsyncSession, tenant_id: str, plant_id: Optional[str] = None) -> ZoneInfo:
    q = select(Plant.timezone).where(Plant.tenant_id == tenant_id)
    if plant_id:
        q = q.where(Plant.id == plant_id)
    name = (await db.execute(q.order_by(Plant.created_at, Plant.id).limit(1))).scalar_one_or_none()
    return tzinfo(name)


async def position_machine(db: AsyncSession, tenant_id: str, position_id: str) -> Optional[str]:
    return (await db.execute(select(Position.machine_id).where(Position.id == position_id,
                                                               Position.tenant_id == tenant_id))).scalar_one_or_none()
