"""
MQTT subscriber — consumes sensor readings published by the Edge Gateway (Sprint 5b: bounded pipeline).

    broker (QoS 1, persistent session, stable client id)
      └► receive loop ─► per-tag ordered queues ─► fixed worker pool ─► _process_reading()
                                                                         ├ tag check (tenant isolation)
                                                                         ├ live WebSocket broadcast
                                                                         ├ Influx write (batched, retried)
                                                                         ├ machine status, usage, alert rules
                                                                         └ watermarks / late-data marks
      ◄── PUBACK in receive order once the reading is finished (stored, or in ingest_dead_letters)

No reading is dropped silently: transient failures (database/Influx unavailable, timeouts) are retried with
backoff for up to MQTT_TRANSIENT_RETRY_S while the pipeline holds the reading (backpressure: when MQTT_MAX_INFLIGHT
readings are inside, the receive loop stops and the broker keeps the backlog); a valid reading that still cannot be
processed goes to the dead-letter table (fm_ingest_dead_letters, log ingest.dead_letter). Readings that are not
valid (topic/payload malformed, tag unknown in the topic's tenant, revoked gateway) are rejected and counted in
fm_mqtt_messages{result}. See services/ingest/.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Optional

import aiomqtt
import structlog
from prometheus_client import Gauge

from ..config import settings
from ..db import AsyncSessionLocal
from ..services.influx import influx, line_protocol
from ..services import influx_downsampling as rollups
from ..services.payload_crypto import PayloadError, decode_payload, key_error
from ..services.machine_status import broadcast_change, mark_seen, mark_seen_many
from ..services.oee.detector import note_late
from ..services.predictive.engine import late_threshold as predictive_late_threshold
from ..services.metrics import INGEST_RETRIES, MQTT_LAG, MQTT_MESSAGES
from ..services.validation import InvalidIdentifier, ensure_uuid
from ..services.usage.broadcaster import usage_broadcaster
from ..services.usage.processor import process_many, process_reading, process_readings  # noqa: F401
from ..services.usage.cache import topology_cache
from ..models.tag import COUNTER_ROLES, SemanticRole
from ..rules.engine import evaluate, note_value, rule_tags
from ..websocket.manager import manager
from .ingest import dead_letter, watermarks
from .ingest.pipeline import Item, OrderedPipeline
from .ingest.transient import is_transient, retry_transient

log = structlog.get_logger()

TOPIC = "factory/+/sensors/#"   # factory/{tenant_id}/sensors/{tag_id}
_DEBUG = str(settings.log_level).upper() == "DEBUG"

USAGE_ROLES = frozenset(COUNTER_ROLES | {SemanticRole.run_state, SemanticRole.fault_state})

# Rejected (unknown / other-tenant) tags are logged once per tag at warning level, then at debug.
_rejected_logged: set[str] = set()

# Kept for dashboards/alerts that already use it (docs/operations.md §8.2): same value as fm_ingest_queue_depth.
INFLIGHT = Gauge("fm_mqtt_inflight_messages", "MQTT readings received and not yet processed")
BACKLOG_WARN = 1000

_pipeline: Optional[OrderedPipeline] = None
_clients: dict[int, Any] = {}          # connection generation -> aiomqtt client (for the acknowledgements)


def pipeline() -> Optional[OrderedPipeline]:
    return _pipeline


def cancel_inflight() -> list[asyncio.Task]:
    """Shutdown: cancels the pipeline's workers and pending writes; returns them so the caller can wait (bounded).
    Readings not finished were not acknowledged: the broker redelivers them (persistent session)."""
    if _pipeline is None:
        return []
    tasks = _pipeline.tasks()
    for t in tasks:
        t.cancel()
    return tasks


def _enable_manual_ack(client: Any) -> bool:
    """Turns on paho's manual acknowledgement (aiomqtt does not expose it). False if unavailable: paho then
    acknowledges on receipt and the subscriber has no broker-side flow control (only the in-process bound)."""
    paho = getattr(client, "_client", None)
    setter = getattr(paho, "manual_ack_set", None)
    if setter is None or not hasattr(paho, "ack"):
        log.warning("mqtt.manual_ack_unavailable")
        return False
    setter(True)
    return True


# Sprint 7 (scale): packets read per socket-readable event. aiomqtt's reader callback reads ONE MQTT packet per event
# loop iteration (paho loop_read with nothing in flight); with the pipeline busy an iteration takes a few ms, so the
# subscriber received only ~250 readings/s however fast the processing was — the broker kept the rest (QA load test:
# 2 600/s published, 60–120 s of lag). This reads every packet already buffered on the socket, up to this many.
READ_BURST = 2000


def _burst_reader(client: Any) -> bool:
    """Replaces aiomqtt's socket reader with one that drains the socket (READ_BURST packets per event). Same error
    handling as aiomqtt's own callback (an exception = the connection broke → the client is disconnected). False when
    the library internals are not the expected ones (the default reader stays: correct, only slower)."""
    import paho.mqtt.client as paho_mqtt
    paho = getattr(client, "_client", None)
    sock = paho.socket() if paho is not None and hasattr(paho, "socket") else None
    disconnected = getattr(client, "_disconnected", None)
    if sock is None or not hasattr(paho, "_packet_read") or not hasattr(paho, "_loop_rc_handle") \
            or disconnected is None:
        log.warning("mqtt.burst_reader_unavailable")
        return False
    again = paho_mqtt.MQTTErrorCode.MQTT_ERR_AGAIN

    def callback() -> None:
        try:
            for _ in range(READ_BURST):
                if paho._sock is None:         # noqa: SLF001
                    return
                rc = paho._packet_read()       # noqa: SLF001
                if rc == again:
                    return
                if rc > 0:
                    paho._loop_rc_handle(rc)   # noqa: SLF001
                    return
        except Exception as exc:  # noqa: BLE001
            if not disconnected.done():
                disconnected.set_exception(exc)

    loop = asyncio.get_running_loop()
    loop.remove_reader(sock.fileno())
    loop.add_reader(sock.fileno(), callback)
    _skip_empty_topic_filters(paho)
    return True


def _skip_empty_topic_filters(paho: Any) -> None:
    """paho walks its per-topic callback trie for every message (message_callback_add filters); aiomqtt registers
    none, so that walk (~5 % of the ingestion CPU at 2 600 readings/s) always finds nothing. Same answer, without the
    walk, while no filter is registered."""
    m = getattr(paho, "_on_message_filtered", None)
    if m is None or not hasattr(m, "iter_match") or getattr(m, "_fm_fast", False):
        return
    root = getattr(m, "_root", None)
    if root is None or not hasattr(root, "_children"):
        return
    orig = m.iter_match

    def iter_match(topic):
        if not root._children:
            return iter(())
        return orig(topic)
    m.iter_match = iter_match
    m._fm_fast = True


async def _drain_messages(client: Any):
    """client.messages, without one event-loop round trip per message (Sprint 7, scale): aiomqtt's iterator creates
    a task and an asyncio.wait for EVERY message, i.e. at least two loop iterations each — with the pipeline busy
    (an iteration of a few ms) the subscriber took ~700 readings/s off the queue however fast everything else was.
    Messages already queued are taken directly; only an empty queue waits through aiomqtt (which also raises on a
    disconnection)."""
    queue = getattr(client, "_queue", None)
    disconnected = getattr(client, "_disconnected", None)
    it = client.messages.__aiter__()
    while True:
        if queue is not None and disconnected is not None:
            while not disconnected.done():
                try:
                    message = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                yield message
        yield await it.__anext__()


def _ack_item(item: Item) -> None:
    client = _clients.get(item.gen)
    if client is not None:
        client._client.ack(item.mid, item.qos)   # noqa: SLF001 — called in receive order by the pipeline


def shard_of(topic: str, count: int) -> int:
    """Ingestion shard of a reading (Sprint 7): crc32(tag_id) % count — stable, so a tag always has the same shard."""
    import zlib
    parts = topic.split("/", 4)
    tag = parts[3] if len(parts) > 3 else topic
    return zlib.crc32(tag.encode()) % max(1, count)


def client_identifier() -> str:
    base = settings.mqtt_client_identifier
    return f"{base}-s{settings.mqtt_shard_index}" if settings.mqtt_shard_count > 1 else base


def _shard_key(topic: str) -> tuple[str, str]:
    """Per-tag ordered queue key (tenant, tag) straight from the topic (validated later by the processor)."""
    parts = topic.split("/", 4)
    return (parts[1] if len(parts) > 1 else "", parts[3] if len(parts) > 3 else topic[:200])


def parse_source_ts(raw: Any, received_at: datetime) -> datetime:
    """Payload `ts` (ISO-8601, `Z` or offset; naive = UTC) → aware UTC datetime.

    Missing/invalid → received_at. A timestamp in the future (gateway clock ahead
    of the server) is clamped to received_at: we never store the future. A timestamp
    older than settings.source_ts_max_age_s is not sane (PLC/gateway clock unset,
    e.g. 1970) → received_at.
    """
    if not isinstance(raw, str) or not raw:
        return received_at
    try:
        ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return received_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)
    if ts > received_at:
        return received_at
    if (received_at - ts).total_seconds() > settings.source_ts_max_age_s:
        log.warning("mqtt.insane_source_ts", ts=raw[:40])
        return received_at
    return ts


def replay_threshold() -> timedelta:
    """Readings older than this (vs. receive time) are edge-backlog replays, not live data."""
    return timedelta(seconds=2 * settings.machine_offline_timeout_s)


def _new_pipeline() -> OrderedPipeline:
    return OrderedPipeline(_process_item, capacity=settings.mqtt_max_inflight,
                           workers=settings.mqtt_max_concurrency, acker=_ack_item,
                           process_batch=_process_items_dyn, keys_per_turn=settings.mqtt_keys_per_turn,
                           group_of=lambda key: key[0])


async def _process_items_dyn(items):
    return await _process_items(items)


async def run_subscriber() -> None:
    """Long-running task started on app lifespan. Never exits (until cancelled)."""
    global _pipeline
    err = key_error(settings.encryption_key)
    if err:
        log.error("mqtt.encryption_key_invalid", error=err)
    pl = _pipeline = _new_pipeline()
    pl.start()
    shards, my_shard = max(1, settings.mqtt_shard_count), settings.mqtt_shard_index
    flusher = asyncio.create_task(watermarks.run_flusher(), name="ingest_watermarks")
    monitor = asyncio.create_task(_backlog_monitor(pl), name="ingest_backlog_monitor")
    try:
        while True:
            gen = None
            try:
                async with aiomqtt.Client(
                    hostname=settings.mqtt_broker_host,
                    port=settings.mqtt_broker_port,
                    username=settings.mqtt_username or None,
                    password=settings.mqtt_password or None,
                    identifier=client_identifier(),
                    clean_session=not settings.mqtt_persistent_session,
                ) as client:
                    gen = pl.new_connection()
                    _clients[gen] = client
                    log.info("mqtt.connected", broker=settings.mqtt_broker_host,
                             client_id=client_identifier(), shard=settings.mqtt_shard_index,
                             shards=settings.mqtt_shard_count,
                             persistent_session=settings.mqtt_persistent_session)
                    manual_ack = _enable_manual_ack(client)
                    _burst_reader(client)
                    # QoS 1 + acknowledge-when-finished = flow control: the broker sends at most its
                    # max_inflight_messages unacknowledged readings and queues the rest (bounded, on its side).
                    await client.subscribe(TOPIC, qos=1)
                    log.info("mqtt.subscribed", topic=TOPIC)
                    # a cancellation delivered while connecting can be swallowed by the client library:
                    # the task is still marked as cancelling, so stop instead of serving forever
                    if _cancelling():
                        raise asyncio.CancelledError()

                    async for message in _drain_messages(client):
                        if _cancelling():
                            raise asyncio.CancelledError()
                        topic = str(message.topic)
                        if shards > 1 and shard_of(topic, shards) != my_shard:
                            # another shard's tag: acknowledged at once (that shard has its own subscription)
                            if manual_ack and (getattr(message, "qos", 0) or 0):
                                client._client.ack(message.mid, message.qos)   # noqa: SLF001
                            continue
                        # waits while MQTT_MAX_INFLIGHT readings are inside the pipeline (backpressure)
                        await pl.put(Item(key=_shard_key(topic), topic=topic, payload=bytes(message.payload),
                                          received_at=datetime.now(timezone.utc), mid=message.mid,
                                          qos=(getattr(message, "qos", 0) or 0) if manual_ack else 0, gen=gen))

            except aiomqtt.MqttError as e:
                # aiomqtt turns a cancellation during message iteration into MqttError("Disconnected
                # during message iteration"): treating it as a disconnect made the task reconnect forever
                # and uvicorn hang on shutdown (Sprint 2 review). A cancelled task stops here.
                if _cancelling():
                    raise asyncio.CancelledError() from e
                log.warning("mqtt.disconnected", error=str(e))
                await asyncio.sleep(5)
            except Exception as e:
                if _cancelling():
                    raise asyncio.CancelledError() from e
                log.error("mqtt.subscriber.error", error=str(e))
                await asyncio.sleep(5)
            finally:
                if gen is not None:
                    _clients.pop(gen, None)
    finally:
        monitor.cancel()
        flusher.cancel()
        await asyncio.gather(monitor, flusher, return_exceptions=True)
        await flush_late_marks(timeout=3.0)
        await pl.stop()


async def flush_late_marks(timeout: float = 3.0) -> None:
    """Writes the in-memory late-data marks to Postgres: watermarks (`ingest_watermarks` → predictive_dirty) and the
    downtime detector's late notes. The subscriber does it on shutdown; any other process that runs readings through
    `_handle_message` (the dead-letter replay CLI) must do it before exiting, or the late readings it stored are
    never re-extracted. Failures are logged, never raised."""
    try:
        await asyncio.wait_for(watermarks.flush_now(), timeout=timeout)
    except BaseException as e:  # noqa: BLE001 — late marks lost: /recompute re-extracts
        log.warning("ingest.watermarks.final_flush_failed", error=str(e)[:200])
    try:                                # the downtime detector's late notes too (restart: not lost)
        from .oee import detector as downtime_detector
        from .oee.detector import flush_late as downtime_flush_late

        async def _flush_downtime():
            if not downtime_detector._late:   # noqa: SLF001
                return
            async with AsyncSessionLocal() as db:
                await downtime_flush_late(db, datetime.now(timezone.utc))
        await asyncio.wait_for(_flush_downtime(), timeout=timeout)
    except BaseException as e:  # noqa: BLE001
        log.warning("downtime.late_final_flush_failed", error=str(e)[:200])


async def _backlog_monitor(pl: OrderedPipeline) -> None:
    warned = 0.0
    while True:
        await asyncio.sleep(1.0)
        INFLIGHT.set(pl.inflight)
        now = asyncio.get_running_loop().time()
        if pl.inflight >= min(BACKLOG_WARN, pl.capacity) and now - warned > 30:
            warned = now
            log.warning("mqtt.backlog", inflight=pl.inflight, capacity=pl.capacity,
                        hint="o servidor não está acompanhando a taxa publicada pelos gateways; o broker guarda o resto")


def _cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


async def _process_item(item: Item) -> Optional[Awaitable]:
    return await _process_reading(item.topic, item.payload, item.received_at)


async def _handle_message(topic: str, raw: bytes, received_at: Optional[datetime] = None) -> None:
    """Processes one reading to the end (Influx write included). The pipeline calls _process_reading directly
    (the write is awaited outside the tag's turn); this is the entry point for tests and dead-letter replay."""
    fin = await _process_reading(topic, raw, received_at or datetime.now(timezone.utc))
    if fin is not None:
        await fin


async def _lookup_tag(tenant_id: str, tag_id: str):
    async with AsyncSessionLocal() as db:
        return await asyncio.wait_for(topology_cache.tag_info(db, tenant_id, tag_id), timeout=5.0)


def _is_transient(e: BaseException) -> bool:
    return is_transient(e)


RETRY_FIRST_S = 0.5
RETRY_MAX_S = 15.0


async def _retry_transient(what: str, topic: str, fn, *args, stage: str = "process"):
    """Calls fn(*args); on a transient failure retries with exponential backoff for up to
    settings.mqtt_transient_retry_s, then re-raises. Non-transient errors are raised at once. The reading keeps
    its place in the pipeline meanwhile, so a persistent outage turns into backpressure, not a growing heap."""
    return await retry_transient(what, lambda: fn(*args), settings.mqtt_transient_retry_s,
                                 first_s=RETRY_FIRST_S, max_s=RETRY_MAX_S,
                                 on_retry=lambda n, e: INGEST_RETRIES.labels(stage).inc(), topic=topic[:200])


def influx_record(tenant_id: str, tag_id: str, value: float, unit: Any, ts: datetime, machine_id: Optional[str]):
    """The Influx record of a reading: its line protocol (fast path), or the dict record (non-finite value)."""
    line = line_protocol(tenant_id, tag_id, machine_id or "", unit or "", value, ts)
    if line is not None:
        return line
    return {"measurement": "sensors",
            "tags": {"tenant_id": tenant_id, "tag_id": tag_id, "machine_id": machine_id or "", "unit": unit or ""},
            "fields": {"value": value}, "time": ts.isoformat()}


def _start_writes(preps: list["_Prep"]) -> None:
    """2. Influx: the records of the batch in one write_many; every reading's `fin` is the same task, which
    completes when the write is durable (watermarks / late notes per reading, dead letters if it failed)."""
    if not preps:
        return
    write = influx.write_many([p.record for p in preps])
    fin = asyncio.ensure_future(_finish_writes(list(preps), write))
    for p in preps:
        p.fin = fin


async def _finish_writes(preps: list["_Prep"], write: Awaitable) -> None:
    try:
        await write
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        if _cancelling():
            raise asyncio.CancelledError() from e
        for p in preps:
            MQTT_MESSAGES.labels("error").inc()
            await dead_letter.record(p.topic, p.raw, p.received_at, "influx", e)
        return
    now = datetime.now(timezone.utc)
    late_after = predictive_late_threshold()
    rollup_late = rollups.late_threshold()
    for p in preps:
        written_at = max(p.received_at, now)
        age = written_at - p.ts
        tag = p.tag
        if age > rollup_late:
            rollups.note_late(p.tenant_id, p.ts)   # behind the 1-minute task's look-back: backfill its aggregates
        if (tag.machine_id and tag.role in USAGE_ROLES
                and age.total_seconds() > settings.downtime_settle_s):
            note_late(p.tenant_id, tag.machine_id, p.ts)
        watermarks.note(p.tenant_id, tag.id, tag.machine_id, p.ts, late=age > late_after)


# mark_seen throttle: machine_id -> (monotonic time of the last call, latest seen_at sent). Within
# SEEN_THROTTLE_S of a call, a reading of the same machine that would not move last_seen_at by the
# write interval (machine_last_seen_write_interval_s) is a database no-op — skip the round trip.
SEEN_THROTTLE_S = 1.0       # minimum; the effective throttle is max(this, machine_last_seen_write_interval_s)
_seen_cache: dict[str, tuple[float, datetime, bool]] = {}


def _is_fresh(seen_at: datetime, received_at: datetime) -> bool:
    return seen_at >= received_at - timedelta(seconds=settings.machine_offline_timeout_s)


def _seen_is_noop(machine_id: Optional[str], seen_at: datetime, received_at: datetime) -> bool:
    """True when mark_seen would change nothing: same machine called < SEEN_THROTTLE_S ago with a reading
    at most the write interval older, and (for a live reading) that call was live too — so it already
    turned a machine without run_state 'running'."""
    if not machine_id:
        return False
    c = _seen_cache.get(machine_id)
    if c is None:
        return False
    called, last, was_fresh = c
    if _is_fresh(seen_at, received_at) and not was_fresh:
        return False
    # Sprint 7 (scale): one machine has ~13 tags at 1 Hz; within the write interval last_seen_at would not move, so
    # skip the UPDATE (it also contended for the machine row with the run_state usage transaction)
    throttle = max(SEEN_THROTTLE_S, float(settings.machine_last_seen_write_interval_s))
    return (asyncio.get_running_loop().time() - called < throttle
            and (seen_at - last).total_seconds() < settings.machine_last_seen_write_interval_s)


def _seen_called(machine_id: Optional[str], seen_at: datetime, received_at: datetime) -> None:
    if not machine_id:
        return
    if len(_seen_cache) > 50_000:
        _seen_cache.clear()
    prev = _seen_cache.get(machine_id)
    _seen_cache[machine_id] = (asyncio.get_running_loop().time(), max(seen_at, prev[1]) if prev else seen_at,
                               _is_fresh(seen_at, received_at))


@dataclass
class _Prep:
    """A reading accepted (tag checked) whose Influx write has started."""
    idx: int
    topic: str
    raw: bytes
    received_at: datetime
    tenant_id: str
    tag_id: str
    tag: Any
    value: float
    unit: Any
    ts: datetime
    replayed: bool
    fin: Any = None
    record: Any = None


async def _process_reading(topic: str, raw: bytes, received_at: datetime) -> Optional[Awaitable]:
    """
    Expected topic:  factory/{tenant_id}/sensors/{tag_id}
    Expected payload: {"value": 245.3, "unit": "°C", "ts": "2026-04-04T10:00:00Z"}

    `ts` is the source timestamp set by the edge (also for readings replayed from
    its SQLite backlog). It is what goes to Influx, last_seen_at and the usage
    processing. Readings whose tag does not exist in the topic's tenant are
    rejected (nothing stored, nothing broadcast). Readings older than
    replay_threshold() are history: they are stored and counted in the usage
    accumulators, but not pushed to live WebSocket clients, they don't change the
    machine status and alert rules are not evaluated on them.

    Returns the pending Influx write (awaited by the pipeline outside the tag's turn) or None.
    """
    return (await _process_batch([(topic, raw, received_at)]))[0]


async def _process_items(items: list[Item]) -> list[Optional[Awaitable]]:
    return await _process_batch([(it.topic, it.payload, it.received_at) for it in items])


async def _process_batch(entries: list[tuple[str, bytes, datetime]]) -> list[Optional[Awaitable]]:
    """Readings of one or (Sprint 7) many tags, in arrival order per tag (what queued up for them in the pipeline):
    each is checked and broadcast on its own; all of them go to Influx in one request; the machines' last_seen_at in
    one transaction; the usage accumulators of each tenant in ONE transaction (usage.processor.process_many — or
    process_readings for a single tag). Returns, per reading, its pending Influx write (or None)."""
    out: list[Optional[Awaitable]] = [None] * len(entries)
    preps: list[_Prep] = []
    for idx, (topic, raw, received_at) in enumerate(entries):
        p = None
        try:
            p = await _prepare(idx, topic, raw, received_at)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a bug: the reading is kept as a dead letter, not lost
            if _cancelling():
                raise asyncio.CancelledError() from e
            MQTT_MESSAGES.labels("error").inc()
            log.exception("mqtt.message.error", topic=topic, error=str(e))
            await dead_letter.record(topic, raw, received_at, "process", e)
        if p is not None:
            preps.append(p)
    _start_writes(preps)
    # 3. machine status (last_seen_at): one transaction for the machines of the batch (throttled per machine)
    await _mark_seen_batch(preps)
    # 4. usage: consecutive readings of the same tag → one group; the groups of one tenant → one transaction
    failed: set[int] = set()
    by_tag: dict[str, list[_Prep]] = {}             # arrival order kept within each tag
    for p in preps:
        if p.tag.role in USAGE_ROLES:
            by_tag.setdefault(p.tag.id, []).append(p)
    by_tenant: dict[str, list[list[_Prep]]] = {}
    for g in by_tag.values():
        by_tenant.setdefault(g[0].tenant_id, []).append(g)
    for tgroups in by_tenant.values():
        failed |= await (_usage(tgroups[0]) if len(tgroups) == 1 else _usage_many(tgroups))
    for p in preps:
        if p.idx in failed:
            continue
        try:
            out[p.idx] = await _finish(p)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if _cancelling():
                raise asyncio.CancelledError() from e
            MQTT_MESSAGES.labels("error").inc()
            log.exception("mqtt.message.error", topic=p.topic, error=str(e))
            await p.fin                    # the write was already started: let it land (idempotent on replay)
            await dead_letter.record(p.topic, p.raw, p.received_at, "process", e)
    return out


async def _prepare(idx: int, topic: str, raw: bytes, received_at: datetime) -> Optional[_Prep]:
    parts = topic.split("/")
    if len(parts) < 4:
        MQTT_MESSAGES.labels("invalid").inc()
        return None
    try:
        tenant_id = ensure_uuid(parts[1], "tenant_id")
        tag_id    = ensure_uuid(parts[3], "tag_id")
    except InvalidIdentifier:
        log.warning("mqtt.invalid_topic", topic=topic[:200])
        MQTT_MESSAGES.labels("invalid").inc()
        return None
    try:
        data = decode_payload(topic, raw, settings.encryption_key, settings.mqtt_require_encryption)
    except PayloadError as e:
        log.warning("mqtt.payload_rejected", topic=topic[:200], reason=str(e))
        MQTT_MESSAGES.labels("invalid").inc()
        return None
    try:
        value = float(data["value"])
    except (KeyError, TypeError, ValueError):
        log.warning("mqtt.payload_rejected", topic=topic[:200], reason="value")
        MQTT_MESSAGES.labels("invalid").inc()
        return None
    unit  = data.get("unit")
    ts    = parse_source_ts(data.get("ts"), received_at)
    replayed = received_at - ts > replay_threshold()

    # 0. The tag must belong to the topic's tenant (fail closed if it can't be checked)
    #    A tag that does not exist is rejected; a check that could not be made (timeout, database
    #    unavailable) is retried, then dead-lettered — never dropped (Sprint 5 QA).
    # Sprint 7 (scale): a cached tag is resolved without awaiting — every await that hops through a task
    # (wait_for) costs a full event-loop iteration per reading, and readings of a batch are prepared one by one
    tag = topology_cache.peek_tag(tenant_id, tag_id)
    try:
        if tag is None:
            tag = await _retry_transient("mqtt.tag_lookup", topic, _lookup_tag, tenant_id, tag_id, stage="lookup")
    except Exception as e:  # noqa: BLE001
        if _cancelling():
            raise asyncio.CancelledError() from e
        MQTT_MESSAGES.labels("error").inc()
        await dead_letter.record(topic, raw, received_at, "lookup", e)
        return None
    if tag is None:
        MQTT_MESSAGES.labels("rejected").inc()
        if tag_id not in _rejected_logged and len(_rejected_logged) < 10_000:
            _rejected_logged.add(tag_id)
            log.warning("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
        else:
            log.debug("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
        return None

    if tag.gateway_revoked:
        # the gateway that reads this tag was revoked (stolen / decommissioned): its MQTT credential may
        # still be on the broker until the operator removes it — the server no longer accepts its data
        MQTT_MESSAGES.labels("rejected").inc()
        key = f"revoked:{tag_id}"
        if key not in _rejected_logged and len(_rejected_logged) < 10_000:
            _rejected_logged.add(key)
            log.warning("mqtt.rejected_revoked_gateway", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
        return None

    if _DEBUG:                  # Sprint 7: a filtered structlog call still cost a bind per reading (scale profile)
        log.debug("mqtt.message", tenant_id=tenant_id[:8], tag_id=tag_id[:8], value=value, replayed=replayed)

    # 1. Broadcast live readings to WebSocket clients immediately (best effort: a client never costs the reading)
    if not replayed:
        msg = {"type": "sensor_reading", "tag_id": tag_id, "value": value, "unit": unit,
               "timestamp": ts.isoformat()}
        try:
            if manager.relay is not None:
                # ingestion process: the relay only buffers the message (websocket/relay.py) — no timeout task
                await manager.broadcast(tenant_id, msg)
            else:
                await asyncio.wait_for(manager.broadcast(tenant_id, msg), timeout=2.0)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt.ws_broadcast.error", error=str(e)[:200])

    # 2. Persist to InfluxDB at the source timestamp. Batched with the other readings in flight and retried on
    #    transient failures (influx.py); awaited by the pipeline after this tag's turn (the next reading of the
    #    tag does not wait for the HTTP round trip).
    #    Sprint 7: the records of the whole batch go out as one write_many (one future per batch, _start_writes).
    p = _Prep(idx, topic, raw, received_at, tenant_id, tag_id, tag, value, unit, ts, replayed)
    p.record = influx_record(tenant_id, tag_id, value, unit, ts, tag.machine_id)

    return p


async def _mark_seen_batch(preps: list[_Prep]) -> None:
    """3. Machine status derived from data (last_seen_at + running) — derived state, best effort. Per machine, the
    reading with the latest source timestamp of the batch is what mark_seen would end with (last_seen_at never moves
    backwards; a fresh one flips unknown/stopped → running); machines throttled (_seen_is_noop) are skipped. All the
    machines of the batch in one transaction, in machine-id order (Sprint 7: one transaction per reading did not
    scale)."""
    best: dict[str, _Prep] = {}
    for p in preps:
        mid = p.tag.machine_id
        if not mid or _seen_is_noop(mid, p.ts, p.received_at):
            continue
        b = best.get(mid)
        if b is None or p.ts > b.ts or (p.ts == b.ts and _is_fresh(p.ts, p.received_at)):
            best[mid] = p
    if not best:
        return
    changes = []
    by_tenant: dict[str, list[_Prep]] = {}
    for mid in sorted(best):
        by_tenant.setdefault(best[mid].tenant_id, []).append(best[mid])
    try:
        async with AsyncSessionLocal() as db:
            async def run():
                for tid, ps in by_tenant.items():
                    changes.extend(await mark_seen_many(db, tid, [(p.tag_id, p.ts, p.received_at) for p in ps]))
                await db.commit()
                for p in best.values():
                    _seen_called(p.tag.machine_id, p.ts, p.received_at)
            await asyncio.wait_for(run(), timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("mqtt.machine_status.timeout", machines=len(best))
        return
    except Exception as e:
        if _cancelling():
            raise asyncio.CancelledError() from e
        log.warning("mqtt.machine_status.error", machines=len(best), error=str(e)[:300])
        return
    for change in changes:
        try:
            await broadcast_change(change)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt.machine_status.broadcast_error", error=str(e)[:200])


async def _usage_many(groups: list[list[_Prep]]) -> set[int]:
    """4. Usage of several tags of ONE tenant (each group = consecutive readings of one tag) in one transaction
    (usage.processor.process_many). A transient failure is retried (the transaction rolled back: exact); any other
    failure falls back to one transaction per tag (_usage), which isolates and dead-letters the bad one."""
    pairs = [(g[0].tag, [(p.value, p.ts, not p.replayed) for p in g]) for g in groups]

    async def many_once():
        async with AsyncSessionLocal() as db:
            return await asyncio.wait_for(process_many(db, groups[0][0].tenant_id, pairs),
                                          timeout=settings.usage_process_timeout_s)
    try:
        results = await _retry_transient("mqtt.usage", groups[0][0].topic, many_once, stage="usage")
    except Exception as e:  # noqa: BLE001
        if _cancelling():
            raise asyncio.CancelledError() from e
        log.warning("mqtt.usage.batch_fallback", tags=len(groups), error=str(e)[:300])
        failed: set[int] = set()
        for g in groups:
            failed |= await _usage(g)
        return failed
    for g, outcomes in zip(groups, results):
        await _usage_broadcast(g, outcomes)
    return set()


async def _usage_broadcast(group: list[_Prep], outcomes) -> None:
    tag = group[0].tag
    try:
        for outcome in outcomes:
            for u in outcome.updates:
                usage_broadcaster.add(u)
            if outcome.status_change is not None:
                await broadcast_change(outcome.status_change)
            if outcome.event:
                log.info("usage.counter_event", tag_id=tag.id[:8], usage_event=outcome.event)
    except Exception as e:  # noqa: BLE001
        log.warning("mqtt.usage.broadcast_error", topic=group[0].topic, error=str(e))


async def _usage(group: list[_Prep]) -> set[int]:
    """4. Usage: counters / run_state / fault_state (domain.md §3.3–3.4, §1.1) for consecutive readings of one tag,
    one transaction. Transient failures are retried (the transaction rolled back, so the retry is exact); a
    non-transient failure of a run is retried reading by reading to isolate the bad one; what still fails is
    dead-lettered (replay is idempotent: duplicates are ignored). Returns the indexes dead-lettered."""
    tag = group[0].tag
    readings = [(p.value, p.ts, not p.replayed) for p in group]

    async def usage_once():
        async with AsyncSessionLocal() as db:
            return await asyncio.wait_for(process_readings(db, tag, readings),
                                          timeout=settings.usage_process_timeout_s)
    try:
        outcomes = await _retry_transient("mqtt.usage", group[0].topic, usage_once, stage="usage")
    except Exception as e:  # noqa: BLE001
        if _cancelling():
            raise asyncio.CancelledError() from e
        if len(group) > 1 and not is_transient(e):
            failed: set[int] = set()
            for p in group:
                failed |= await _usage([p])
            return failed
        for p in group:
            MQTT_MESSAGES.labels("error").inc()
            await p.fin
            await dead_letter.record(p.topic, p.raw, p.received_at, "usage", e)
        return {p.idx for p in group}
    try:
        for outcome in outcomes:
            for u in outcome.updates:
                usage_broadcaster.add(u)
            if outcome.status_change is not None:
                await broadcast_change(outcome.status_change)
            if outcome.event:
                log.info("usage.counter_event", tag_id=tag.id[:8], usage_event=outcome.event)
    except Exception as e:  # noqa: BLE001
        log.warning("mqtt.usage.broadcast_error", topic=group[0].topic, error=str(e))
    return set()


async def _finish(p: _Prep) -> Optional[Awaitable]:
    """5. Evaluate alert rules (with timeout) — live data only."""
    if p.replayed:
        if _DEBUG:
            log.debug("mqtt.alerts.skipped_replay", tag_id=p.tag_id[:8], ts=p.ts.isoformat())
        MQTT_MESSAGES.labels("ok").inc()
        return p.fin
    try:
        # Sprint 7: no session / query for the (many) tags without an active rule (rules.engine.rule_tags)
        has = rule_tags.cached(p.tenant_id, p.tag_id)
        if has is None:
            has = await asyncio.wait_for(rule_tags.has_rules(p.tenant_id, p.tag_id), timeout=5.0)
        if has:
            async with AsyncSessionLocal() as db:
                await asyncio.wait_for(evaluate(db=db, tag_id=p.tag_id, tenant_id=p.tenant_id, value=p.value),
                                       timeout=5.0)
        else:
            note_value(p.tenant_id, p.tag_id, p.value)
    except asyncio.TimeoutError:
        log.warning("mqtt.alerts.timeout", topic=p.topic)
    except Exception as e:
        if _cancelling():
            raise asyncio.CancelledError() from e
        log.warning("mqtt.alerts.error", topic=p.topic, error=str(e))
    MQTT_MESSAGES.labels("ok").inc()
    MQTT_LAG.observe(max((datetime.now(timezone.utc) - p.ts).total_seconds(), 0.0))
    return p.fin
