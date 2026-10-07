"""
Edge Gateway — entry point.

Responsibilities:
- Poll PLCs/sensors via Modbus, OPC UA or MQTT
- Filter noise before forwarding (deadband only for continuous values)
- Count fast cycle pulses locally (edge counting, persisted accumulated counter)
- Buffer data locally (SQLite) when broker is unreachable
- Publish payloads to MQTT broker → server (AES-256-GCM envelope when ENCRYPTION_KEY is set)
- Publish a heartbeat per gateway (MQTT) and, when enrolled in the fleet, an HTTP heartbeat to the server
- Hot-reload the device/tag map when the server's config_version changes (no restart, counters kept)

Configuration (first match wins):
- fleet (Sprint 5, docs/gateways.md): ENROLL_CODE + SERVER_URL on the first start → POST /api/gateways/enroll,
  credentials (gateway_id, token, MQTT user/password) saved with 0600 at GATEWAY_CREDENTIALS_PATH
  (edge/src/enrollment.py); from then on the map comes from GET /api/gateways/{id}/config (ETag, local cached copy
  for offline start — edge/src/remote_config.py), the HTTP heartbeat POST /api/gateways/{id}/heartbeat runs every
  GATEWAY_HEARTBEAT_S (edge/src/fleet.py) and `config_changed` → reload; `commands` in the heartbeat answer
  (Sprint 6: `opcua_browse` of a configured source) run in the background — edge/src/commands.py;
- GATEWAY_CONFIG_URL (+ GATEWAY_TOKEN, GATEWAY_ID) → same remote config without enrollment (Sprint 4 setup);
- PLANT_MAP_PATH=edge/config/plant.local.json → device/tag map file (edge/src/plant_map.py), the full path
  `virtual plant + provision + gateway` (also the last-resort fallback of the remote config);
- otherwise the legacy MODBUS_HOST + MODBUS_TAGS settings.

Heartbeat MQTT (not buffered — a stale heartbeat is meaningless):
  topic   factory/{tenant_id}/gateways/{gateway_id}/heartbeat
  payload {"v": 1, "gateway_id": "...", "ts": "...Z", "uptime_s": 12.3, "tags": 46, "version": "0.6.0",
           "config_version": 7, "buffered": 0, "sources": {"opcua-plc": true, "modbus-plc": true},
           "clocks": {"opcua-plc": {"policy": "source", "configured": "source", "max_skew_s": 2.0,
                                    "skew_s": -0.12}}}

Timestamps: one clock per device (plant map `sources[].timestamps`, default `gateway`); see
edge/src/clock.py for the policy table, skew detection (> max_clock_skew_s, default 2 s) and demotion.

Sending (store-and-forward, Sprint 5b QA (d)): a reading goes to a bounded in-memory outbox (`outbox_max`; full =
the driver waits — backpressure, bounded memory) drained by ONE sender task (`_sender_loop`):
- broker up and SQLite buffer empty → published directly, `publish_window` QoS 1 publishes in flight at once
  (in order on the wire; a failure moves the failed readings and everything after them to the buffer, in order);
- broker down → the outbox is spilled to SQLite in batches (`spill_batch` rows per transaction, at least every
  `spill_max_age_s`);
- broker back with a backlog → the BUFFER is flushed first (oldest first, windowed); new readings wait in the outbox
  and are spilled behind the backlog, so FIFO order holds and the flush always progresses faster than new data
  arrives (the old path did COUNT(*) + INSERT + COMMIT per reading on the same SQLite queue the flush needed, and
  never drained).
The row count of the buffer is kept in memory (LocalBuffer.rows). On stop the outbox is written to SQLite.

Predictive inputs (docs/predictive.md P-1 — window features are computed by the SERVER): `stroke_time` is one
reading per cycle (OPC UA: every notification; Modbus: when the position's counter advances), never deduplicated;
`aggregate` tags publish one RMS/peak/mean value per window from the raw samples (edge/src/plant_map.py).
"""

import asyncio
import math
import signal
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from .buffer.local_buffer import LocalBuffer
from .clock import DeviceClock
from .config import settings
from .counting import EdgeCounterStore, PulsePoller
from .enrollment import Credentials, EnrollmentError, base_url, ensure_enrolled
from .filters.deadband import DeadbandFilter
from .commands import CommandRunner
from .fleet import FleetClient, HeartbeatError, heartbeat_body
from .plant_map import COUNTER_ROLES, MapTag, PlantMap, load_plant_map
from .protocols.modbus import ModbusDriver, ModbusTagConfig
from .protocols.mqtt_publisher import MqttPublisher, effective_port, format_ts, parse_tls_settings
from .protocols.opcua import OpcTagConfig, OpcUaDriver
from .remote_config import LoadedConfig, load_gateway_config, local_gateway_id
from .version import __version__

