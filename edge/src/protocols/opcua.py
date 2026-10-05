"""
OPC UA driver — subscribes to nodes on an OPC UA server.

Tag address format: "ns=2;i=1001"   (numeric node ID)
                    "ns=2;s=Temp1"  (string node ID)
"""

import asyncio
from dataclasses import dataclass
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

    def __init__(self, tag_map: dict[ua.NodeId, OpcTagConfig], on_reading: Optional[OnReading]) -> None:
        # Keyed by NodeId object: str(NodeId) is a repr ("NodeId(Identifier=...)"),
        # not "ns=2;i=1001", so string keys never matched the notifications.
        self._tag_map = tag_map
        self._on_reading = on_reading
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
        asyncio.run_coroutine_threadsafe(self._on_reading(cfg.tag_id, value), self._loop)


class OpcUaDriver:
    def __init__(
        self,
        endpoint: str,
        tags: Optional[list[OpcTagConfig]] = None,
        on_reading: Optional[OnReading] = None,
        publishing_interval_ms: int = 500,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        self._endpoint = endpoint
        self._tags = tags or []
        self._on_reading = on_reading
        self._publishing_interval = publishing_interval_ms
        self._reconnect_delay = reconnect_delay_s
        self._running = False

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

                    handler = _SubscriptionHandler(tag_map, self._on_reading)
                    sub = await client.create_subscription(self._publishing_interval, handler)
                    await sub.subscribe_data_change(nodes)

                    while self._running:
                        await asyncio.sleep(1)

            except Exception as e:
                log.warning("opcua.error", error=str(e))
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)

    async def stop(self) -> None:
        self._running = False
        log.info("opcua.stopped")
