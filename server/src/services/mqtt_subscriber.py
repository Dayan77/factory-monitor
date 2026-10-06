"""
MQTT subscriber — consumes sensor readings published by the Edge Gateway.
Writes to InfluxDB, evaluates alert rules and broadcasts via WebSocket.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiomqtt
import structlog
from prometheus_client import Gauge

from ..config import settings
from ..db import AsyncSessionLocal
from ..services.influx import influx
from ..services.payload_crypto import PayloadError, decode_payload, key_error
from ..services.machine_status import broadcast_change, mark_seen
from ..services.oee.detector import note_late
from ..services.predictive.engine import late_threshold as predictive_late_threshold
from ..services.predictive.engine import note_late as predictive_note_late
from ..services.metrics import MQTT_LAG, MQTT_MESSAGES
from ..services.validation import InvalidIdentifier, ensure_uuid
from ..services.usage.broadcaster import usage_broadcaster
from ..services.usage.processor import process_reading
from ..services.usage.cache import topology_cache
from ..models.tag import COUNTER_ROLES, SemanticRole
from ..rules.engine import evaluate
from ..websocket.manager import manager

log = structlog.get_logger()

TOPIC = "factory/+/sensors/#"   # factory/{tenant_id}/sensors/{tag_id}

USAGE_ROLES = frozenset(COUNTER_ROLES | {SemanticRole.run_state, SemanticRole.fault_state})

# Rejected (unknown / other-tenant) tags are logged once per tag at warning level, then at debug.
_rejected_logged: set[str] = set()

# One lock per tag: readings of the same tag are processed one at a time, in arrival order
# (asyncio.Lock is FIFO and is acquired before the first await of the handler).
_tag_locks: dict[str, list] = {}      # tag_id -> [Lock, in-flight count]

# Backlog visibility: messages received but not processed yet. If the server cannot keep up with
# what the gateways publish this grows without bound (and live data lags) — warn and export it.
INFLIGHT = Gauge("fm_mqtt_inflight_messages", "MQTT readings received and not yet processed")
BACKLOG_WARN = 1000
_backlog = {"n": 0, "warned_at": 0.0}
_slots: Optional[asyncio.Semaphore] = None
# In-flight reading tasks (strong references: asyncio keeps only weak ones) — cancelled on shutdown.
_inflight: set[asyncio.Task] = set()


def cancel_inflight() -> list[asyncio.Task]:
    """Cancels every reading still being processed; returns them so the caller can wait (bounded)."""
    tasks = [t for t in _inflight if not t.done()]
    for t in tasks:
        t.cancel()
    return tasks


_capacity: Optional[asyncio.Semaphore] = None


def _inflight_capacity() -> asyncio.Semaphore:
    global _capacity
    if _capacity is None:
        _capacity = asyncio.Semaphore(max(1, settings.mqtt_max_inflight))
    return _capacity


def _reading_done(task: asyncio.Task) -> None:
    _inflight.discard(task)
    _inflight_capacity().release()


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


def _ack(client: Any, message: Any) -> None:
    qos = getattr(message, "qos", 0) or 0
    if qos > 0:
        client._client.ack(message.mid, qos)   # noqa: SLF001 — in receive order (this loop is sequential)


def _processing_slots() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(max(1, settings.mqtt_max_concurrency))
    return _slots


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


async def run_subscriber() -> None:
    """Long-running task started on app lifespan. Never exits."""
    err = key_error(settings.encryption_key)
    if err:
        log.error("mqtt.encryption_key_invalid", error=err)
    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_broker_host,
                port=settings.mqtt_broker_port,
                username=settings.mqtt_username or None,
                password=settings.mqtt_password or None,
            ) as client:
                log.info("mqtt.connected", broker=settings.mqtt_broker_host)
                manual_ack = _enable_manual_ack(client)
                # QoS 1 + acknowledge-on-accept = flow control: the broker sends at most its
                # max_inflight_messages unacknowledged readings and queues the rest (bounded, on its side).
                await client.subscribe(TOPIC, qos=1)
                log.info("mqtt.subscribed", topic=TOPIC)
                # a cancellation delivered while connecting can be swallowed by the client library:
                # the task is still marked as cancelling, so stop instead of serving forever
                if _cancelling():
                    raise asyncio.CancelledError()

                async for message in client.messages:
                    if _cancelling():
                        raise asyncio.CancelledError()
                    # Backpressure: wait for room before accepting (and acknowledging) the reading. While
                    # we wait, nothing more is acknowledged and the broker stops delivering.
                    capacity = _inflight_capacity()
                    await capacity.acquire()
                    try:
                        if manual_ack:
                            _ack(client, message)
                        # Processed concurrently (per-tag order kept by _handle_message's tag lock).
                        # Kept in _inflight so shutdown can cancel them (cancel_inflight()).
                        task = asyncio.create_task(
                            _tracked(_handle_message(str(message.topic), bytes(message.payload))),
                            name="mqtt_reading",
                        )
                    except BaseException:
                        capacity.release()
                        raise
                    _inflight.add(task)
                    task.add_done_callback(_reading_done)

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


def _cancelling() -> bool:
    task = asyncio.current_task()
    return bool(task is not None and task.cancelling())


async def _tracked(coro) -> None:
    _backlog["n"] += 1
    INFLIGHT.set(_backlog["n"])
    now = asyncio.get_running_loop().time()
    if _backlog["n"] >= BACKLOG_WARN and now - _backlog["warned_at"] > 30:
        _backlog["warned_at"] = now
        log.warning("mqtt.backlog", inflight=_backlog["n"],
                    hint="o servidor não está acompanhando a taxa publicada pelos gateways")
    try:
        await coro
    finally:
        _backlog["n"] -= 1
        INFLIGHT.set(_backlog["n"])


async def _handle_message(topic: str, raw: bytes, received_at: Optional[datetime] = None) -> None:
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
    """
    received_at = received_at or datetime.now(timezone.utc)
    parts = topic.split("/")
    if len(parts) < 4:
        MQTT_MESSAGES.labels("invalid").inc()
        return
    try:
        tenant_id = ensure_uuid(parts[1], "tenant_id")
        tag_id    = ensure_uuid(parts[3], "tag_id")
    except InvalidIdentifier:
        log.warning("mqtt.invalid_topic", topic=topic[:200])
        MQTT_MESSAGES.labels("invalid").inc()
        return
    entry = _tag_locks.get(tag_id)
    if entry is None:
        entry = _tag_locks[tag_id] = [asyncio.Lock(), 0]
    entry[1] += 1
    pending: list = []
    try:
        async with entry[0]:
            # Slot taken after the tag lock: readings queued behind their own tag hold no slot.
            async with _processing_slots():
                await _process_message(topic, tenant_id, tag_id, raw, received_at, pending)
    finally:
        entry[1] -= 1
        if entry[1] == 0:          # nobody waiting: drop it (bounded memory with junk topics)
            _tag_locks.pop(tag_id, None)
    # The Influx write is awaited outside the tag lock: the next reading of the same tag does not wait for the
    # HTTP round trip, and the writes queued meanwhile go to Influx together (influx.py coalescing).
    for fin in pending:
        await fin


