"""Per-tag watermarks and late-data marks written by the ingestion (Sprint 5b, predictive.md P-12).

The reading processor calls `note()` once a reading is stored in Influx. In memory, per (tenant, tag): the highest
source timestamp written and — for readings written long after their own timestamp (gateway store-and-forward
flush, backlog), i.e. possibly after the predictive engine already extracted their window — the range of those
late timestamps. `flush()` upserts that into `ingest_watermarks` (every `ingest_watermark_flush_s` by the
subscriber, and by the engine before it looks), so the marks survive a restart and reach the engine even when it
runs in its own process.

The engine moves the marks into `predictive_dirty` (one row per affected monitor and range, `transfer_late()`), and
each monitor run re-extracts the stored windows that a dirty range touches (valid or not) and refolds its detectors.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ..metrics import INGEST_LATE

log = structlog.get_logger()

# (tenant_id, tag_id) -> [machine_id, max_ts, late_min, late_max, late_count]
_pending: dict[tuple[str, str], list] = {}


def note(tenant_id: str, tag_id: str, machine_id: Optional[str], ts: datetime, late: bool) -> None:
    k = (tenant_id, tag_id)
    e = _pending.get(k)
    if e is None:
        e = _pending[k] = [machine_id, ts, None, None, 0]
    else:
        e[0] = machine_id or e[0]
        if ts > e[1]:
            e[1] = ts
    if late:
        INGEST_LATE.inc()
        e[2] = ts if e[2] is None or ts < e[2] else e[2]
        e[3] = ts if e[3] is None or ts > e[3] else e[3]
        e[4] += 1


def pending() -> int:
    return len(_pending)


_UPSERT = text("""
INSERT INTO ingest_watermarks (tenant_id, tag_id, machine_id, max_ts, late_min_ts, late_max_ts, late_count, updated_at)
VALUES (:tenant_id, :tag_id, :machine_id, :max_ts, :late_min, :late_max, :late_count, now())
ON CONFLICT (tenant_id, tag_id) DO UPDATE SET
  machine_id  = COALESCE(EXCLUDED.machine_id, ingest_watermarks.machine_id),
  max_ts      = GREATEST(ingest_watermarks.max_ts, EXCLUDED.max_ts),
  late_min_ts = LEAST(ingest_watermarks.late_min_ts, EXCLUDED.late_min_ts),
  late_max_ts = GREATEST(ingest_watermarks.late_max_ts, EXCLUDED.late_max_ts),
  late_count  = ingest_watermarks.late_count + EXCLUDED.late_count,
  updated_at  = now()
""")


async def flush(db: AsyncSession) -> int:
    """Writes the in-memory watermarks (caller commits). On failure they are merged back for the next try."""
    if not _pending:
        return 0
    items = list(_pending.items())
    _pending.clear()
    rows = [{"tenant_id": t, "tag_id": g, "machine_id": e[0], "max_ts": e[1], "late_min": e[2], "late_max": e[3],
             "late_count": e[4]} for (t, g), e in sorted(items)]       # sorted: same lock order in every flush
    try:
        await db.execute(_UPSERT, rows)
    except BaseException:
        for (t, g), e in items:
            note(t, g, e[0], e[1], False)
            cur = _pending[(t, g)]
            if e[2] is not None:
                cur[2] = e[2] if cur[2] is None or e[2] < cur[2] else cur[2]
                cur[3] = e[3] if cur[3] is None or e[3] > cur[3] else cur[3]
                cur[4] += e[4]
        raise
    return len(rows)


async def run_flusher() -> None:
    """Background task of the subscriber: watermarks → Postgres every ingest_watermark_flush_s."""
    from ...db import AsyncSessionLocal
    while True:
        await asyncio.sleep(settings.ingest_watermark_flush_s)
        if not _pending:
            continue
        try:
            async with AsyncSessionLocal() as db:
                await flush(db)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — kept in memory, retried next interval
            log.warning("ingest.watermarks.flush_failed", error=str(e)[:200], error_type=type(e).__name__)


async def flush_now() -> None:
    """Final flush (shutdown)."""
    from ...db import AsyncSessionLocal
    if not _pending:
        return
    async with AsyncSessionLocal() as db:
        await flush(db)
        await db.commit()


_TAKE = text("""
WITH c AS (
  SELECT tenant_id, tag_id, machine_id, late_min_ts, late_max_ts FROM ingest_watermarks
  WHERE late_min_ts IS NOT NULL {flt} ORDER BY tenant_id, tag_id FOR UPDATE
)
UPDATE ingest_watermarks w SET late_min_ts = NULL, late_max_ts = NULL, late_count = 0
FROM c WHERE w.tenant_id = c.tenant_id AND w.tag_id = c.tag_id
RETURNING c.tenant_id, c.tag_id, c.machine_id, c.late_min_ts, c.late_max_ts
""")


async def take_late(db: AsyncSession, tenant_id: Optional[str] = None) -> list[tuple]:
    """Atomically reads and clears the late marks (all tenants, or one). Caller commits in the same transaction
    as whatever it records from them."""
    sql = text(_TAKE.text.format(flt="AND tenant_id = :tenant_id" if tenant_id else ""))
    res = await db.execute(sql, {"tenant_id": tenant_id} if tenant_id else {})
    return [tuple(r) for r in res.all()]
