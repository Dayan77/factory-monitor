"""
Edge Gateway — entry point.

Responsibilities:
- Poll PLCs/sensors via Modbus, OPC UA or MQTT
- Filter noise before forwarding
- Buffer data locally (SQLite) when broker is unreachable
- Publish payloads to MQTT broker → server (AES-256-GCM envelope when ENCRYPTION_KEY is set)
"""

import asyncio
import signal
from datetime import datetime, timezone
from typing import Optional

import structlog

from .protocols.modbus import ModbusDriver, ModbusTagConfig
from .protocols.opcua import OpcUaDriver
from .protocols.mqtt_publisher import MqttPublisher
from .buffer.local_buffer import LocalBuffer
from .filters.deadband import DeadbandFilter
from .config import settings

log = structlog.get_logger()


class EdgeGateway:
    def __init__(self, flush_interval_s: float = 30.0) -> None:
        self.buffer = LocalBuffer(settings.buffer_db_path)
        self.publisher = MqttPublisher(
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            encryption_key=settings.encryption_key,
        )
        self.filter = DeadbandFilter(
            deadband_pct=settings.deadband_pct,
            max_silence_s=settings.deadband_max_silence_s,
        )
        self.drivers: list = []
        self.tenant_id: str = settings.tenant_id
        self.flush_interval_s = flush_interval_s
        self._units: dict[str, str] = {}
        self._no_deadband: set[str] = set()
        self._running = False

    def register_modbus_tags(self, tags: list[ModbusTagConfig]) -> None:
        for t in tags:
            self._units[t.tag_id] = t.unit
            if t.bypass_deadband:
                self._no_deadband.add(t.tag_id)

    async def start(self) -> None:
        log.info("edge_gateway.starting")
        await self.buffer.initialize()
        await self.publisher.connect()

        # TODO: load driver configs dynamically from server API
        if settings.modbus_host:
            tags = [ModbusTagConfig.from_dict(t) for t in settings.modbus_tags]
            self.register_modbus_tags(tags)
            self.drivers.append(
                ModbusDriver(
                    host=settings.modbus_host,
                    port=settings.modbus_port,
                    tags=tags,
                    on_reading=self._handle_reading,
                )
            )

        self._running = True
        await asyncio.gather(
            *[d.run() for d in self.drivers],
            self._flush_buffer_loop(),
        )

    async def _handle_reading(self, tag_id: str, value: float, ts: Optional[datetime] = None) -> None:
        # Timestamp at the source (UTC), before any network I/O.
        ts = ts or datetime.now(timezone.utc)
        if not self.filter.should_publish(tag_id, value, bypass=tag_id in self._no_deadband):
            return

        topic = self.publisher.build_topic(self.tenant_id, tag_id)
        payload = self.publisher.build_payload(value, self._units.get(tag_id), ts)
        # Keep FIFO order: while there is a backlog, new readings go to the buffer too.
        if await self.buffer.count() == 0 and await self.publisher.publish_payload(topic, payload):
            return
        await self.buffer.store(topic, payload)

    async def flush_once(self) -> int:
        """Reconnects if needed and re-sends the buffer in order. Returns rows sent."""
        if not self.publisher.is_connected:
            await self.publisher.connect()
        if not self.publisher.is_connected:
            return 0
        return await self.buffer.flush_to(self.publisher.publish_payload)

    async def _flush_buffer_loop(self) -> None:
        """Retry buffered readings when connection is restored."""
        while self._running:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self.flush_once()
            except Exception as e:  # noqa: BLE001 — never kill the loop
                log.warning("edge_gateway.flush_failed", error=str(e))

    async def stop(self) -> None:
        self._running = False
        for d in self.drivers:
            await d.stop()
        await self.publisher.disconnect()
        await self.buffer.close()
        log.info("edge_gateway.stopped")


async def main() -> None:
    gateway = EdgeGateway()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(gateway.stop()))

    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
