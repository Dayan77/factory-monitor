"""
OPC UA driver — subscribes to nodes on an OPC UA server.

Tag address format: "ns=2;i=1001"   (numeric node ID)
                    "ns=2;s=Temp1"  (string node ID)

With `source_timestamps=True` the callback receives a third argument: the DataValue SourceTimestamp
(UTC, set by the PLC/server), falling back to ServerTimestamp, or None — and `initial=True` for the first
notification of each monitored item after (re)subscribing, whose SourceTimestamp is the value's last
change (possibly hours old), not a delivery-time sample (edge/src/clock.py). `connected` is True while
the session and subscription are up (the gateway only refreshes heartbeats of connected sources).
Monitored items are created with a server-side queue (`queue_size`, default 4096): intermediate changes are kept,
also through a pause of the gateway (at 116 strokes/s per tag, 64 overflowed after ~0.55 s — Sprint 5b QA).

Notifications are delivered in order by ONE consumer task per driver from a bounded local queue (`max_pending`):
asyncua calls the handler in a task per notification, and the handler used to start one more coroutine per reading
(unbounded: 4 GB of pending readings when the uplink was slow). When the local queue is full the reading is dropped
and counted (`dropped`, warning `opcua.readings_dropped` with the total) — never an unbounded heap.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

import structlog
from asyncua import Client, Node, ua

log = structlog.get_logger()

OnReading = Callable[[str, float], Awaitable[None]]


@dataclass
class OpcTagConfig:
    tag_id: str
    node_id: str       # e.g. "ns=2;i=1001"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""


class _SubscriptionHandler:
    """Implements asyncua's DataChangeNotificationHandler protocol (asyncua>=2 has no SubHandler base)."""

    def __init__(self, tag_map: dict[ua.NodeId, OpcTagConfig], on_reading: Optional[OnReading],
                 source_timestamps: bool = False, sink: Optional[Callable[[tuple], None]] = None) -> None:
        # Keyed by NodeId object: str(NodeId) is a repr ("NodeId(Identifier=...)"),
        # not "ns=2;i=1001", so string keys never matched the notifications.
        self._tag_map = tag_map
        self._on_reading = on_reading
        self._source_ts = source_timestamps
        self._loop = asyncio.get_running_loop()
        self._seen: set = set()            # monitored items already notified since (re)subscribing
        # sink(item): the driver's bounded, ordered queue; item = (tag_id, value, ts, initial) or (tag_id, value)
        self._sink = sink

    def status_change_notification(self, status) -> None:
        # subscription re-created / transferred by asyncua: the next values are initial again
        self._seen.clear()

    def datachange_notification(self, node: Node, val, data) -> None:
        cfg = self._tag_map.get(node.nodeid)
        if cfg is None or self._on_reading is None:
            return
        try:
            value = float(val) * cfg.scale + cfg.offset
        except (TypeError, ValueError):
            log.warning("opcua.non_numeric_value", tag_id=cfg.tag_id, value=repr(val))
            return
        if self._source_ts:
            ts = _source_timestamp(data)
            initial = node.nodeid not in self._seen
            self._seen.add(node.nodeid)
            item = (cfg.tag_id, value, ts, initial)
        else:
            item = (cfg.tag_id, value)
        if self._sink is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._sink(item)
        else:                                   # called from another thread: hand over to the loop, in order
            self._loop.call_soon_threadsafe(self._sink, item)


