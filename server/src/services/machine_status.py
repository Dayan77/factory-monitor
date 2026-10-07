"""
Machine status derived from data.

Rules (Fase 0 behaviour; machines with a machine-level run_state tag get running/stopped from
that tag instead — see services/usage/processor.py derive_machine_status, domain.md §1.1):
  * A reading arrives for any tag of a machine  → last_seen_at = the reading's
    source timestamp (payload `ts`, clamped by the MQTT subscriber; never moved
    backwards) and, if the reading is fresh (source ts within
    machine_offline_timeout_s of the server receive time) and the machine was
    'unknown' or 'stopped', it becomes 'running'. Readings replayed from an edge
    backlog (old ts) refresh nothing but last_seen_at and never flip the status.
  * 'maintenance' and 'error' are operator-declared states: incoming data only
    refreshes last_seen_at, it never overrides them.
  * No reading for > settings.machine_offline_timeout_s → a machine that is
    'running' or 'stopped' becomes 'unknown' (offline). This includes machines
    that were never seen (last_seen_at IS NULL) but claim to be running.

Edge dependency: this only works because the edge DeadbandFilter re-publishes
unchanged values after `deadband_max_silence_s` (default 30 s, see
edge/src/filters/deadband.py). That heartbeat must stay well below
machine_offline_timeout_s or healthy gateways reading stable values will be
reported offline. (Fase 1: dedicated gateway heartbeat topic + run_state tags.)

Every status change is broadcast to the tenant's WebSocket clients:

    {"type": "machine_status", "machine_id": "<uuid>", "machine_name": "...",
     "status": "running|unknown", "previous_status": "...",
     "last_seen_at": "<ISO-8601 UTC>|null", "timestamp": "<ISO-8601 UTC>"}
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..websocket.manager import manager
from .validation import is_uuid

log = structlog.get_logger()


@dataclass(frozen=True)
class StatusChange:
    tenant_id: str
    machine_id: str
    machine_name: str
    status: str
    previous_status: str
    last_seen_at: Optional[datetime]

    def to_message(self) -> dict:
        return {
            "type": "machine_status",
            "machine_id": self.machine_id,
            "machine_name": self.machine_name,
            "status": self.status,
            "previous_status": self.previous_status,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# One atomic statement: lock the machine owning the tag (same tenant), keep the
# previous status, and update only when something actually changes (status) or
# last_seen_at is older than the write interval — limits writes to ~1 per
# machine every few seconds no matter how many tags it has. last_seen_at never
# moves backwards (GREATEST) and only fresh readings flip the status.
_MARK_SEEN_SQL = text("""
WITH prev AS (
    SELECT m.id, m.status AS old_status,
           EXISTS (SELECT 1 FROM tags rt
                    WHERE rt.machine_id = m.id AND rt.semantic_role = 'run_state'
                      AND rt.position_id IS NULL AND rt.is_active) AS has_run_state
      FROM machines m
      JOIN tags t ON t.machine_id = m.id
     WHERE t.id = CAST(:tag_id AS uuid)
       AND t.tenant_id = CAST(:tenant_id AS uuid)
       AND m.tenant_id = CAST(:tenant_id AS uuid)
       FOR UPDATE OF m
)
UPDATE machines m
   SET last_seen_at = GREATEST(COALESCE(m.last_seen_at, :seen_at), :seen_at),
       status = CASE WHEN CAST(:fresh AS boolean) AND m.status IN ('unknown', 'stopped')
                          AND NOT prev.has_run_state
                     THEN 'running'::machinestatus ELSE m.status END
  FROM prev
 WHERE m.id = prev.id
   AND ((CAST(:fresh AS boolean) AND m.status IN ('unknown', 'stopped') AND NOT prev.has_run_state)
        OR m.last_seen_at IS NULL
        OR m.last_seen_at < :write_before)
RETURNING m.id::text, m.tenant_id::text, m.name, m.status::text,
          prev.old_status::text, m.last_seen_at
""")

# Sprint 7 (scale): _MARK_SEEN_SQL for several machines of one tenant in one statement (one row per machine: the
# reading it would end with), rows locked in machine-id order.
_MARK_SEEN_MANY_SQL = text("""
WITH inp AS (
    SELECT i.tag_id, i.seen_at, i.fresh, i.write_before
      FROM unnest(CAST(:tag_ids AS uuid[]), CAST(:seen AS timestamptz[]), CAST(:fresh AS boolean[]),
                  CAST(:write_before AS timestamptz[])) AS i(tag_id, seen_at, fresh, write_before)
), prev AS (
    SELECT m.id, m.status AS old_status, inp.seen_at, inp.fresh, inp.write_before,
           EXISTS (SELECT 1 FROM tags rt
                    WHERE rt.machine_id = m.id AND rt.semantic_role = 'run_state'
                      AND rt.position_id IS NULL AND rt.is_active) AS has_run_state
      FROM inp
      JOIN tags t ON t.id = inp.tag_id
      JOIN machines m ON m.id = t.machine_id
     WHERE t.tenant_id = CAST(:tenant_id AS uuid)
       AND m.tenant_id = CAST(:tenant_id AS uuid)
     ORDER BY m.id
       FOR UPDATE OF m
)
UPDATE machines m
   SET last_seen_at = GREATEST(COALESCE(m.last_seen_at, prev.seen_at), prev.seen_at),
       status = CASE WHEN prev.fresh AND m.status IN ('unknown', 'stopped') AND NOT prev.has_run_state
                     THEN 'running'::machinestatus ELSE m.status END
  FROM prev
 WHERE m.id = prev.id
   AND ((prev.fresh AND m.status IN ('unknown', 'stopped') AND NOT prev.has_run_state)
        OR m.last_seen_at IS NULL
        OR m.last_seen_at < prev.write_before)
