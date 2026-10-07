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


class BatchedPgNotifyPublisher:
    """Sprint 7 — for the ingestion worker (src/ingest_worker.py): one live message per reading (2 400/s at scale)
    cannot be one NOTIFY transaction each. Messages are buffered and sent every FLUSH_S as JSON batches
    {"b": [body, …]} (each NOTIFY < MAX_PAYLOAD) on one dedicated connection; the listener unpacks them. A batch
    that cannot be sent is dropped (live updates only — the data itself is stored by the pipeline)."""

    FLUSH_S = 0.05
    MAX_BUFFER = 20000

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._task: Optional[asyncio.Task] = None
        self._conn = None
        self.sent_batches = 0
        self.dropped = 0

    async def publish(self, kind: str, tenant_id: str, user_id: Optional[str], message: dict) -> None:
        if len(self._buf) >= self.MAX_BUFFER:
            self.dropped += 1
            return
        self._buf.append(encode(kind, tenant_id, user_id, message))
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._flush_loop(), name="ws_relay_flush")

    def _chunks(self, items: list[str]) -> list[str]:
        out, cur, size = [], [], 8
        for raw in items:
            n = len(raw.encode()) + 1
            if cur and size + n > MAX_PAYLOAD:
                out.append('{"b":[' + ",".join(cur) + "]}")
                cur, size = [], 8
            cur.append(raw)
            size += n
        if cur:
            out.append('{"b":[' + ",".join(cur) + "]}")
        return out

    async def _flush_loop(self) -> None:
        import asyncpg
        from ..config import settings
        while self._buf:
            await asyncio.sleep(self.FLUSH_S)
            items, self._buf = self._buf, []
            try:
                if self._conn is None or self._conn.is_closed():
                    self._conn = await asyncpg.connect(settings.database_url.replace("postgresql+asyncpg://",
                                                                                     "postgresql://"))
                for payload in self._chunks(items):
                    await self._conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
                    self.sent_batches += 1
            except Exception as e:  # noqa: BLE001 — a live update lost is not data lost (the UI refetches)
                self.dropped += len(items)
                log.warning("ws.relay.publish_failed", error=str(e)[:200], messages=len(items))
                try:
                    if self._conn is not None:
                        await self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None


def enable_publisher(batched: bool = False) -> None:
    from .manager import manager
    manager.relay = BatchedPgNotifyPublisher() if batched else PgNotifyPublisher()


async def deliver(raw: str) -> None:
    from .manager import manager
    try:
        body = json.loads(raw)
        if "b" in body:                               # a batch (BatchedPgNotifyPublisher)
            for item in body["b"]:
                await _deliver_one(item)
            return
        await _deliver_one(body)
    except Exception as e:  # noqa: BLE001
        log.warning("ws.relay.deliver_failed", error=str(e)[:200])


async def _deliver_one(body: dict) -> None:
    from .manager import manager
    try:
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