def _source_timestamp(data) -> Optional[datetime]:
    try:
        dv = data.monitored_item.Value
    except AttributeError:
        return None
    ts = dv.SourceTimestamp or dv.ServerTimestamp
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class OpcUaDriver:
    def __init__(
        self,
        endpoint: str,
        tags: Optional[list[OpcTagConfig]] = None,
        on_reading: Optional[OnReading] = None,
        publishing_interval_ms: int = 500,
        reconnect_delay_s: float = 5.0,
        source_timestamps: bool = False,
        queue_size: int = 4096,
        notify_on_timestamp: bool = False,
        max_pending: int = 50000,
    ) -> None:
        self._endpoint = endpoint
        # Server-side queue per monitored item: every change between two publishes is delivered
        # (with its own SourceTimestamp) instead of only the last one — a short stop that starts
        # and ends within one publishing interval is not lost.
        self._queue_size = queue_size
        # DataChangeTrigger.StatusValueTimestamp: a re-write of the same value with a newer SourceTimestamp
        # (device heartbeat) is notified too. Only for devices that re-stamp on purpose (plant map
        # `device_heartbeat`), never for servers that re-stamp every scan.
        self._notify_on_timestamp = notify_on_timestamp
        self._tags = tags or []
        self._on_reading = on_reading
        self._publishing_interval = publishing_interval_ms
        self._reconnect_delay = reconnect_delay_s
        self._source_ts = source_timestamps
        self._running = False
        self.connected = False
        # local queue: notifications waiting for on_reading (one consumer, in order, bounded)
        self._max_pending = max(1, int(max_pending))
        self._pending: deque = deque()
        self._pending_evt: Optional[asyncio.Event] = None
        self.dropped = 0
        self._dropped_logged = 0
        self._drop_log_at = 0.0

    @property
    def pending(self) -> int:
        return len(self._pending)

    def _push(self, item: tuple) -> None:
        if len(self._pending) >= self._max_pending:
            self.dropped += 1
            now = time.monotonic()
            if now - self._drop_log_at >= 10.0:
                log.warning("opcua.readings_dropped", endpoint=self._endpoint, total=self.dropped,
                            since_last=self.dropped - self._dropped_logged, max_pending=self._max_pending,
                            hint="o gateway não acompanha a taxa de leituras (CPU/uplink)")
                self._drop_log_at, self._dropped_logged = now, self.dropped
            return
        self._pending.append(item)
        if self._pending_evt is not None:
            self._pending_evt.set()

    async def _consume(self) -> None:
        """Delivers queued notifications to on_reading one at a time, in arrival order. on_reading may wait
        (gateway outbox full): the local queue absorbs it up to `max_pending`."""
        assert self._pending_evt is not None
        while True:
            await self._pending_evt.wait()
            self._pending_evt.clear()
            while self._pending:
                item = self._pending.popleft()
                if self._on_reading is None:
                    continue
                try:
                    if len(item) == 4:
                        await self._on_reading(item[0], item[1], item[2], initial=item[3])
                    else:
                        await self._on_reading(item[0], item[1])
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — one bad reading never stops the consumer
                    log.warning("opcua.reading_failed", tag_id=item[0], error=str(e)[:200])

    async def run(self) -> None:
        self._running = True
        log.info("opcua.starting", endpoint=self._endpoint, tags=len(self._tags), queue_size=self._queue_size)
        self._pending_evt = asyncio.Event()
        if self._pending:
            self._pending_evt.set()
        consumer = asyncio.create_task(self._consume(), name="opcua_consumer")
        try:
            await self._run_sessions()
        finally:
            # deliver what was already received (a stop/reload must not lose queued strokes), then stop
            deadline = time.monotonic() + 5.0
            while self._pending and time.monotonic() < deadline and not consumer.done():
                await asyncio.sleep(0.01)
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    async def _run_sessions(self) -> None:
        while self._running:
            try:
                async with Client(url=self._endpoint) as client:
                    log.info("opcua.connected", endpoint=self._endpoint)

                    tag_map: dict[ua.NodeId, OpcTagConfig] = {}
                    nodes: list[Node] = []
                    for cfg in self._tags:
                        node = client.get_node(cfg.node_id)
                        tag_map[node.nodeid] = cfg
                        nodes.append(node)

                    handler = _SubscriptionHandler(tag_map, self._on_reading, self._source_ts, sink=self._push)
                    sub = await client.create_subscription(self._publishing_interval, handler)
                    if self._notify_on_timestamp:
                        mfilter = ua.DataChangeFilter(Trigger=ua.DataChangeTrigger.StatusValueTimestamp)
                        await sub._subscribe(nodes, mfilter=mfilter, queuesize=self._queue_size)
                    else:
                        await sub.subscribe_data_change(nodes, queuesize=self._queue_size)
                    self.connected = True

                    while self._running:
                        await asyncio.sleep(1)
                        # asyncua 2 reconnects by itself: report the live session state.
                        state = getattr(getattr(client, "uaclient", None), "state", None)
                        self.connected = state is None or getattr(state, "name", "") == "CONNECTED"
                        await client.check_connection()   # raises when the session is gone for good

            except Exception as e:
                log.warning("opcua.error", error=str(e))
            finally:
                self.connected = False
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)

    async def stop(self) -> None:
        self._running = False
        log.info("opcua.stopped")
