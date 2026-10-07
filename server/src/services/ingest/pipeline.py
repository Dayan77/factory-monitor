"""Bounded, ordered ingestion pipeline (Sprint 5b).

    broker ──► receive loop ──► per-tag FIFO queues ──► fixed worker pool ──► process(reading)
                   │  (blocks when `capacity` readings are in flight)          │ returns the Influx write
                   │                                                          ▼
                   └──── acknowledge in receive order PER TAG ◄──── reading finished (stored / dead-lettered)

* **Order per tag**: one queue per key (tenant, tag); a key is served by at most one worker at a time, so the
  readings of a tag are processed one after the other, in arrival order. Different tags run in parallel.
* **Fixed concurrency**: `workers` tasks, created once (no task per message).
* **Bounded memory / backpressure**: at most `capacity` readings are inside the pipeline (queued, processing or
  waiting for their Influx write). When full, `put()` waits — the receive loop stops reading. QoS 1 deliveries are
  acknowledged (PUBACK) only when the reading is *finished*, so the broker never has more than its
  `max_inflight_messages` unacknowledged deliveries to this client and keeps the backlog in its own queue
  (persistent session: also across a server restart).
* **Acknowledgement order = per key (tenant, tag)**, not global: a reading is acknowledged as soon as it and every
  earlier reading OF THE SAME TAG are finished. A global receive-order gate let one stuck reading (a tenant's usage
  transaction waiting on a recompute lock, a retry of up to MQTT_TRANSIENT_RETRY_S) hold back the PUBACK of every
  later reading of every tenant until the broker's in-flight window filled and ingestion stopped for all tenants
  (Sprint 5b review). Mosquitto and paho match a PUBACK by packet id, so out-of-order acknowledgements across tags are
  accepted (MQTT 3.1.1 §4.6 asks for receive order; we keep it within each tag, which is what redelivery after a
  crash needs: a tag's unacknowledged readings are always a suffix of its stream, redelivered in order).
* **At-least-once**: a reading acknowledged is stored (or in the dead-letter table). After a crash the broker
  redelivers the unacknowledged ones; processing is idempotent (same Influx point; usage ignores duplicates).
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Hashable, Optional

import structlog

from ..metrics import INGEST_ACTIVE_TAGS, INGEST_DROPPED, INGEST_LATENCY, INGEST_QUEUE_CAPACITY, \
    INGEST_QUEUE_DEPTH, INGEST_UNACKED

log = structlog.get_logger()


@dataclass(eq=False)
class Item:
    key: Hashable
    topic: str
    payload: bytes
    received_at: Any                       # datetime (wall clock, for the processing)
    mid: int = 0
    qos: int = 0
    gen: int = 0                           # broker connection generation the delivery belongs to
    t0: float = 0.0                        # loop time at receive (latency metric)
    done: bool = False
    meta: dict = field(default_factory=dict)


# process(item) does the ordered part of the work and returns an awaitable that completes when the reading is
# durable (its Influx write), or None when there is nothing left to wait for. It must not raise for a reading
# problem (it dead-letters it itself); an exception escaping is logged and counted as dropped.
Processor = Callable[[Item], Awaitable[Optional[Awaitable]]]
Acker = Callable[[Item], None]


class OrderedPipeline:
    def __init__(self, process: Processor, *, capacity: int, workers: int, acker: Optional[Acker] = None,
                 batch_per_turn: int = 64, process_batch=None) -> None:
        self._process = process
        # optional: process_batch(items of one key, in order) -> [awaitable | None per item] (one turn at once)
        self._process_batch = process_batch
        self._acker = acker
        self.capacity = max(1, int(capacity))
        self.n_workers = max(1, int(workers))
        self._batch = max(1, batch_per_turn)
        self._slots = asyncio.Semaphore(self.capacity)
        self._queues: dict[Hashable, deque[Item]] = {}
        self._ready: asyncio.Queue = asyncio.Queue()
        self._scheduled: set[Hashable] = set()          # keys in _ready or being served
        # receive order per key, for in-order acknowledgement within a tag (independent across tags/tenants)
        self._order: dict[Hashable, deque[Item]] = {}
        self.unacked = 0
        self._finishing: set[asyncio.Task] = set()
        self._workers: list[asyncio.Task] = []
        self.inflight = 0
        self.processed = 0
        self.gen = 0
        INGEST_QUEUE_CAPACITY.set(self.capacity)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker(k), name=f"ingest_worker_{k}")
                             for k in range(self.n_workers)]

    def tasks(self) -> list[asyncio.Task]:
        return [t for t in [*self._workers, *self._finishing] if not t.done()]

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        for t in list(self._finishing):
            t.cancel()
        await asyncio.gather(*self._workers, *self._finishing, return_exceptions=True)
        self._workers = []

    def new_connection(self) -> int:
        """A new broker connection: deliveries of the old one cannot be acknowledged on it (their packet ids
        belong to the old session state; the broker redelivers what was not acknowledged)."""
        self.gen += 1
        return self.gen

    # ── input ────────────────────────────────────────────────────────────────
    async def put(self, item: Item) -> None:
        """Accepts one reading; waits while the pipeline is full (backpressure)."""
        await self._slots.acquire()
        self.inflight += 1
        INGEST_QUEUE_DEPTH.set(self.inflight)
        item.t0 = item.t0 or asyncio.get_running_loop().time()
        order = self._order.get(item.key)
        if order is None:
            order = self._order[item.key] = deque()
        order.append(item)
        self.unacked += 1
        INGEST_UNACKED.set(self.unacked)
        q = self._queues.get(item.key)
        if q is None:
            q = self._queues[item.key] = deque()
            INGEST_ACTIVE_TAGS.set(len(self._queues))
        q.append(item)
        if item.key not in self._scheduled:
            self._scheduled.add(item.key)
            self._ready.put_nowait(item.key)

    async def drain(self, timeout: Optional[float] = None) -> bool:
        """Waits until every accepted reading is finished. True if it did within `timeout`."""
        loop = asyncio.get_running_loop()
        end = None if timeout is None else loop.time() + timeout
        while self.inflight:
            if end is not None and loop.time() > end:
                return False
            await asyncio.sleep(0.01)
        return True

    # ── workers ──────────────────────────────────────────────────────────────
    async def _worker(self, k: int) -> None:
        while True:
            key = await self._ready.get()
            q = self._queues.get(key)
            if self._process_batch is not None and q:
                batch = [q.popleft() for _ in range(min(len(q), self._batch))]
                await self._run_batch(batch)
            served = 0
            while self._process_batch is None and q and served < self._batch:
                item = q.popleft()
                served += 1
                await self._run(item)
            if q:
                self._ready.put_nowait(key)            # more readings of this tag: back of the line (fairness)
            else:
                self._queues.pop(key, None)
                self._scheduled.discard(key)
                INGEST_ACTIVE_TAGS.set(len(self._queues))

    async def _run_batch(self, items: list[Item]) -> None:
        try:
            fins = await self._process_batch(items)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a processor bug must not kill the worker
            from .transient import cancelling
            if cancelling():
                raise asyncio.CancelledError() from e
            INGEST_DROPPED.inc(len(items))
            log.error("ingest.reading_dropped", topic=items[0].topic[:200], count=len(items), error=str(e)[:300],
                      error_type=type(e).__name__, reading_lost=True)
            fins = [None] * len(items)
        for item, fin in zip(items, fins):
            self._attach(item, fin)

    async def _run(self, item: Item) -> None:
        try:
            fin = await self._process(item)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a processor bug must not kill the worker
            from .transient import cancelling
            if cancelling():
                raise asyncio.CancelledError() from e
            INGEST_DROPPED.inc()
            log.error("ingest.reading_dropped", topic=item.topic[:200], error=str(e)[:300],
                      error_type=type(e).__name__, reading_lost=True)
            fin = None
        self._attach(item, fin)

    def _attach(self, item: Item, fin) -> None:
        if fin is None:
            self._finished(item)
            return
        task = asyncio.ensure_future(fin)
        self._finishing.add(task)
        task.add_done_callback(lambda t, it=item: self._write_done(t, it))

    def _write_done(self, task: asyncio.Task, item: Item) -> None:
        self._finishing.discard(task)
        if not task.cancelled() and task.exception() is not None:
            e = task.exception()
            INGEST_DROPPED.inc()
            log.error("ingest.reading_dropped", topic=item.topic[:200], error=str(e)[:300],
                      error_type=type(e).__name__, reading_lost=True)
        if task.cancelled():
            return                                     # shutdown: not acknowledged → redelivered by the broker
        self._finished(item)

    def _finished(self, item: Item) -> None:
        item.done = True
        self.processed += 1
        self.inflight -= 1
        INGEST_QUEUE_DEPTH.set(self.inflight)
        INGEST_LATENCY.observe(max(0.0, asyncio.get_running_loop().time() - item.t0))
        self._slots.release()
        order = self._order.get(item.key)
        while order and order[0].done:
            it = order.popleft()
            self.unacked -= 1
            if self._acker is not None and it.qos and it.gen == self.gen:
                try:
                    self._acker(it)
                except Exception as e:  # noqa: BLE001 — connection gone: the broker redelivers it
                    log.warning("ingest.ack_failed", error=str(e)[:200])
        if order is not None and not order:
            self._order.pop(item.key, None)
        INGEST_UNACKED.set(self.unacked)
