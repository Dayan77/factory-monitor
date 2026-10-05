"""
Edge Gateway — entry point.

Responsibilities:
- Poll PLCs/sensors via Modbus, OPC UA or MQTT
- Filter noise before forwarding (deadband only for continuous values)
- Count fast cycle pulses locally (edge counting, persisted accumulated counter)
- Buffer data locally (SQLite) when broker is unreachable
- Publish payloads to MQTT broker → server (AES-256-GCM envelope when ENCRYPTION_KEY is set)
- Publish a heartbeat per gateway

Configuration:
- PLANT_MAP_PATH=edge/config/plant.local.json → device/tag map (edge/src/plant_map.py), the full path
  `virtual plant + provision + gateway`;
- GATEWAY_CONFIG_URL (+ GATEWAY_TOKEN, GATEWAY_ID) → the map is fetched from the server
  (GET /api/gateways/{gateway_id}/config, Authorization: Gateway <token>) with a local cached copy for offline
  start and the PLANT_MAP_PATH file as last resort (edge/src/remote_config.py);
- otherwise the legacy MODBUS_HOST + MODBUS_TAGS settings.

Heartbeat (not buffered — a stale heartbeat is meaningless):
  topic   factory/{tenant_id}/gateways/{gateway_id}/heartbeat
  payload {"v": 1, "gateway_id": "...", "ts": "...Z", "uptime_s": 12.3, "tags": 46,
           "buffered": 0, "sources": {"opcua-plc": true, "modbus-plc": true},
           "clocks": {"opcua-plc": {"policy": "source", "configured": "source", "max_skew_s": 2.0,
                                    "skew_s": -0.12}}}

Timestamps: one clock per device (plant map `sources[].timestamps`, default `gateway`); see
edge/src/clock.py for the policy table, skew detection (> max_clock_skew_s, default 2 s) and demotion.
"""

import asyncio
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from .buffer.local_buffer import LocalBuffer
from .clock import DeviceClock
from .config import settings
from .counting import EdgeCounterStore, PulsePoller
from .filters.deadband import DeadbandFilter
from .plant_map import PlantMap, load_plant_map
from .protocols.modbus import ModbusDriver, ModbusTagConfig
from .protocols.mqtt_publisher import MqttPublisher, format_ts
from .protocols.opcua import OpcTagConfig, OpcUaDriver
from .remote_config import load_gateway_config, local_gateway_id

log = structlog.get_logger()

HEARTBEAT_VERSION = 1
# Smallest step the payload timestamp can express (format_ts has millisecond resolution).
TS_STEP = timedelta(milliseconds=1)


