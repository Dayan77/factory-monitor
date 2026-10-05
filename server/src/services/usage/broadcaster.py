"""
Throttled WebSocket broadcast of usage deltas.

    {"type": "usage", "position_id": "<uuid>|null", "machine_id": "<uuid>|null",
     "measure": "cycles|hours|starts|good|scrap|total", "delta": <float>, "total": <float>,
     "timestamp": "<ISO-8601 UTC>"}

Deltas are coalesced per (position or machine, measure) and flushed every
settings.usage_ws_interval_s (1 s): at most one message per position and measure per second,
whatever the reading rate. `delta` is the sum since the previous message, `total` the latest
accumulator value (usage_total of the position / machine total; hours in hours).
Machine-level production (good/scrap/total) and machine-level hours have position_id = null.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from ...config import settings
from ...websocket.manager import manager
from .processor import UsageUpdate

log = structlog.get_logger()


class UsageBroadcaster:
    def __init__(self) -> None:
        self._pending: dict[tuple, dict] = {}

    def add(self, u: UsageUpdate) -> None:
        key = (u.tenant_id, u.position_id, u.machine_id if u.position_id is None else None, u.measure)
        cur = self._pending.get(key)
        if cur is None:
            self._pending[key] = {"tenant_id": u.tenant_id, "type": "usage", "position_id": u.position_id,
                                  "machine_id": u.machine_id, "measure": u.measure,
                                  "delta": u.delta, "total": u.total}
        else:
            cur["delta"] += u.delta
            cur["total"] = u.total

    def pending(self) -> int:
        return len(self._pending)

    async def flush(self) -> int:
        items, self._pending = self._pending, {}
        now = datetime.now(timezone.utc).isoformat()
        for msg in items.values():
            tenant_id = msg.pop("tenant_id")
            msg["timestamp"] = now
            await manager.broadcast(tenant_id, msg)
        return len(items)

    async def run(self) -> None:
        while True:
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("usage.ws_flush.error", error=str(e))
            await asyncio.sleep(settings.usage_ws_interval_s)


usage_broadcaster = UsageBroadcaster()
