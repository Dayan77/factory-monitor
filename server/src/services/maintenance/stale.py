"""S7-23 — "falha aberta há muito tempo — verificar": ONE predicate for the plant map/tablet/Raio-X
(`layout.state.load_facts`) and the failures API (`open_stale`), kpis.md S7-23.

A FailureRecord is *stale* only when the data contradicts it — age alone is not enough (a real breakdown waiting
for a spare part over a weekend lasts > 72 h and must stay "falha"):

  * no `restored_at`, severity is not `incipient` (an incipient record never puts the machine in failure,
    ISO 14224: the function was not lost), and open for longer than FAILURE_STALE_OPEN_H; AND
  * contradicting evidence: its linked downtime event is already closed (the stop it caused has ended), or the
    machine is running now (fresh `running` status reported after `failed_at`).

A record whose linked downtime event is still open is never stale (the stop confirms the failure).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import DowntimeEvent, FailureRecord, Machine


def _status(v) -> Optional[str]:
    return getattr(v, "value", v)


def machine_running_since(machine: Optional[Machine], since: datetime, now: datetime) -> bool:
    """The machine reports `running` now (fresh, not offline) and that report is newer than `since`."""
    if machine is None or _status(machine.status) != "running" or machine.last_seen_at is None:
        return False
    if now - machine.last_seen_at > timedelta(seconds=settings.machine_offline_timeout_s):
        return False
    return machine.last_seen_at > since


def is_open_stale(fr: FailureRecord, now: datetime, *, linked_event_open: Optional[bool],
                  machine_running: bool) -> bool:
    """`linked_event_open`: None = no linked downtime event (or not found), True/False = it is open/closed."""
    if fr.restored_at is not None or fr.severity == "incipient":
        return False
    if now - fr.failed_at <= timedelta(hours=settings.failure_stale_open_h):
        return False
    if linked_event_open is True:
        return False
    return linked_event_open is False or machine_running


async def stale_open_ids(db: AsyncSession, tenant_id: str, records: Iterable[FailureRecord], now: datetime,
                         machines: Optional[dict[str, Machine]] = None) -> set[str]:
    """Ids of `records` (tenant `tenant_id`) that are stale at `now` (two queries at most)."""
    old_after = timedelta(hours=settings.failure_stale_open_h)
    cand = [f for f in records if f.restored_at is None and f.severity != "incipient"
            and now - f.failed_at > old_after]
    if not cand:
        return set()
    ev_ids = list({f.downtime_event_id for f in cand if f.downtime_event_id})
    ev_open: dict[str, bool] = {}
    if ev_ids:
        for eid, end in (await db.execute(select(DowntimeEvent.id, DowntimeEvent.end).where(
                DowntimeEvent.tenant_id == tenant_id, DowntimeEvent.id.in_(ev_ids)))).all():
            ev_open[eid] = end is None
    machines = dict(machines or {})
    missing = list({f.machine_id for f in cand} - set(machines))
    if missing:
        for m in (await db.execute(select(Machine).where(
                Machine.tenant_id == tenant_id, Machine.id.in_(missing)))).scalars():
            machines[m.id] = m
    return {f.id for f in cand
            if is_open_stale(f, now, linked_event_open=ev_open.get(f.downtime_event_id),
                             machine_running=machine_running_since(machines.get(f.machine_id), f.failed_at, now))}
