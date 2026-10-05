"""
Short-lived in-process cache of the asset topology used by the MQTT hot path.

Every reading needs the tag (tenant check, role, position/machine) and, for semantic tags, the
topology of its machine (positions, tags, component install windows). Loading them from Postgres
costs 4-7 round trips per reading, which capped the subscriber below the rate a gateway publishes
in accelerated mode (QA: ~75 msg/s processed vs ~90 msg/s published).

Correctness:
  * Only positive lookups are cached (a tag created after a rejected reading is accepted at once).
  * Any committed ORM change to Tag / Position / Component / Machine in this process clears the
    cache (SQLAlchemy session events, after commit). Install/remove/link changes therefore take
    effect for the very next reading.
  * A load that started before an invalidation is not stored (generation check), so a reader can
    never re-populate the cache with pre-commit data.
  * Changes made by another process (another worker, psql) are picked up within ``ttl_s``.
"""

from __future__ import annotations

import time
from itertools import chain
from typing import Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...config import settings
from ...models import Component, Machine, Position, Tag
from .topology import TagInfo, Topology, load_tag_info, load_topology

_WATCHED = (Tag, Position, Component, Machine)
_MAX_ENTRIES = 50_000


class TopologyCache:
    def __init__(self, ttl_s: float) -> None:
        self.ttl_s = ttl_s
        self._tags: dict[tuple[str, str], tuple[float, TagInfo]] = {}
        self._topos: dict[tuple[str, tuple[str, ...]], tuple[float, Topology]] = {}
        self.generation = 0
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.ttl_s > 0

    def invalidate(self) -> None:
        self.generation += 1
        self._tags.clear()
        self._topos.clear()

    def _get(self, store: dict, key):
        hit = store.get(key)
        if hit is not None and hit[0] > time.monotonic():
            self.hits += 1
            return hit[1]
        if hit is not None:
            store.pop(key, None)
        self.misses += 1
        return None

    def _put(self, store: dict, key, value, generation: int) -> None:
        if generation != self.generation or value is None:
            return
        if len(store) >= _MAX_ENTRIES:
            store.clear()
        store[key] = (time.monotonic() + self.ttl_s, value)

    async def tag_info(self, db: AsyncSession, tenant_id: str, tag_id: str) -> Optional[TagInfo]:
        if not self.enabled:
            return await load_tag_info(db, tenant_id, tag_id)
        key = (tenant_id, tag_id)
        info = self._get(self._tags, key)
        if info is not None:
            return info
        gen = self.generation
        info = await load_tag_info(db, tenant_id, tag_id)
        self._put(self._tags, key, info, gen)
        return info

    async def topology(self, db: AsyncSession, tenant_id: str, machine_ids: list[str]) -> Topology:
        if not self.enabled:
            return await load_topology(db, tenant_id, machine_ids)
        key = (tenant_id, tuple(sorted(machine_ids)))
        topo = self._get(self._topos, key)
        if topo is not None:
            return topo
        gen = self.generation
        topo = await load_topology(db, tenant_id, machine_ids)
        self._put(self._topos, key, topo, gen)
        return topo


topology_cache = TopologyCache(settings.usage_topology_cache_ttl_s)


# ── invalidation on committed asset changes ─────────────────────────────────

@event.listens_for(Session, "after_flush")
def _mark_dirty(session: Session, _ctx) -> None:
    if any(isinstance(o, _WATCHED) for o in chain(session.new, session.dirty, session.deleted)):
        session.info["topology_dirty"] = True


@event.listens_for(Session, "do_orm_execute")
def _mark_bulk(state) -> None:
    if (state.is_update or state.is_delete) and any(
            m.class_ in _WATCHED for m in state.all_mappers):
        state.session.info["topology_dirty"] = True


@event.listens_for(Session, "after_commit")
def _invalidate(session: Session) -> None:
    if session.info.pop("topology_dirty", False):
        topology_cache.invalidate()


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("topology_dirty", None)
