"""
Tenant-level advisory lock between the incremental usage pipeline and a recompute (apply=true).

* processor.process_reading takes it SHARED for its transaction: readings of a tenant still run
  in parallel with each other (the tag_states row lock serialises per tag).
* recompute takes it EXCLUSIVE for the final catch-up + replace transaction: live processing of
  that tenant waits (it is not lost) until the replaced accumulators are committed, and then
  continues from the replayed tag_states (already-replayed readings are duplicates/out of order).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NAMESPACE = 0x5553_4147   # "USAG": keeps these keys apart from other advisory-lock users


def tenant_usage_key(tenant_id: str) -> int:
    """Stable signed 64-bit key for pg_advisory_* (Postgres bigint)."""
    u = uuid.UUID(str(tenant_id)).int
    k = ((u >> 64) ^ (u & 0xFFFF_FFFF_FFFF_FFFF) ^ _NAMESPACE) & 0xFFFF_FFFF_FFFF_FFFF
    return k - (1 << 64) if k >= (1 << 63) else k


async def lock_tenant_usage_shared(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": tenant_usage_key(tenant_id)})


async def lock_tenant_usage_exclusive(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": tenant_usage_key(tenant_id)})


async def try_lock_tenant_usage_exclusive(db: AsyncSession, tenant_id: str) -> bool:
    return bool((await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                                  {"k": tenant_usage_key(tenant_id)})).scalar_one())
