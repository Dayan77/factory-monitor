"""
Edge Gateway — entry point.

Responsibilities:
- Poll PLCs/sensors via Modbus, OPC UA or MQTT
- Filter noise before forwarding
- Buffer data locally (SQLite) when broker is unreachable
- Publish encrypted payloads to MQTT broker → server
"""

import asyncio
import signal
import structlog

from .protocols.modbus import ModbusDriver
from .protocols.opcua import OpcUaDriver
from .protocols.mqtt_publisher import MqttPublisher
from .buffer.local_buffer import LocalBuffer
from .filters.deadband import DeadbandFilter
from .config import settings

log = structlog.get_logger()


class EdgeGateway:
    def __init__(self) -> None:
        self.buffer = LocalBuffer(settings.buffer_db_path)
        self.publisher = MqttPublisher(
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
        )
        self.filter = DeadbandFilter(deadband_pct=settings.deadband_pct)
        self.drivers: list = []
        self._running = False

    async def start(self) -> None:
        log.info("edge_gateway.starting")
        await self.buffer.initialize()
        await self.publisher.connect()

        # TODO: load driver configs dynamically from server API
        if settings.modbus_host:
            self.drivers.append(
                ModbusDriver(
                    host=settings.modbus_host,
                    port=settings.modbus_port,
                    tags=settings.modbus_tags,
                    on_reading=self._handle_reading,
                )
            )

        self._running = True
        await asyncio.gather(
            *[d.run() for d in self.drivers],
            self._flush_buffer_loop(),
        )

    async def _handle_reading(self, tag_id: str, value: float) -> None:
        if not self.filter.should_publish(tag_id, value):
            return

        topic = self.publisher.build_topic(settings.tenant_id, tag_id)
        payload = self.publisher.build_payload(value)
        published = await self.publisher.publish(settings.tenant_id, tag_id, value)
        if not published:
            await self.buffer.store(topic, payload)

    async def _flush_buffer_loop(self) -> None:
        """Retry buffered readings when connection is restored."""
        while self._running:
            await asyncio.sleep(30)
            if self.publisher.is_connected:
                pending = await self.buffer.flush()
                for topic, payload in pending:
                    await self._client_publish_raw(topic, payload)

    async def _client_publish_raw(self, topic: str, payload: dict) -> None:
        """Republish a buffered (topic, payload) pair directly."""
        import json
        if self.publisher.is_connected and self.publisher._client:
            try:
                await self.publisher._client.publish(topic, json.dumps(payload).encode())
            except Exception:
                await self.buffer.store(topic, payload)

    async def stop(self) -> None:
        self._running = False
        await self.publisher.disconnect()
        log.info("edge_gateway.stopped")


async def main() -> None:
    gateway = EdgeGateway()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(gateway.stop()))

    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