async def _lookup_tag(tenant_id: str, tag_id: str):
    async with AsyncSessionLocal() as db:
        return await asyncio.wait_for(topology_cache.tag_info(db, tenant_id, tag_id), timeout=5.0)


def _is_transient(e: BaseException) -> bool:
    """Failures that say nothing about the reading itself: retrying later can succeed."""
    if isinstance(e, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True
    try:
        from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
        if isinstance(e, (DBAPIError, PoolTimeout)):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        import aiohttp
        if isinstance(e, aiohttp.ClientError):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        from influxdb_client.client.exceptions import InfluxDBError
        if isinstance(e, InfluxDBError):
            status = getattr(getattr(e, "response", None), "status", None)
            return status is None or status >= 500 or status == 429
    except ImportError:  # pragma: no cover
        pass
    return False


RETRY_FIRST_S = 0.5
RETRY_MAX_S = 15.0


async def _retry_transient(what: str, topic: str, fn, *args):
    """Calls fn(*args); on a transient failure retries with exponential backoff for up to
    settings.mqtt_transient_retry_s, then re-raises. Non-transient errors are raised at once. The reading
    keeps its in-flight slot meanwhile, so a persistent outage turns into backpressure, not a growing heap."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.mqtt_transient_retry_s
    delay = RETRY_FIRST_S
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn(*args)
        except Exception as e:  # noqa: BLE001
            if not _is_transient(e) or loop.time() + delay > deadline:
                raise
            log.warning(f"{what}.retry", topic=topic, attempt=attempt, error=str(e),
                        error_type=type(e).__name__, retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)


async def _finish_write(topic: str, tenant_id: str, tag, ts: datetime, received_at: datetime, write,
                        rewrite=None) -> None:
    """Awaits the Influx write; a transient failure is re-sent with rewrite() (idempotent in Influx: same
    series + timestamp overwrites) instead of losing the reading."""
    first = [write]

    async def attempt():
        w = first.pop() if first else rewrite()
        return await asyncio.wait_for(w, timeout=10.0)

    try:
        if rewrite is None:
            await attempt()
        else:
            await _retry_transient("mqtt.influx", topic, attempt)
    except asyncio.TimeoutError:
        log.error("mqtt.influx.timeout", topic=topic, reading_lost=True)
    except Exception as e:
        log.error("mqtt.influx.error", topic=topic, error=str(e), error_type=type(e).__name__, reading_lost=True)
    else:
        # a detector signal written well after its own timestamp (gateway buffer flush, replay,
        # backlog, write retries): the downtime detector may already have passed it → rewind (detector.py)
        written_at = max(received_at, datetime.now(timezone.utc))
        if (tag.machine_id and tag.role in USAGE_ROLES
                and (written_at - ts).total_seconds() > settings.downtime_settle_s):
            note_late(tenant_id, tag.machine_id, ts)
        # …and the predictive engine may already have stored its time window as invalid → re-extract it
        if tag.machine_id and written_at - ts > predictive_late_threshold():
            predictive_note_late(tenant_id, tag.machine_id, ts)


# mark_seen throttle: machine_id -> (monotonic time of the last call, latest seen_at sent). Within
# SEEN_THROTTLE_S of a call, a reading of the same machine that would not move last_seen_at by the
# write interval (machine_last_seen_write_interval_s) is a database no-op — skip the round trip.
SEEN_THROTTLE_S = 1.0
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
    return (asyncio.get_running_loop().time() - called < SEEN_THROTTLE_S
            and (seen_at - last).total_seconds() < settings.machine_last_seen_write_interval_s)


def _seen_called(machine_id: Optional[str], seen_at: datetime, received_at: datetime) -> None:
    if not machine_id:
        return
    if len(_seen_cache) > 50_000:
        _seen_cache.clear()
    prev = _seen_cache.get(machine_id)
    _seen_cache[machine_id] = (asyncio.get_running_loop().time(), max(seen_at, prev[1]) if prev else seen_at,
                               _is_fresh(seen_at, received_at))


async def _process_message(topic: str, tenant_id: str, tag_id: str, raw: bytes,
                           received_at: datetime, pending: Optional[list] = None) -> None:
    try:
        try:
            data = decode_payload(topic, raw, settings.encryption_key, settings.mqtt_require_encryption)
        except PayloadError as e:
            log.warning("mqtt.payload_rejected", topic=topic[:200], reason=str(e))
            MQTT_MESSAGES.labels("invalid").inc()
            return
        try:
            value = float(data["value"])
        except (KeyError, TypeError, ValueError):
            log.warning("mqtt.payload_rejected", topic=topic[:200], reason="value")
            MQTT_MESSAGES.labels("invalid").inc()
            return
        unit  = data.get("unit")
        ts    = parse_source_ts(data.get("ts"), received_at)
        replayed = received_at - ts > replay_threshold()

        # 0. The tag must belong to the topic's tenant (fail closed if it can't be checked)
        #    A tag that does not exist is rejected; a check that could not be made (timeout, database
        #    unavailable) is retried — dropping it lost valid readings under backlog (Sprint 5 QA).
        try:
            tag = await _retry_transient("mqtt.tag_lookup", topic, _lookup_tag, tenant_id, tag_id)
        except Exception as e:  # noqa: BLE001
            log.error("mqtt.tag_lookup.error", topic=topic, error=str(e), error_type=type(e).__name__,
                      reading_lost=True)
            MQTT_MESSAGES.labels("error").inc()
            return
        if tag is None:
            MQTT_MESSAGES.labels("rejected").inc()
            if tag_id not in _rejected_logged and len(_rejected_logged) < 10_000:
                _rejected_logged.add(tag_id)
                log.warning("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
            else:
                log.debug("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
            return

        if tag.gateway_revoked:
            # the gateway that reads this tag was revoked (stolen / decommissioned): its MQTT credential may
            # still be on the broker until the operator removes it — the server no longer accepts its data
            MQTT_MESSAGES.labels("rejected").inc()
            key = f"revoked:{tag_id}"
            if key not in _rejected_logged and len(_rejected_logged) < 10_000:
                _rejected_logged.add(key)
                log.warning("mqtt.rejected_revoked_gateway", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
            return

        log.debug("mqtt.message", tenant_id=tenant_id[:8], tag_id=tag_id[:8], value=value,
                  replayed=replayed)

        # 1. Broadcast live readings to WebSocket clients immediately
        if not replayed:
            await manager.broadcast(tenant_id, {
                "type":      "sensor_reading",
                "tag_id":    tag_id,
                "value":     value,
                "unit":      unit,
                "timestamp": ts.isoformat(),
            })

        # 2. Persist to InfluxDB at the source timestamp (timeout so it never blocks forever). Started now,
        #    awaited after the tag lock is released (_handle_message) — or here when called directly.
        def start_write():
            return asyncio.ensure_future(influx.write(tenant_id=tenant_id, tag_id=tag_id, value=value, unit=unit,
                                                      ts=ts, machine_id=tag.machine_id))
        fin = _finish_write(topic, tenant_id, tag, ts, received_at, start_write(), start_write)
        if pending is not None:
            pending.append(fin)
        else:
            await fin

        # 3. Machine status derived from data (last_seen_at + running)
        try:
            if not _seen_is_noop(tag.machine_id, ts, received_at):
                async with AsyncSessionLocal() as db:
                    change = await asyncio.wait_for(
                        mark_seen(db=db, tenant_id=tenant_id, tag_id=tag_id,
                                  now=received_at, seen_at=ts),
                        timeout=5.0,
                    )
                _seen_called(tag.machine_id, ts, received_at)
                if change is not None:
                    await broadcast_change(change)
        except asyncio.TimeoutError:
            log.warning("mqtt.machine_status.timeout", topic=topic)
        except Exception as e:
            log.warning("mqtt.machine_status.error", topic=topic, error=str(e))

        # 4. Usage: counters / run_state / fault_state (domain.md §3.3–3.4, §1.1)
        if tag.role in USAGE_ROLES:
            try:
                async with AsyncSessionLocal() as db:
                    outcome = await asyncio.wait_for(
                        process_reading(db, tag, value, ts, fresh=not replayed),
                        timeout=settings.usage_process_timeout_s)
                for u in outcome.updates:
                    usage_broadcaster.add(u)
                if outcome.status_change is not None:
                    await broadcast_change(outcome.status_change)
                if outcome.event:
                    log.info("usage.counter_event", tag_id=tag_id[:8], usage_event=outcome.event)
            except asyncio.TimeoutError:
                log.warning("mqtt.usage.timeout", topic=topic)
            except Exception as e:
                log.warning("mqtt.usage.error", topic=topic, error=str(e))

        # 5. Evaluate alert rules (with timeout) — live data only
        if replayed:
            log.debug("mqtt.alerts.skipped_replay", tag_id=tag_id[:8], ts=ts.isoformat())
            MQTT_MESSAGES.labels("ok").inc()
            return
        try:
            async with AsyncSessionLocal() as db:
                await asyncio.wait_for(
                    evaluate(db=db, tag_id=tag_id, tenant_id=tenant_id, value=value),
                    timeout=5.0,
                )
        except asyncio.TimeoutError:
            log.warning("mqtt.alerts.timeout", topic=topic)
        except Exception as e:
            log.warning("mqtt.alerts.error", topic=topic, error=str(e))
        MQTT_MESSAGES.labels("ok").inc()
        MQTT_LAG.observe(max((datetime.now(timezone.utc) - ts).total_seconds(), 0.0))

    except Exception as e:
        MQTT_MESSAGES.labels("error").inc()
        log.exception("mqtt.message.error", topic=topic, error=str(e))
