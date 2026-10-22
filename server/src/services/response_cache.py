"""Short-lived response cache for the hot read endpoints (Sprint 7, docs/performance.md §6).

Dashboards poll the same few answers: the overview of a plant (TV mode, every open "Raio-X"), the plan status, the
OEE / Pareto of a line for a preset window. Recomputing each of them for every poll of every browser was most of the
API's CPU at scale. `@cached_endpoint(name)` (under the route decorator) keeps the answer for API_CACHE_TTL_S
seconds (default 10 s), per **user** (so RBAC, operator line scope and tenant isolation are exactly those of the
request that computed it) and per query; concurrent identical requests share one computation (single flight).

Freshness: any commit in this process that writes a row of a tenant (justifying a stop, closing a work order,
changing a plan …) drops that tenant's entries at once, so a user always sees their own changes. Data written by
the other processes (MQTT ingestion, detectors in the worker) shows up within the TTL — the same order as the
detectors' own cadence (DOWNTIME_TICK_S). API_CACHE_TTL_S=0 turns it off (the test suite does).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable

from prometheus_client import Counter
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..config import settings

CACHE_REQUESTS = Counter("fm_api_cache_requests_total", "Cached endpoint lookups", ["endpoint", "result"])
MAX_ENTRIES = 5000


class ResponseCache:
    def __init__(self) -> None:
        self._d: dict[tuple, tuple[float, Any]] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}
        self._gen: dict[str, int] = {}

    def invalidate_tenant(self, tenant_id: str) -> None:
        tid = str(tenant_id)
        self._gen[tid] = self._gen.get(tid, 0) + 1
        for k in [k for k in self._d if k[1] == tid]:
            self._d.pop(k, None)

    def clear(self) -> None:
        self._d.clear()
        self._gen.clear()

    async def get(self, key: tuple, compute: Callable[[], Any], ttl: float, name: str) -> Any:
        tid = key[1]
        hit = self._d.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            CACHE_REQUESTS.labels(name, "hit").inc()
            return hit[1]
        fut = self._inflight.get(key)
        if fut is not None:
            CACHE_REQUESTS.labels(name, "shared").inc()
            return await asyncio.shield(fut)
        CACHE_REQUESTS.labels(name, "miss").inc()
        gen = self._gen.get(tid, 0)
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await compute()
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()
            raise
        finally:
            self._inflight.pop(key, None)
        fut.set_result(value)
        if self._gen.get(tid, 0) == gen:              # no write of the tenant committed meanwhile
            if len(self._d) >= MAX_ENTRIES:
                self._d.clear()
            self._d[key] = (time.monotonic() + ttl, value)
        return value


response_cache = ResponseCache()


def cached_endpoint(name: str) -> Callable:
    """Decorator for an async route whose parameters include `user` (the authenticated User) and `db`."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            ttl = float(settings.api_cache_ttl_s)
            user = kwargs.get("user")
            if ttl <= 0 or user is None:
                return await fn(*args, **kwargs)
            params = tuple(sorted((k, repr(v)) for k, v in kwargs.items() if k not in ("user", "db")))
            role = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
            key = (name, str(user.tenant_id), str(user.id), str(role), params)
            return await response_cache.get(key, lambda: fn(*args, **kwargs), ttl, name)
        # FastAPI reads the parameters from the signature; the route modules use string annotations
        # (from __future__ import annotations), resolved against the ROUTE's globals, not this module's
        wrapper.__signature__ = inspect.signature(fn, eval_str=True)
        return wrapper
    return deco


# ── invalidation: a commit that wrote rows of a tenant drops the tenant's entries ───────────────

@event.listens_for(Session, "after_flush")
def _collect(session, _ctx) -> None:
    tids = session.info.setdefault("cache_tenants", set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        tid = getattr(obj, "tenant_id", None)
        if tid:
            tids.add(str(tid))


@event.listens_for(Session, "do_orm_execute")
def _collect_bulk(state) -> None:
    if state.is_update or state.is_delete or state.is_insert:
        tid = (state.parameters or {}).get("tenant_id") if isinstance(state.parameters, dict) else None
        state.session.info.setdefault("cache_tenants", set()).add(str(tid) if tid else "*")


@event.listens_for(Session, "after_commit")
def _invalidate(session) -> None:
    tids = session.info.pop("cache_tenants", None)
    if not tids:
        return
    if "*" in tids:
        response_cache.clear()
        return
    for tid in tids:
        response_cache.invalidate_tenant(tid)


@event.listens_for(Session, "after_rollback")
def _forget(session) -> None:
    session.info.pop("cache_tenants", None)