log = structlog.get_logger()

HEARTBEAT_VERSION = 1
DEFAULT_CREDENTIALS_PATH = "/tmp/edge_gateway_credentials.json"
# Smallest step the payload timestamp can express (format_ts has millisecond resolution).
TS_STEP = timedelta(milliseconds=1)


class WindowAggregator:
    """One value per window of `window_s` (aligned to the epoch) from raw samples: rms, peak (max |x|), mean,
    max, min. A window is emitted when the first sample of a later window arrives (timestamp = window end)."""

    def __init__(self, window_s: float, stat: str) -> None:
        self.window_s = float(window_s)
        self.stat = stat
        self._bucket: Optional[int] = None
        self._n = 0
        self._sum = self._sumsq = 0.0
        self._max = -math.inf
        self._min = math.inf
        self._peak = 0.0

    def _value(self) -> float:
        if self.stat == "rms":
            return math.sqrt(self._sumsq / self._n)
        if self.stat == "peak":
            return self._peak
        if self.stat == "mean":
            return self._sum / self._n
        return self._max if self.stat == "max" else self._min

    def add(self, ts: datetime, value: float) -> list[tuple[datetime, float]]:
        out = []
        b = math.floor(ts.timestamp() / self.window_s)
        if self._bucket is not None and b != self._bucket and self._n:
            out.append(self._close())
        if self._bucket is None or b != self._bucket:
            self._bucket = b
        v = float(value)
        self._n += 1
        self._sum += v
        self._sumsq += v * v
        self._max, self._min = max(self._max, v), min(self._min, v)
        self._peak = max(self._peak, abs(v))
        return out

    def _close(self) -> tuple[datetime, float]:
        end = datetime.fromtimestamp((self._bucket + 1) * self.window_s, tz=timezone.utc)
        val = round(self._value(), 6)
        self._n, self._sum, self._sumsq, self._max, self._min, self._peak = 0, 0.0, 0.0, -math.inf, math.inf, 0.0
        return end, val


