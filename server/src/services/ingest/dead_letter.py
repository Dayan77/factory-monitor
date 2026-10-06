"""Dead letters: valid readings that could not be processed after the retries (no silent drops).

`record()` stores the raw delivery (topic + payload exactly as received) in `ingest_dead_letters`, counts it in
`fm_ingest_dead_letters{stage}` and logs `ingest.dead_letter`. If even the database is unavailable for the whole
retry budget, the delivery is appended to a JSON-lines file (settings.ingest_dead_letter_file); only if that also
fails is the reading counted in `fm_ingest_dropped` (must stay 0).

`replay()` feeds stored dead letters back through the normal reading processor (idempotent) — for the operator
after the cause is fixed (`python -m src.services.ingest.dead_letter --replay`).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import structlog
from sqlalchemy import select

from ...config import settings
from ...db import AsyncSessionLocal
from ..metrics import INGEST_DEAD_LETTERS, INGEST_DROPPED
from ..validation import InvalidIdentifier, ensure_uuid
from .transient import retry_transient

log = structlog.get_logger()


def _ids(topic: str) -> tuple[Optional[str], Optional[str]]:
    parts = topic.split("/")
    out = []
    for k in (1, 3):
        try:
            out.append(ensure_uuid(parts[k], "id") if len(parts) > k else None)
        except InvalidIdentifier:
            out.append(None)
    return out[0], out[1]


async def _insert(topic: str, payload: bytes, received_at: datetime, stage: str, error: str, attempts: int) -> None:
    from ...models import IngestDeadLetter
    tenant_id, tag_id = _ids(topic)
    async with AsyncSessionLocal() as db:
        db.add(IngestDeadLetter(tenant_id=tenant_id, tag_id=tag_id, topic=topic[:512], payload=bytes(payload),
                                received_at=received_at, stage=stage, error=error[:4000], attempts=attempts))
        await asyncio.wait_for(db.commit(), timeout=10.0)


async def record(topic: str, payload: bytes, received_at: datetime, stage: str, error: BaseException | str,
                 attempts: int = 1) -> bool:
    """Stores the reading as a dead letter. True if it was stored (database or fallback file)."""
    err = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    INGEST_DEAD_LETTERS.labels(stage).inc()
    log.error("ingest.dead_letter", topic=topic[:200], stage=stage, error=err[:300], attempts=attempts)
    try:
        await retry_transient("ingest.dead_letter", lambda: _insert(topic, payload, received_at, stage, err, attempts),
                              settings.mqtt_transient_retry_s)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("ingest.dead_letter.db_failed", error=str(e)[:200], error_type=type(e).__name__)
    try:
        line = json.dumps({"topic": topic, "payload_b64": base64.b64encode(bytes(payload)).decode(),
                           "received_at": received_at.isoformat(), "stage": stage, "error": err[:4000],
                           "attempts": attempts, "at": datetime.now(timezone.utc).isoformat()})
        path = settings.ingest_dead_letter_file
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        INGEST_DROPPED.inc()
        log.critical("ingest.reading_dropped", topic=topic[:200], stage=stage, error=str(e)[:200], reading_lost=True)
        return False


async def replay(handle: Callable[[str, bytes, datetime], Awaitable[None]], tenant_id: Optional[str] = None,
                 limit: int = 10_000) -> int:
    """Re-processes stored dead letters (oldest first, optionally one tenant) with `handle(topic, payload,
    received_at)`; marks each `replayed_at`. A reading that fails again is dead-lettered again by the processor."""
    from ...models import IngestDeadLetter
    async with AsyncSessionLocal() as db:
        q = select(IngestDeadLetter).where(IngestDeadLetter.replayed_at.is_(None))
        if tenant_id:
            q = q.where(IngestDeadLetter.tenant_id == ensure_uuid(tenant_id, "tenant_id"))
        rows = (await db.execute(q.order_by(IngestDeadLetter.created_at).limit(limit))).scalars().all()
        n = 0
        for r in rows:
            # received now: a reading older than the replay threshold is history (no live alert/WebSocket)
            now = datetime.now(timezone.utc)
            await handle(r.topic, bytes(r.payload), now)
            r.replayed_at = now
            n += 1
        await db.commit()
    return n


if __name__ == "__main__":  # pragma: no cover — operator tool
    import argparse

    ap = argparse.ArgumentParser(description="Reprocessa leituras em ingest_dead_letters")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--tenant")
    a = ap.parse_args()
    if a.replay:
        from ..mqtt_subscriber import _handle_message
        print(asyncio.run(replay(lambda t, p, r: _handle_message(t, p, received_at=r), a.tenant)))
