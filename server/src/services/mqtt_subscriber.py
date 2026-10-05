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
                await client.subscribe(TOPIC)
                log.info("mqtt.subscribed", topic=TOPIC)

                async for message in client.messages:
                    # Fire-and-forget — never let a slow handler block message delivery
                    asyncio.create_task(
                        _tracked(_handle_message(str(message.topic), bytes(message.payload)))
                    )

        except aiomqtt.MqttError as e:
            log.warning("mqtt.disconnected", error=str(e))
            await asyncio.sleep(5)
        except Exception as e:
            log.error("mqtt.subscriber.error", error=str(e))
            await asyncio.sleep(5)


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
        return
    try:
        tenant_id = ensure_uuid(parts[1], "tenant_id")
        tag_id    = ensure_uuid(parts[3], "tag_id")
    except InvalidIdentifier:
        log.warning("mqtt.invalid_topic", topic=topic[:200])
        return
    entry = _tag_locks.get(tag_id)
    if entry is None:
        entry = _tag_locks[tag_id] = [asyncio.Lock(), 0]
    entry[1] += 1
    try:
        async with entry[0]:
            # Slot taken after the tag lock: readings queued behind their own tag hold no slot.
            async with _processing_slots():
                await _process_message(topic, tenant_id, tag_id, raw, received_at)
    finally:
        entry[1] -= 1
        if entry[1] == 0:          # nobody waiting: drop it (bounded memory with junk topics)
            _tag_locks.pop(tag_id, None)


async def _process_message(topic: str, tenant_id: str, tag_id: str, raw: bytes,
                           received_at: datetime) -> None:
    try:
        try:
            data = decode_payload(topic, raw, settings.encryption_key, settings.mqtt_require_encryption)
        except PayloadError as e:
            log.warning("mqtt.payload_rejected", topic=topic[:200], reason=str(e))
            return
        value = float(data["value"])
        unit  = data.get("unit")
        ts    = parse_source_ts(data.get("ts"), received_at)
        replayed = received_at - ts > replay_threshold()

        # 0. The tag must belong to the topic's tenant (fail closed if it can't be checked)
        try:
            async with AsyncSessionLocal() as db:
                tag = await asyncio.wait_for(topology_cache.tag_info(db, tenant_id, tag_id), timeout=5.0)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt.tag_lookup.error", topic=topic, error=str(e))
            return
        if tag is None:
            if tag_id not in _rejected_logged and len(_rejected_logged) < 10_000:
                _rejected_logged.add(tag_id)
                log.warning("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
            else:
                log.debug("mqtt.rejected_unknown_tag", tenant_id=tenant_id[:8], tag_id=tag_id[:8])
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

        # 2. Persist to InfluxDB at the source timestamp (timeout so it never blocks forever)
        try:
            await asyncio.wait_for(
                influx.write(tenant_id=tenant_id, tag_id=tag_id, value=value, unit=unit, ts=ts,
                             machine_id=tag.machine_id),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            log.warning("mqtt.influx.timeout", topic=topic)
        except Exception as e:
            log.warning("mqtt.influx.error", topic=topic, error=str(e))

        # 3. Machine status derived from data (last_seen_at + running)
        try:
            async with AsyncSessionLocal() as db:
                change = await asyncio.wait_for(
                    mark_seen(db=db, tenant_id=tenant_id, tag_id=tag_id,
                              now=received_at, seen_at=ts),
                    timeout=5.0,
                )
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

    except Exception as e:
        log.exception("mqtt.message.error", topic=topic, error=str(e))
