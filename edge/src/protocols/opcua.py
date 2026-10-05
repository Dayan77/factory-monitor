"""
OPC UA driver — subscribes to nodes on an OPC UA server.

Tag address format: "ns=2;i=1001"   (numeric node ID)
                    "ns=2;s=Temp1"  (string node ID)

With `source_timestamps=True` the callback receives a third argument: the DataValue SourceTimestamp
(UTC, set by the PLC/server), falling back to ServerTimestamp, or None. `connected` is True while
the session and subscription are up (the gateway only refreshes heartbeats of connected sources).
"""

import asyncio
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
                 source_timestamps: bool = False) -> None:
        # Keyed by NodeId object: str(NodeId) is a repr ("NodeId(Identifier=...)"),
        # not "ns=2;i=1001", so string keys never matched the notifications.
        self._tag_map = tag_map
        self._on_reading = on_reading
        self._source_ts = source_timestamps
        self._loop = asyncio.get_running_loop()

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
            coro = self._on_reading(cfg.tag_id, value, ts)
        else:
            coro = self._on_reading(cfg.tag_id, value)
        asyncio.run_coroutine_threadsafe(coro, self._loop)


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
    ) -> None:
        self._endpoint = endpoint
        self._tags = tags or []
        self._on_reading = on_reading
        self._publishing_interval = publishing_interval_ms
        self._reconnect_delay = reconnect_delay_s
        self._source_ts = source_timestamps
        self._running = False
        self.connected = False

    async def run(self) -> None:
        self._running = True
        log.info("opcua.starting", endpoint=self._endpoint, tags=len(self._tags))

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

                    handler = _SubscriptionHandler(tag_map, self._on_reading, self._source_ts)
                    sub = await client.create_subscription(self._publishing_interval, handler)
                    await sub.subscribe_data_change(nodes)
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
