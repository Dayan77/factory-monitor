"""WebSocket relay between processes (Sprint 5b): the predictive engine runs in its own process (src/worker.py)
and has no WebSocket clients; what it would send (`predictive_alert`, `notification`) goes through Postgres
`NOTIFY fm_ws` and the app process — which holds the sockets — delivers it (`run_listener`). Tenant isolation is
kept: the message carries its tenant (and user) and is delivered with the same ConnectionManager calls.

NOTIFY payloads are limited to 8000 bytes: a bigger message is reduced to its type/event/ids with
`"truncated": true` (the web app refetches on any predictive_alert / notification message).
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import structlog
from sqlalchemy import text

log = structlog.get_logger()

CHANNEL = "fm_ws"
MAX_PAYLOAD = 7900


def _shrink(message: dict) -> dict:
    out = {"type": message.get("type"), "truncated": True}
    for k in ("event",):
        if k in message:
            out[k] = message[k]
    for k in ("alert", "notification"):
        v = message.get(k)
        if isinstance(v, dict):
            out[k] = {kk: v.get(kk) for kk in ("id", "state", "severity", "position_id", "machine_id", "feature",
                                               "kind", "title", "link") if kk in v}
    return out


def encode(kind: str, tenant_id: str, user_id: Optional[str], message: dict) -> str:
    body = {"k": kind, "t": tenant_id, "u": user_id, "m": message}
    raw = json.dumps(body, default=str, separators=(",", ":"))
    if len(raw.encode()) > MAX_PAYLOAD:
        body["m"] = _shrink(message)
        raw = json.dumps(body, default=str, separators=(",", ":"))
    return raw


class PgNotifyPublisher:
    """Used in the worker process: manager.relay = PgNotifyPublisher()."""

    async def publish(self, kind: str, tenant_id: str, user_id: Optional[str], message: dict) -> None:
        from ..db import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(text("SELECT pg_notify(:ch, :p)"), {"ch": CHANNEL,
                                                                     "p": encode(kind, tenant_id, user_id, message)})
                await db.commit()
        except Exception as e:  # noqa: BLE001 — a live update lost is not data lost (the UI refetches)
            log.warning("ws.relay.publish_failed", error=str(e)[:200])


def enable_publisher() -> None:
    from .manager import manager
    manager.relay = PgNotifyPublisher()


async def deliver(raw: str) -> None:
    from .manager import manager
    try:
        body = json.loads(raw)
        if body.get("k") == "user":
            await manager.send_to_user(str(body["t"]), str(body["u"]), body["m"])
        elif body.get("k") == "tenant":
            await manager.broadcast(str(body["t"]), body["m"])
    except Exception as e:  # noqa: BLE001
        log.warning("ws.relay.deliver_failed", error=str(e)[:200])


async def run_listener() -> None:
    """App process: LISTEN fm_ws and deliver to the local sockets. Reconnects forever."""
    import asyncpg
    from ..config import settings
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            loop = asyncio.get_running_loop()

            def on_notify(_c, _pid, _ch, payload: str) -> None:
                loop.create_task(deliver(payload))

            await conn.add_listener(CHANNEL, on_notify)
            log.info("ws.relay.listening", channel=CHANNEL)
            while not conn.is_closed():
                await asyncio.sleep(5)
                await conn.execute("SELECT 1")          # keepalive / detect a dead connection
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("ws.relay.listener_error", error=str(e)[:200])
        finally:
            if conn is not None and not conn.is_closed():
                try:
                    await asyncio.wait_for(conn.close(), timeout=2)
                except BaseException:  # noqa: BLE001
                    pass
        await asyncio.sleep(2)
