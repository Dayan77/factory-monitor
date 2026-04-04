"""
OPC UA driver — subscribes to nodes on an OPC UA server.

Tag address format: "ns=2;i=1001"   (numeric node ID)
                    "ns=2;s=Temp1"  (string node ID)
"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

import structlog
from asyncua import Client, Node
from asyncua.common.subscription import SubHandler

log = structlog.get_logger()

OnReading = Callable[[str, float], Awaitable[None]]


@dataclass
class OpcTagConfig:
    tag_id: str
    node_id: str       # e.g. "ns=2;i=1001"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""


class _SubscriptionHandler(SubHandler):
    def __init__(self, tag_map: dict[str, OpcTagConfig], on_reading: OnReading) -> None:
        self._tag_map = tag_map    # node_id str → config
        self._on_reading = on_reading
        self._loop = asyncio.get_event_loop()

    def datachange_notification(self, node: Node, val, data) -> None:
        node_id_str = str(node.nodeid)
        cfg = self._tag_map.get(node_id_str)
        if cfg is None:
            return
        try:
            value = float(val) * cfg.scale + cfg.offset
            asyncio.run_coroutine_threadsafe(
                self._on_reading(cfg.tag_id, value), self._loop
            )
        except (TypeError, ValueError):
            pass


class OpcUaDriver:
    def __init__(
        self,
        endpoint: str,
        tags: list[OpcTagConfig] = None,
        on_reading: Optional[OnReading] = None,
    ) -> None:
        self._endpoint = endpoint
        self._tags = tags or []
        self._on_reading = on_reading
        self._running = False

    async def run(self) -> None:
        self._running = True
        log.info("opcua.starting", endpoint=self._endpoint, tags=len(self._tags))

        while self._running:
            try:
                async with Client(url=self._endpoint) as client:
                    log.info("opcua.connected", endpoint=self._endpoint)

                    tag_map: dict[str, OpcTagConfig] = {}
                    nodes: list[Node] = []
                    for cfg in self._tags:
                        node = client.get_node(cfg.node_id)
                        tag_map[cfg.node_id] = cfg
                        nodes.append(node)

                    handler = _SubscriptionHandler(tag_map, self._on_reading)
                    sub = await client.create_subscription(500, handler)
                    await sub.subscribe_data_change(nodes)

                    while self._running:
                        await asyncio.sleep(1)

            except Exception as e:
                log.warning("opcua.error", error=str(e))
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        log.info("opcua.stopped")
