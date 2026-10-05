"""
Local SQLite buffer — stores sensor payloads when MQTT broker is unreachable.
Flushes automatically when connection is restored.
"""

import json
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiosqlite
import structlog

log = structlog.get_logger()


class LocalBuffer:
    def __init__(self, db_path: str = "/tmp/edge_buffer.db") -> None:
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                topic     TEXT NOT NULL,
                payload   TEXT NOT NULL,
                stored_at TEXT NOT NULL
            )
        """)
        await self._db.commit()
        log.info("buffer.initialized", path=self._path)

    async def store(self, topic: str, payload: dict) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "INSERT INTO buffer (topic, payload, stored_at) VALUES (?, ?, ?)",
            (topic, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()
        log.debug("buffer.stored", topic=topic)

    async def peek(self, limit: int = 500) -> list[tuple[int, str, dict]]:
        """Oldest-first (id, topic, payload) rows, without removing them."""
        if self._db is None:
            return []
        async with self._db.execute(
            "SELECT id, topic, payload FROM buffer ORDER BY id LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    async def delete(self, ids: list[int]) -> None:
        if self._db is None or not ids:
            return
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(f"DELETE FROM buffer WHERE id IN ({placeholders})", ids)
        await self._db.commit()

    async def flush_to(
        self,
        publish: Callable[[str, dict], Awaitable[bool]],
        batch_size: int = 500,
    ) -> int:
        """Re-sends buffered rows in original (FIFO) order through `publish`.

        A row is deleted only after `publish` returned True. On the first failure the
        flush stops, so nothing is lost and order is preserved for the next attempt.
        Returns how many rows were sent.
        """
        sent = 0
        while True:
            rows = await self.peek(batch_size)
            if not rows:
                break
            done: list[int] = []
            failed = False
            for row_id, topic, payload in rows:
                try:
                    ok = await publish(topic, payload)
                except Exception as e:  # noqa: BLE001
                    log.warning("buffer.flush_error", error=str(e))
                    ok = False
                if not ok:
                    failed = True
                    break
                done.append(row_id)
            await self.delete(done)
            sent += len(done)
            if failed:
                break
        if sent:
            log.info("buffer.flushed", count=sent)
        return sent

    async def flush(self) -> list[tuple[str, dict]]:
        """Returns all buffered (topic, payload) pairs and clears them.

        Prefer `flush_to`, which only deletes rows that were actually re-sent.
        """
        if self._db is None:
            return []
        async with self._db.execute(
            "SELECT id, topic, payload FROM buffer ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return []

        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(f"DELETE FROM buffer WHERE id IN ({placeholders})", ids)
        await self._db.commit()

        log.info("buffer.flushed", count=len(rows))
        return [(r[1], json.loads(r[2])) for r in rows]

    async def count(self) -> int:
        if self._db is None:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM buffer") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