class EdgeGateway:
    def __init__(self, flush_interval_s: float = 30.0) -> None:
        self.buffer = LocalBuffer(settings.buffer_db_path)
        self.publisher = MqttPublisher(
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            encryption_key=settings.encryption_key,
            tls=settings.mqtt_tls,
            ca_cert=settings.mqtt_ca_cert,
        )
        self.filter = DeadbandFilter(
            deadband_pct=settings.deadband_pct,
            max_silence_s=settings.deadband_max_silence_s,
        )
        self.drivers: list = []
        self.tenant_id: str = settings.tenant_id
        self.gateway_id: str = settings.gateway_id
        self.flush_interval_s = flush_interval_s
        self.heartbeat_s: float = settings.heartbeat_interval_s or 10.0
        self.plant_map: Optional[PlantMap] = None
        self.counter_store: Optional[EdgeCounterStore] = None
        self._units: dict[str, str] = {}
        self._no_deadband: set[str] = set()
        self._last_values: dict[str, float] = {}
        self._last_ts: dict[str, datetime] = {}            # last stamped ts per tag (monotonic)
        # One clock per device (edge/src/clock.py); tags outside the plant map use the default one.
        self._default_clock = DeviceClock("default", settings.timestamp_policy, settings.max_clock_skew_s, log=log)
        self._clocks: dict[str, DeviceClock] = {}          # source id → clock
        self._tag_clock: dict[str, DeviceClock] = {}       # tag id → clock of its device
        self._refresh_tags: dict[str, OpcUaDriver] = {}   # OPC UA tags (report-by-exception) → driver
        self._sources: dict[str, object] = {}              # source id → driver (heartbeat status)
        self._started_at = time.monotonic()
        self._running = False

    # ── configuration ───────────────────────────────────────────────────
    def register_modbus_tags(self, tags: list[ModbusTagConfig]) -> None:
        for t in tags:
            self._units[t.tag_id] = t.unit
            if t.bypass_deadband:
                self._no_deadband.add(t.tag_id)

    def load_plant_map(self, pm: PlantMap) -> None:
        """Builds one driver per source (+ a fast pulse poller per Modbus source with cycle_pulse tags)."""
        self.plant_map = pm
        if not self.tenant_id and pm.tenant_id:
            self.tenant_id = pm.tenant_id
        if not self.gateway_id:
            self.gateway_id = pm.gateway_id
        if not settings.heartbeat_interval_s:
            self.heartbeat_s = pm.heartbeat_s
        if pm.skipped:
            log.warning("edge_gateway.tags_without_tag_id", count=len(pm.skipped),
                        hint="rode python -m edge.src.provision para preencher os tag_id")

        for t in pm.tags:
            self._units[t.tag_id] = t.unit
            if t.bypass_deadband:
                self._no_deadband.add(t.tag_id)

        pulse_ms = settings.pulse_poll_ms or pm.pulse_poll_ms
        for src in pm.sources.values():
            clock = DeviceClock(src.id, src.timestamps, src.max_clock_skew_s, log=log)
            self._clocks[src.id] = clock
            for t in pm.tags_for(src.id):
                self._tag_clock[t.tag_id] = clock
            log.info("edge_gateway.clock_policy", source=src.id, policy=src.timestamps,
                     max_skew_s=src.max_clock_skew_s)
            if src.protocol == "opcua":
                tags = [OpcTagConfig(t.tag_id, str(t.address), t.scale, t.offset, t.unit) for t in pm.tags_for(src.id)]
                if not tags:
                    continue
                drv = OpcUaDriver(src.endpoint, tags, on_reading=self._handle_reading,
                                  publishing_interval_ms=src.publishing_interval_ms, source_timestamps=True,
                                  notify_on_timestamp=src.device_heartbeat)
                for t in tags:
                    self._refresh_tags[t.tag_id] = drv
            else:
                regular = [
                    ModbusTagConfig(
                        tag_id=t.tag_id, address=int(t.address), register_type=t.register_type,
                        scale=t.scale, offset=t.offset, unit=t.unit, poll_interval_ms=t.poll_interval_ms,
                        data_type=t.data_type_plc, word_order=t.word_order, device_id=t.unit_id,
                        no_deadband=t.bypass_deadband,
                    )
                    for t in pm.tags_for(src.id, pulse=False)
                ]
                drv = ModbusDriver(src.host, src.port, tags=regular, on_reading=self._handle_reading) if regular else None
                pulses = pm.tags_for(src.id, pulse=True)
                if pulses:
                    if self.counter_store is None:
                        self.counter_store = EdgeCounterStore(settings.counter_db_path)
                    poller = PulsePoller(src.host, src.port, pulses, self.counter_store,
                                         on_count=self._handle_reading, interval_ms=pulse_ms)
                    self.drivers.append(poller)
                    self._sources[f"{src.id}:pulses"] = poller
                if drv is None:
                    continue
            self.drivers.append(drv)
            self._sources[src.id] = drv
        log.info("edge_gateway.plant_map_loaded", gateway_id=self.gateway_id, tags=len(pm.tags),
                 drivers=len(self.drivers), pulse_poll_ms=pulse_ms)

    # ── lifecycle ───────────────────────────────────────────────────────
    async def start(self) -> None:
        log.info("edge_gateway.starting")
        await self.buffer.initialize()
        await self.publisher.connect()

        if settings.gateway_config_url:
            loaded = await load_gateway_config(
                settings.gateway_config_url,
                settings.gateway_id or local_gateway_id(settings.plant_map_path),
                settings.gateway_token, settings.gateway_config_cache,
                fallback_path=settings.plant_map_path, timeout_s=settings.gateway_config_timeout_s,
            )
            log.info("edge_gateway.config", origin=loaded.origin, revision=loaded.revision, detail=loaded.detail)
            self.load_plant_map(loaded.plant_map)
        elif settings.plant_map_path:
            self.load_plant_map(load_plant_map(settings.plant_map_path))
        elif settings.modbus_host:
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
        if not self.tenant_id:
            log.warning("edge_gateway.no_tenant_id", hint="defina TENANT_ID ou rode o provisionamento")

        self._running = True
        self._started_at = time.monotonic()
        await asyncio.gather(
            *[d.run() for d in self.drivers],
            self._flush_buffer_loop(),
            self._heartbeat_loop(),
            self._refresh_loop(),
        )

    def _stamp(self, tag_id: str, ts: Optional[datetime], initial: bool = False) -> datetime:
        """Timestamp for a reading: the device's clock policy (edge/src/clock.py), then strictly
        increasing per tag at the published (millisecond) resolution — the server drops a reading
        older than the tag's last accepted ts, so a change racing a refresh is published right
        after it (+1 ms) instead of being dropped."""
        clock = self._tag_clock.get(tag_id, self._default_clock)
        ts = clock.stamp(ts, tag_id=tag_id, initial=initial)
        ts = ts.replace(microsecond=ts.microsecond - ts.microsecond % 1000)
        last = self._last_ts.get(tag_id)
        if last is not None and ts <= last:
            ts = last + TS_STEP
        self._last_ts[tag_id] = ts
        return ts

    async def _handle_reading(self, tag_id: str, value: float, ts: Optional[datetime] = None,
                              initial: bool = False) -> None:
        # Timestamp at the source (UTC) when trustworthy, else the gateway read time; monotonic per tag.
        # `initial`: first OPC UA notification of the item (value's last change, not a skew sample).
        ts = self._stamp(tag_id, ts, initial)
        self._last_values[tag_id] = value
        # Silence (heartbeat) measured on the reading's own clock: same clock as the published ts.
        if not self.filter.should_publish(tag_id, value, bypass=tag_id in self._no_deadband, now=ts.timestamp()):
            return

        topic = self.publisher.build_topic(self.tenant_id, tag_id)
        payload = self.publisher.build_payload(value, self._units.get(tag_id), ts)
        # Keep FIFO order: while there is a backlog, new readings go to the buffer too.
        if await self.buffer.count() == 0 and await self.publisher.publish_payload(topic, payload):
            return
        await self.buffer.store(topic, payload)

    async def refresh_once(self) -> int:
        """Re-offers the last value of OPC UA tags (subscriptions only notify changes) so the
        deadband heartbeat can re-publish them. Only for connected sources: never fake fresh data
        from a source that is down. Returns how many values were re-offered."""
        n = 0
        for tag_id, drv in self._refresh_tags.items():
            clock = self._tag_clock.get(tag_id, self._default_clock)
            # A device clock the gateway cannot map (source policy, skew check off) gets no synthesized ts.
            if drv.connected and tag_id in self._last_values and clock.can_synthesize:
                # Stamped on the device's clock (see _stamp / clock.py), never behind the tag's last ts.
                await self._handle_reading(tag_id, self._last_values[tag_id])
                n += 1
        return n

    async def _refresh_loop(self) -> None:
        if not self._refresh_tags or self.filter._max_silence is None:
            return
        period = max(1.0, self.filter._max_silence / 3)
        while self._running:
            await asyncio.sleep(period)
            try:
                await self.refresh_once()
            except Exception as e:  # noqa: BLE001
                log.warning("edge_gateway.refresh_failed", error=str(e))

    def heartbeat_payload(self) -> dict:
        sources = {}
        for sid, drv in self._sources.items():
            if isinstance(drv, ModbusDriver):
                sources[sid] = bool(drv._client.connected)
            else:
                sources[sid] = bool(getattr(drv, "connected", False))
        return {
            "v": HEARTBEAT_VERSION,
            "gateway_id": self.gateway_id,
            "ts": format_ts(datetime.now(timezone.utc)),
            "uptime_s": round(time.monotonic() - self._started_at, 1),
            "tags": len(self._units),
            "sources": sources,
            "clocks": {sid: c.status() for sid, c in self._clocks.items()},
        }

    def heartbeat_topic(self) -> str:
        return f"factory/{self.tenant_id}/gateways/{self.gateway_id or 'gateway'}/heartbeat"

    async def publish_heartbeat(self) -> bool:
        payload = self.heartbeat_payload()
        payload["buffered"] = await self.buffer.count()
        return await self.publisher.publish_payload(self.heartbeat_topic(), payload)

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                if self.publisher.is_connected:
                    await self.publish_heartbeat()
            except Exception as e:  # noqa: BLE001
                log.warning("edge_gateway.heartbeat_failed", error=str(e))
            await asyncio.sleep(self.heartbeat_s)

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
        if self.counter_store is not None:
            self.counter_store.close()
            self.counter_store = None
        log.info("edge_gateway.stopped")


async def main() -> None:
    gateway = EdgeGateway()

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    async def _shutdown() -> None:
        await gateway.stop()
        if main_task is not None:
            main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    try:
        await gateway.start()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
