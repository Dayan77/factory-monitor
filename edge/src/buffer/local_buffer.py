"""
Local SQLite buffer — stores sensor payloads when MQTT broker is unreachable.
Flushes automatically when connection is restored.
"""

import json
from datetime import datetime, timezone

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

    async def flush(self) -> list[tuple[str, dict]]:
        """Returns all buffered (topic, payload) pairs and clears them."""
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
