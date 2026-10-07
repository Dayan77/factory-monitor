"""
Local SQLite buffer — stores sensor payloads when MQTT broker is unreachable.
Flushes automatically when connection is restored.

Throughput (Sprint 5b QA: a gateway at ~230 readings/s never recovered after an uplink outage, because every
reading was one INSERT + COMMIT with fsync and one COUNT(*) on the whole table, all on the single aiosqlite queue the
flush also needs): writes are batched (`store_many`, one transaction per batch), the database runs in WAL mode with
synchronous=NORMAL (a power cut may lose the last committed batches, never corrupts the file) and the gateway keeps
the row count in memory (`initialize()` / `rows`) instead of counting the table per reading.
"""

import asyncio
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
        self.rows = 0                      # rows in the table (kept in memory; set by initialize)

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                topic     TEXT NOT NULL,
                payload   TEXT NOT NULL,
                stored_at TEXT NOT NULL
            )
        """)
        await self._db.commit()
        self.rows = await self.count()
        log.info("buffer.initialized", path=self._path, rows=self.rows)

    async def store(self, topic: str, payload: dict) -> None:
        await self.store_many([(topic, payload)])

    async def store_many(self, items: list[tuple[str, dict]]) -> int:
        """Appends (topic, payload) rows in order, in ONE transaction. Returns how many were stored."""
        if self._db is None or not items:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        await self._db.executemany(
            "INSERT INTO buffer (topic, payload, stored_at) VALUES (?, ?, ?)",
            [(t, json.dumps(p), now) for t, p in items],
        )
        await self._db.commit()
        self.rows += len(items)
        log.debug("buffer.stored", count=len(items))
        return len(items)

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
        cur = await self._db.execute(f"DELETE FROM buffer WHERE id IN ({placeholders})", ids)
        await self._db.commit()
        self.rows = max(0, self.rows - (cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(ids)))

    async def flush_to(
        self,
        publish: Callable[[str, dict], Awaitable[bool]],
        batch_size: int = 500,
        window: int = 1,
        max_batches: int | None = None,
    ) -> int:
        """Re-sends buffered rows in original (FIFO) order through `publish`.

        `window` > 1 starts that many publishes at once (pipelined: they reach the wire in order, their
        acknowledgements are awaited together — one round trip per window instead of per reading). A row is
        deleted only after `publish` returned True, and only the leading run of successes of a window: on the
        first failure the flush stops, so nothing is lost and order is preserved for the next attempt (a later
        row of the same window that did go through is sent again — the server's processing is idempotent).
        `max_batches` limits the work of one call (the gateway interleaves the flush with new readings).
        Returns how many rows were sent.
        """
        sent = 0
        batches = 0
        window = max(1, int(window))

        async def _one(topic: str, payload: dict) -> bool:
            try:
                return bool(await publish(topic, payload))
            except Exception as e:  # noqa: BLE001
                log.warning("buffer.flush_error", error=str(e))
                return False

        while max_batches is None or batches < max_batches:
            rows = await self.peek(batch_size)
            if not rows:
                break
            batches += 1
            done: list[int] = []
            failed = False
            for i in range(0, len(rows), window):
                chunk = rows[i:i + window]
                if len(chunk) == 1:
                    results = [await _one(chunk[0][1], chunk[0][2])]
                else:
                    results = await asyncio.gather(*(_one(t, p) for _, t, p in chunk))
                for (row_id, _, _), ok in zip(chunk, results):
                    if not ok:
                        failed = True
                        break
                    done.append(row_id)
                if failed:
                    break
            await self.delete(done)
            sent += len(done)
            if failed:
                break
        if sent:
            log.info("buffer.flushed", count=sent, remaining=self.rows)
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
        self.rows = 0

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