class EdgeGateway:
    def __init__(self, flush_interval_s: float = 30.0) -> None:
        self.buffer = LocalBuffer(settings.buffer_db_path)
        # TLS validated at startup: a missing CA/cert file fails here (never silently plaintext)
        self.tls = parse_tls_settings(settings.mqtt_tls, settings.mqtt_ca_cert, settings.mqtt_client_cert,
                                      settings.mqtt_client_key)
        for w in self.tls.warnings:
            log.warning("edge_gateway.mqtt_tls", warning=w)
        self.publisher = self._make_publisher(settings.mqtt_username, settings.mqtt_password)
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
        self._deadband_pct: dict[str, float] = {}          # per-tag deadband (plant map `deadband_pct`)
        self._last_values: dict[str, float] = {}
        self._last_ts: dict[str, datetime] = {}            # last stamped ts per tag (monotonic)
        # One clock per device (edge/src/clock.py); tags outside the plant map use the default one.
        self._default_clock = DeviceClock("default", settings.timestamp_policy, settings.max_clock_skew_s, log=log)
        self._clocks: dict[str, DeviceClock] = {}          # source id → clock
        self._tag_clock: dict[str, DeviceClock] = {}       # tag id → clock of its device
        self._refresh_tags: dict[str, OpcUaDriver] = {}   # OPC UA tags (report-by-exception) → driver
        self._sources: dict[str, object] = {}              # source id → driver (heartbeat status)
        # stroke_time = one reading per cycle: tag id → paired counter tag id (Modbus) or None (OPC UA notification)
        self._stroke: dict[str, Optional[str]] = {}
        self._stroke_seen: dict[str, float] = {}           # counter value at the last published stroke
        self._aggregates: dict[str, WindowAggregator] = {}
        self._driver_tasks: list[asyncio.Task] = []
        # fleet (enrollment / remote config / HTTP heartbeat)
        self.credentials: Optional[Credentials] = None
        self.fleet: Optional[FleetClient] = None
        self.commands: Optional[CommandRunner] = None
        self.fleet_status: Optional[str] = None
        self.config_version: Optional[int] = None
        self.config_revision: Optional[str] = None
        self.config_origin: Optional[str] = None
        self.reloads = 0
        self._config_url = ""
        self._token = ""
        self._errors: deque[str] = deque(maxlen=20)
        self._reload_lock: Optional[asyncio.Lock] = None
        self._started_at = time.monotonic()
        self._running = False
        # sender (see module docstring): outbox of (topic, payload, enqueued_at monotonic)
        self._outbox: deque[tuple[str, dict, float]] = deque()
        self._outbox_max = max(1, int(settings.outbox_max))
        self._window = max(1, int(settings.publish_window))
        self._spill_batch = max(1, int(settings.spill_batch))
        self._spill_age_s = max(0.0, float(settings.spill_max_age_s))
        self._sender_task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._space: Optional[asyncio.Event] = None
        self._pump_lock: Optional[asyncio.Lock] = None
        self.sent_direct = 0
        self._inflight_n = 0                               # readings of the window being published

    def _make_publisher(self, username: str, password: str) -> MqttPublisher:
        port = effective_port(settings.mqtt_broker_port, self.tls.enabled)
        if self.tls.enabled and port == 1883:
            log.warning("edge_gateway.mqtt_tls_port", port=port, hint="o broker com TLS costuma usar 8883")
        return MqttPublisher(
            host=settings.mqtt_broker_host,
            port=port,
            username=username,
            password=password,
            encryption_key=settings.encryption_key,
            tls_config=self.tls,
            max_inflight=min(100, settings.publish_window),
            pending_warn=settings.publish_window + 100,
        )

    # ── configuration ───────────────────────────────────────────────────
    def register_modbus_tags(self, tags: list[ModbusTagConfig]) -> None:
        for t in tags:
            self._units[t.tag_id] = t.unit
            if t.bypass_deadband:
                self._no_deadband.add(t.tag_id)

    def _clock_for(self, src) -> DeviceClock:
        """Keeps a device clock across reloads when its configuration did not change (skew history kept)."""
        old = self._clocks.get(src.id)
        if old is not None and old.policy == src.timestamps and old.max_skew_s == src.max_clock_skew_s:
            return old
        return DeviceClock(src.id, src.timestamps, src.max_clock_skew_s, log=log)

    def _reset_map_state(self) -> None:
        self.drivers = []
        self._units.clear()
        self._no_deadband.clear()
        self._deadband_pct.clear()
        self._tag_clock.clear()
        self._refresh_tags.clear()
        self._sources.clear()
        self._stroke.clear()

    @staticmethod
    def _stroke_pairs(pm: PlantMap) -> dict[str, Optional[str]]:
        """stroke_time tag → counter tag of the same position (Modbus); None for OPC UA (one notification per
        cycle). Modbus stroke without a counter: not in the dict (published only when the value changes)."""
        counters: dict[str, MapTag] = {}
        for t in pm.tags:
            if t.semantic_role in COUNTER_ROLES and t.position_group:
                counters.setdefault(t.position_group, t)
        out: dict[str, Optional[str]] = {}
        for t in pm.tags:
            if t.semantic_role != "stroke_time":
                continue
            src = pm.sources[t.source]
            if src.protocol == "opcua":
                out[t.tag_id] = None
            elif t.position_group in counters:
                out[t.tag_id] = counters[t.position_group].tag_id
        return out

    def load_plant_map(self, pm: PlantMap) -> None:
        """Builds one driver per source (+ a fast pulse poller per Modbus source with cycle_pulse tags).
        Replaces the previous map's drivers (call `apply_plant_map` on a running gateway: it stops them first)."""
        self._reset_map_state()
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

        aggregates: dict[str, WindowAggregator] = {}
        for t in pm.tags:
            self._units[t.tag_id] = t.unit
            if t.bypass_deadband:
                self._no_deadband.add(t.tag_id)
            if t.deadband_pct is not None:
                self._deadband_pct[t.tag_id] = t.deadband_pct
            if t.aggregate:
                old = self._aggregates.get(t.tag_id)
                same = old is not None and (old.window_s, old.stat) == (t.aggregate["window_s"], t.aggregate["stat"])
                aggregates[t.tag_id] = old if same else WindowAggregator(t.aggregate["window_s"], t.aggregate["stat"])
        self._aggregates = aggregates
        self._stroke = self._stroke_pairs(pm)

        pulse_ms = settings.pulse_poll_ms or pm.pulse_poll_ms
        clocks: dict[str, DeviceClock] = {}
        for src in pm.sources.values():
            clock = self._clock_for(src)
            clocks[src.id] = clock
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
                                  notify_on_timestamp=src.device_heartbeat, queue_size=settings.opcua_queue_size,
                                  max_pending=settings.opcua_max_pending)
                for t in tags:
                    if t.tag_id not in self._stroke and t.tag_id not in self._aggregates:
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
        self._clocks = clocks
        log.info("edge_gateway.plant_map_loaded", gateway_id=self.gateway_id, tags=len(pm.tags),
                 drivers=len(self.drivers), pulse_poll_ms=pulse_ms, stroke_per_cycle=len(self._stroke),
                 aggregates=len(self._aggregates))

    async def _stop_drivers(self) -> None:
        for d in self.drivers:
            try:
                await d.stop()
            except Exception as e:  # noqa: BLE001
                log.warning("edge_gateway.driver_stop_failed", error=str(e))
        for t in self._driver_tasks:
            t.cancel()
        if self._driver_tasks:
            await asyncio.gather(*self._driver_tasks, return_exceptions=True)
        self._driver_tasks = []

    def _start_drivers(self) -> None:
        self._driver_tasks = [asyncio.create_task(d.run()) for d in self.drivers]

    async def apply_plant_map(self, pm: PlantMap, config_version: Optional[int] = None,
                              revision: Optional[str] = None, origin: str = "remote") -> None:
        """Hot reload: stops the current drivers, loads the new map and starts its drivers — buffer, MQTT
        connection, edge counters (SQLite, by tag key), deadband/timestamp state and device clocks are kept."""
        if self._reload_lock is None:
            self._reload_lock = asyncio.Lock()
        async with self._reload_lock:
            old_version = self.config_version
            await self._stop_drivers()
            self.load_plant_map(pm)
            self.config_version, self.config_revision, self.config_origin = config_version, revision, origin
            self.reloads += 1
            if self._running:
                self._start_drivers()
            log.info("edge_gateway.config_reloaded", old_version=old_version, config_version=config_version,
                     revision=revision, tags=len(pm.tags), drivers=len(self.drivers))

    # ── fleet: identity, remote config, HTTP heartbeat ──────────────────
    async def bootstrap_identity(self) -> Optional[Credentials]:
        """Enrollment (first start with ENROLL_CODE) or the saved credentials; fills tenant, gateway id, token
        and the per-gateway MQTT user (env MQTT_USERNAME/MQTT_PASSWORD win)."""
        if not (settings.server_url or settings.enroll_code or settings.gateway_credentials_path):
            return None                  # legado: mapa em arquivo / GATEWAY_CONFIG_URL + GATEWAY_TOKEN do ambiente
        path = settings.gateway_credentials_path or DEFAULT_CREDENTIALS_PATH
        creds = await ensure_enrolled(path, settings.server_url, settings.enroll_code,
                                      allow_insecure=settings.allow_insecure_http)
        self.credentials = creds
        if creds is None:
            return None
        if settings.gateway_id and settings.gateway_id != creds.gateway_id:
            log.warning("edge_gateway.gateway_id_override", env=settings.gateway_id, enrolled=creds.gateway_id)
        self.gateway_id = settings.gateway_id or creds.gateway_id
        self.tenant_id = self.tenant_id or creds.tenant_id
        if not settings.mqtt_username and creds.mqtt_username:
            self.publisher = self._make_publisher(creds.mqtt_username, creds.mqtt_password)
        return creds

    def _remote_settings(self) -> tuple[str, str, str]:
        """(config url/base, fleet base url, token) — empty when the gateway is not in the fleet."""
        creds = self.credentials
        token = settings.gateway_token or (creds.token if creds else "")
        server = settings.server_url or (creds.server_url if creds else "")
        config = settings.gateway_config_url or server
        fleet = server or (base_url(settings.gateway_config_url) if settings.gateway_config_url else "")
        return config, fleet, token

    async def _load_remote(self) -> LoadedConfig:
        return await load_gateway_config(
            self._config_url, self.gateway_id or local_gateway_id(settings.plant_map_path), self._token,
            settings.gateway_config_cache, fallback_path=settings.plant_map_path,
            timeout_s=settings.gateway_config_timeout_s,
        )

    async def reload_config(self) -> bool:
        """Fetches the config (ETag) and applies it when the server has a new version. True = reloaded."""
        if not self._config_url:
            return False
        loaded = await self._load_remote()
        if loaded.origin != "remote":
            if loaded.detail and loaded.detail != "304":
                self._note_error(f"configuração: {loaded.detail}")
            return False
        if (loaded.config_version, loaded.revision) == (self.config_version, self.config_revision) \
                and loaded.config_version is not None:
            return False
        await self.apply_plant_map(loaded.plant_map, loaded.config_version, loaded.revision, loaded.origin)
        return True

    def _note_error(self, msg: str) -> None:
        if msg not in self._errors:
            self._errors.append(msg)

    def current_errors(self) -> list[str]:
        errs = []
        for sid, ok in self.source_status().items():
            if not ok:
                errs.append(f"fonte {sid} desconectada")
        if not self.publisher.is_connected:
            errs.append("broker MQTT desconectado")
        return errs + list(self._errors)

    def command_source(self, source_id: str) -> Optional[tuple[str, str]]:
        """(protocol, endpoint) of a source of the CURRENT map (endpoint with ${VAR} already expanded — the
        credentials never leave the gateway). None = unknown source."""
        src = self.plant_map.sources.get(source_id) if self.plant_map is not None else None
        if src is None:
            return None
        return src.protocol, src.endpoint

    async def fleet_heartbeat_body(self) -> dict:
        return heartbeat_body(__version__, time.monotonic() - self._started_at, self.buffer_depth(),
                              self.current_errors(), self.config_version)

    async def fleet_heartbeat_once(self) -> bool:
        """One HTTP heartbeat; reloads the config when the server says it changed. True = reloaded."""
        if self.fleet is None:
            return False
        try:
            res = await self.fleet.heartbeat(await self.fleet_heartbeat_body())
        except HeartbeatError as e:
            log.warning("edge_gateway.fleet_heartbeat_failed", error=str(e))
            self._note_error(str(e))
            return False
        self._errors.clear()            # delivered: the next heartbeat reports only what happens from now on
        self.fleet_status = res.status
        if res.commands and self.commands is not None:
            self.commands.submit(res.commands)      # background: never delays the heartbeat or the readings
        changed = res.config_changed or (res.config_version is not None and self.config_version is not None
                                         and res.config_version != self.config_version)
        if not changed:
            return False
        log.info("edge_gateway.config_changed", current=self.config_version, server=res.config_version)
        try:
            return await self.reload_config()
        except Exception as e:  # noqa: BLE001 — a bad map never kills the running gateway
            log.warning("edge_gateway.reload_failed", error=str(e))
            self._note_error(f"recarga da configuração falhou: {e}")
            return False

    async def _fleet_loop(self) -> None:
        if self.fleet is None:
            return
        period = max(1.0, settings.gateway_heartbeat_s)
        next_poll = time.monotonic() + max(period, settings.gateway_config_poll_s)
        while self._running:
            reloaded = await self.fleet_heartbeat_once()
            if not reloaded and time.monotonic() >= next_poll:
                try:
                    await self.reload_config()
                except Exception as e:  # noqa: BLE001
                    log.warning("edge_gateway.reload_failed", error=str(e))
            if reloaded or time.monotonic() >= next_poll:
                next_poll = time.monotonic() + max(period, settings.gateway_config_poll_s)
            await asyncio.sleep(period)

    # ── lifecycle ───────────────────────────────────────────────────────
    async def configure(self) -> None:
        """Identity + map (fleet → GATEWAY_CONFIG_URL → PLANT_MAP_PATH → MODBUS_TAGS)."""
        await self.bootstrap_identity()
        config_url, fleet_url, token = self._remote_settings()
        if config_url and (token or settings.gateway_config_url):
            self._config_url, self._token = config_url, token
            loaded = await self._load_remote()
            log.info("edge_gateway.config", origin=loaded.origin, revision=loaded.revision,
                     config_version=loaded.config_version, detail=loaded.detail)
            self.load_plant_map(loaded.plant_map)
            self.config_version, self.config_revision, self.config_origin = \
                loaded.config_version, loaded.revision, loaded.origin
            if fleet_url and token and self.gateway_id:
                self.fleet = FleetClient(fleet_url, self.gateway_id, token)
                self.commands = CommandRunner(fleet_url, self.gateway_id, token, self.command_source,
                                              timeout_s=settings.gateway_browse_timeout_s)
        elif settings.plant_map_path:
            self.load_plant_map(load_plant_map(settings.plant_map_path))
            self.config_origin = "file"
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
            log.warning("edge_gateway.no_tenant_id", hint="defina TENANT_ID, cadastre o gateway ou rode o provisionamento")

    async def start(self) -> None:
        log.info("edge_gateway.starting", version=__version__)
        await self.buffer.initialize()
        await self.configure()
        await self.publisher.connect()

        self._running = True
        self._started_at = time.monotonic()
        self.start_sender()
        self._start_drivers()
        await asyncio.gather(
            self._flush_buffer_loop(),
            self._heartbeat_loop(),
            self._refresh_loop(),
            self._fleet_loop(),
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
                              initial: bool = False, refresh: bool = False) -> None:
        # Timestamp at the source (UTC) when trustworthy, else the gateway read time; monotonic per tag.
        # `initial`: first OPC UA notification of the item (value's last change, not a skew sample).
        # `refresh`: re-offer of the last value (heartbeat of report-by-exception sources).
        agg = self._aggregates.get(tag_id)
        if agg is not None:
            if refresh:
                return
            clock = self._tag_clock.get(tag_id, self._default_clock)
            for end, v in agg.add(clock.stamp(ts, tag_id=tag_id, initial=initial), value):
                await self._publish(tag_id, v, self._stamp(tag_id, end, initial=True))
            return
        force = False
        if tag_id in self._stroke:
            if refresh:
                return                       # a stroke is a cycle event: never re-offered
            counter = self._stroke[tag_id]
            if counter is None:              # OPC UA: every notification is one stroke (same value included)
                force = not initial
            else:                            # Modbus: one stroke per counter advance
                c = self._last_values.get(counter)
                if c is None or c == self._stroke_seen.get(tag_id):
                    self._last_values[tag_id] = value
                    return
                self._stroke_seen[tag_id] = c
                force = True
        ts = self._stamp(tag_id, ts, initial)
        self._last_values[tag_id] = value
        # Silence (heartbeat) measured on the reading's own clock: same clock as the published ts.
        if not self.filter.should_publish(tag_id, value, bypass=tag_id in self._no_deadband, now=ts.timestamp(),
                                          force=force, deadband_pct=self._deadband_pct.get(tag_id)):
            return
        await self._publish(tag_id, value, ts)

    async def _publish(self, tag_id: str, value: float, ts: datetime) -> None:
        topic = self.publisher.build_topic(self.tenant_id, tag_id)
        payload = self.publisher.build_payload(value, self._units.get(tag_id), ts)
        await self._enqueue(topic, payload)

    # ── sender: outbox → broker / SQLite buffer (module docstring) ──────
    def buffer_depth(self) -> int:
        """Readings not yet delivered: SQLite rows + the in-memory outbox + the window being published."""
        return self.buffer.rows + len(self._outbox) + self._inflight_n

    def _sender_running(self) -> bool:
        return self._sender_task is not None and not self._sender_task.done()

    def _lock(self) -> asyncio.Lock:
        if self._pump_lock is None:
            self._pump_lock = asyncio.Lock()
        return self._pump_lock

    async def _enqueue(self, topic: str, payload: dict) -> None:
        if not self._sender_running():
            # no sender task (tests, tools, a sender that died): the same step, inline — nothing is ever stranded
            self._outbox.append((topic, payload, time.monotonic()))
            await self._pump(flush=False, force_spill=True)
            return
        while len(self._outbox) >= self._outbox_max and self._sender_running():
            self._space.clear()
            try:
                await asyncio.wait_for(self._space.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        self._outbox.append((topic, payload, time.monotonic()))
        self._wake.set()

    def _spill_due(self) -> bool:
        if not self._outbox:
            return False
        return (len(self._outbox) >= self._spill_batch
                or time.monotonic() - self._outbox[0][2] >= self._spill_age_s)

    async def _spill(self) -> int:
        """Writes the whole outbox to the SQLite buffer in one transaction (behind the rows already there)."""
        if not self._outbox:
            return 0
        items = list(self._outbox)
        self._outbox.clear()
        try:
            await self.buffer.store_many([(t, p) for t, p, _ in items])
        except BaseException as e:
            # put them back in front (older than whatever arrived meanwhile); bounded by outbox_max
            self._outbox.extendleft(reversed(items))
            if not isinstance(e, asyncio.CancelledError):
                log.warning("edge_gateway.spill_failed", error=str(e)[:200], count=len(items))
            raise
        finally:
            self._notify_space()
        return len(items)

    async def _publish_window(self, batch: list[tuple[str, dict, float]]) -> Optional[int]:
        """Publishes a window at once (wire order = list order). Index of the first failure, or None."""
        if len(batch) == 1:
            results = [await self.publisher.publish_payload(batch[0][0], batch[0][1])]
        else:
            results = await asyncio.gather(*(self.publisher.publish_payload(t, p) for t, p, _ in batch))
        for i, ok in enumerate(results):
            if not ok:
                return i
        return None

    def _notify_space(self) -> None:
        if self._space is not None and len(self._outbox) < self._outbox_max:
            self._space.set()

    async def _pump(self, *, flush: bool, force_spill: bool = False) -> int:
        """One step of the sender. Returns how many readings it moved (sent or stored)."""
        async with self._lock():
            connected = self.publisher.is_connected
            if self._outbox and self.buffer.rows == 0 and connected:
                n = min(len(self._outbox), self._window)
                batch = [self._outbox.popleft() for _ in range(n)]
                self._notify_space()
                self._inflight_n = n
                try:
                    failed = await self._publish_window(batch)
                except BaseException:
                    self._outbox.extendleft(reversed(batch))   # cancelled mid-window: resent later (idempotent)
                    raise
                finally:
                    self._inflight_n = 0
                if failed is None:
                    self.sent_direct += n
                    return n
                # the failed reading and everything after it are the oldest unsent: back in front, then to SQLite
                self._outbox.extendleft(reversed(batch[failed:]))
                await self._spill()
                return n
            if self.buffer.rows > 0 and connected and flush:
                # backlog first: new readings wait in memory and are spilled behind it
                if force_spill or self._spill_due():
                    await self._spill()
                return await self.buffer.flush_to(self.publisher.publish_payload, batch_size=500,
                                                  window=self._window, max_batches=1)
            if self._outbox and (force_spill or self._spill_due()):
                return await self._spill()
            return 0

    def start_sender(self) -> None:
        if self._sender_running():
            return
        self._wake = asyncio.Event()
        self._space = asyncio.Event()
        self._space.set()
        self._sender_task = asyncio.create_task(self._sender_loop(), name="edge_sender")

    async def _sender_loop(self) -> None:
        while True:
            try:
                moved = await self._pump(flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — never kill the sender
                log.warning("edge_gateway.sender_error", error=str(e)[:200], error_type=type(e).__name__)
                moved = 0
                await asyncio.sleep(0.5)
            if moved:
                await asyncio.sleep(0)               # let the drivers run between steps
                continue
            # idle: until a new reading, the spill deadline of the oldest one, or 1 s (reconnect / backlog)
            timeout = 1.0
            if self._outbox:
                timeout = max(0.01, min(timeout, self._spill_age_s - (time.monotonic() - self._outbox[0][2])))
            self._wake.clear()
            if self._outbox and self.publisher.is_connected and self.buffer.rows == 0:
                continue                             # raced with a new reading: send it now
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def _stop_sender(self) -> None:
        """Stops the sender and writes whatever is still in memory to the SQLite buffer (never lost on stop)."""
        task, self._sender_task = self._sender_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._outbox:
            try:
                await asyncio.wait_for(self._spill(), timeout=10.0)
            except BaseException as e:  # noqa: BLE001
                log.error("edge_gateway.final_spill_failed", error=str(e)[:200], lost=len(self._outbox))
        if self._space is not None:
            self._space.set()                        # producers still waiting go inline

    async def refresh_once(self) -> int:
        """Re-offers the last value of OPC UA tags (subscriptions only notify changes) so the
        deadband heartbeat can re-publish them. Only for connected sources: never fake fresh data
        from a source that is down. Returns how many values were re-offered."""
        n = 0
        for tag_id, drv in list(self._refresh_tags.items()):
            clock = self._tag_clock.get(tag_id, self._default_clock)
            # A device clock the gateway cannot map (source policy, skew check off) gets no synthesized ts.
            if drv.connected and tag_id in self._last_values and clock.can_synthesize:
                # Stamped on the device's clock (see _stamp / clock.py), never behind the tag's last ts.
                await self._handle_reading(tag_id, self._last_values[tag_id], refresh=True)
                n += 1
        return n

    async def _refresh_loop(self) -> None:
        if self.filter._max_silence is None:
            return
        period = max(1.0, self.filter._max_silence / 3)
        while self._running:
            await asyncio.sleep(period)
            if not self._refresh_tags:
                continue
            try:
                await self.refresh_once()
            except Exception as e:  # noqa: BLE001
                log.warning("edge_gateway.refresh_failed", error=str(e))

    def source_status(self) -> dict[str, bool]:
        sources = {}
        for sid, drv in self._sources.items():
            if isinstance(drv, ModbusDriver):
                sources[sid] = bool(drv._client.connected)
            else:
                sources[sid] = bool(getattr(drv, "connected", False))
        return sources

    def heartbeat_payload(self) -> dict:
        return {
            "v": HEARTBEAT_VERSION,
            "gateway_id": self.gateway_id,
            "ts": format_ts(datetime.now(timezone.utc)),
            "uptime_s": round(time.monotonic() - self._started_at, 1),
            "tags": len(self._units),
            "version": __version__,
            "config_version": self.config_version,
            "sources": self.source_status(),
            "clocks": {sid: c.status() for sid, c in self._clocks.items()},
        }

    def heartbeat_topic(self) -> str:
        return f"factory/{self.tenant_id}/gateways/{self.gateway_id or 'gateway'}/heartbeat"

    async def publish_heartbeat(self) -> bool:
        payload = self.heartbeat_payload()
        payload["buffered"] = self.buffer_depth()
        return await self.publisher.publish_payload(self.heartbeat_topic(), payload)

    def touch_health(self) -> None:
        """Marca de vida para o HEALTHCHECK do container (loop principal rodando), mesmo com o broker fora."""
        if settings.health_file:
            try:
                with open(settings.health_file, "w", encoding="utf-8") as f:
                    f.write(format_ts(datetime.now(timezone.utc)))
            except OSError as e:
                log.warning("edge_gateway.health_file_failed", error=str(e))

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                if self.publisher.is_connected:
                    await self.publish_heartbeat()
            except Exception as e:  # noqa: BLE001
                log.warning("edge_gateway.heartbeat_failed", error=str(e))
            self.touch_health()
            await asyncio.sleep(self.heartbeat_s)

    async def flush_once(self) -> int:
        """Reconnects if needed and re-sends the buffer in order. Returns rows sent here (0 while the sender task
        runs: it drains the buffer itself, interleaved with new readings)."""
        if not self.publisher.is_connected:
            await self.publisher.connect()
        if not self.publisher.is_connected:
            return 0
        if self._sender_running():
            self._wake.set()
            return 0
        async with self._lock():
            await self._spill()
            return await self.buffer.flush_to(self.publisher.publish_payload, window=self._window)

    async def _flush_buffer_loop(self) -> None:
        """Reconnects to the broker when it was lost (the sender then flushes the buffer)."""
        while self._running:
            await asyncio.sleep(self.flush_interval_s)
            try:
                await self.flush_once()
            except Exception as e:  # noqa: BLE001 — never kill the loop
                log.warning("edge_gateway.flush_failed", error=str(e))

    async def stop(self) -> None:
        self._running = False
        await self._stop_drivers()                   # drivers hand their queued readings to the outbox first
        await self._stop_sender()
        if self.commands is not None:
            await self.commands.close()
        if self.fleet is not None:
            await self.fleet.close()
        await self.publisher.disconnect()
        await self.buffer.close()
        if self.counter_store is not None:
            self.counter_store.close()
            self.counter_store = None
        log.info("edge_gateway.stopped")


def configure_logging(level: str) -> None:
    """structlog without configuration logs every level (one debug line per reading sent)."""
    import logging
    lvl = getattr(logging, str(level or "info").upper(), logging.INFO)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(lvl))


async def main() -> None:
    configure_logging(settings.log_level)
    try:
        gateway = EdgeGateway()
    except ValueError as e:              # e.g. MQTT TLS settings (missing CA file)
        log.error("edge_gateway.invalid_settings", error=str(e))
        raise SystemExit(2) from None

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    async def _shutdown() -> None:
        try:
            await asyncio.wait_for(gateway.stop(), timeout=30.0)
        except BaseException as e:  # noqa: BLE001 — a stuck stop never blocks SIGTERM
            log.error("edge_gateway.stop_timeout", error=str(e)[:200])
        finally:
            if main_task is not None:
                main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    try:
        await gateway.start()
    except EnrollmentError as e:
        log.error("edge_gateway.enrollment_failed", error=str(e))
        raise SystemExit(3) from None
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