RETURNING m.id::text, m.tenant_id::text, m.name, m.status::text,
          prev.old_status::text, m.last_seen_at
""")


async def mark_seen_many(db: AsyncSession, tenant_id: str,
                         readings: list[tuple[str, datetime, datetime]]) -> list[StatusChange]:
    """mark_seen for several machines of one tenant: `readings` = [(tag_id, seen_at, now)], at most one per machine
    (the caller picks the one mark_seen would end with). One statement; the caller commits. Returns the status
    changes."""
    rows = [(tag_id, min(seen_at or now, now), now) for tag_id, seen_at, now in readings
            if is_uuid(tag_id)]
    if not rows or not is_uuid(tenant_id):
        return []
    off = timedelta(seconds=settings.machine_offline_timeout_s)
    wi = timedelta(seconds=settings.machine_last_seen_write_interval_s)
    result = await db.execute(_MARK_SEEN_MANY_SQL, {
        "tenant_id": tenant_id, "tag_ids": [r[0] for r in rows], "seen": [r[1] for r in rows],
        "fresh": [r[1] >= r[2] - off for r in rows], "write_before": [r[1] - wi for r in rows]})
    out = []
    for row in result.all():
        change = _row_to_change(row)
        if change.status != change.previous_status:
            out.append(change)
    return out


_SWEEP_SQL = text("""
WITH stale AS (
    SELECT id, status AS old_status
      FROM machines
     WHERE status IN ('running', 'stopped')
       AND (last_seen_at IS NULL OR last_seen_at < :cutoff)
       FOR UPDATE SKIP LOCKED
)
UPDATE machines m
   SET status = 'unknown'::machinestatus
  FROM stale
 WHERE m.id = stale.id
RETURNING m.id::text, m.tenant_id::text, m.name, m.status::text,
          stale.old_status::text, m.last_seen_at
""")


def _row_to_change(row) -> StatusChange:
    mid, tid, name, new_status, old_status, last_seen = row
    return StatusChange(
        tenant_id=tid, machine_id=mid, machine_name=name,
        status=new_status, previous_status=old_status, last_seen_at=last_seen,
    )


async def mark_seen(
    db: AsyncSession,
    tenant_id: str,
    tag_id: str,
    now: Optional[datetime] = None,
    seen_at: Optional[datetime] = None,
    commit: bool = True,
) -> Optional[StatusChange]:
    """Records that a reading arrived for tag_id. Returns the status change, if any.

    ``now`` is the server receive time; ``seen_at`` the reading's source
    timestamp (defaults to ``now``). A reading older than
    machine_offline_timeout_s (an edge backlog replay) only advances
    last_seen_at — it never turns the machine 'running'.

    Commits the session. Unknown tags, tags of another tenant and tags without
    a machine are silently ignored.
    """
    if not (is_uuid(tenant_id) and is_uuid(tag_id)):
        return None
    now = now or datetime.now(timezone.utc)
    seen_at = min(seen_at or now, now)
    fresh = seen_at >= now - timedelta(seconds=settings.machine_offline_timeout_s)
    write_before = seen_at - timedelta(seconds=settings.machine_last_seen_write_interval_s)
    result = await db.execute(
        _MARK_SEEN_SQL,
        {"tag_id": tag_id, "tenant_id": tenant_id, "seen_at": seen_at,
         "fresh": fresh, "write_before": write_before},
    )
    row = result.first()
    if commit:      # commit=False: the caller commits (the MQTT batch marks several machines in one transaction)
        await db.commit()
    if row is None:
        return None
    change = _row_to_change(row)
    if change.status == change.previous_status:
        return None
    return change


async def sweep_offline(
    db: AsyncSession,
    now: Optional[datetime] = None,
    timeout_s: Optional[int] = None,
) -> list[StatusChange]:
    """Marks machines with no data for > timeout_s as 'unknown'. Commits."""
    now = now or datetime.now(timezone.utc)
    timeout = settings.machine_offline_timeout_s if timeout_s is None else timeout_s
    cutoff = now - timedelta(seconds=timeout)
    result = await db.execute(_SWEEP_SQL, {"cutoff": cutoff})
    rows = result.all()
    await db.commit()
    return [_row_to_change(r) for r in rows]


async def broadcast_change(change: StatusChange) -> None:
    log.info(
        "machine.status_changed",
        machine_id=change.machine_id, status=change.status, previous=change.previous_status,
    )
    await manager.broadcast(change.tenant_id, change.to_message())


async def run_offline_sweeper() -> None:
    """Long-running task started on app lifespan."""
    from ..db import AsyncSessionLocal  # local import: keeps module importable in tests

    interval = settings.machine_status_sweep_interval_s
    while True:
        try:
            async with AsyncSessionLocal() as db:
                for change in await sweep_offline(db):
                    await broadcast_change(change)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("machine.sweeper.error", error=str(e))
        await asyncio.sleep(interval)
