"""
MQTT subscriber — consumes sensor readings published by the Edge Gateway.
Writes to InfluxDB, evaluates alert rules and broadcasts via WebSocket.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiomqtt
import structlog

from ..config import settings
from ..db import AsyncSessionLocal
from ..services.influx import influx
from ..services.machine_status import broadcast_change, mark_seen
from ..services.validation import InvalidIdentifier, ensure_uuid
from ..rules.engine import evaluate
from ..websocket.manager import manager

log = structlog.get_logger()

TOPIC = "factory/+/sensors/#"   # factory/{tenant_id}/sensors/{tag_id}

def parse_source_ts(raw: Any, received_at: datetime) -> datetime:
    """Payload `ts` (ISO-8601, `Z` or offset; naive = UTC) → aware UTC datetime.

    Missing/invalid → received_at. A timestamp in the future (gateway clock ahead
    of the server) is clamped to received_at: we never store the future.
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
    return ts


def replay_threshold() -> timedelta:
    """Readings older than this (vs. receive time) are edge-backlog replays, not live data."""
    return timedelta(seconds=2 * settings.machine_offline_timeout_s)


async def run_subscriber() -> None:
    """Long-running task started on app lifespan. Never exits."""
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
                        _handle_message(str(message.topic), bytes(message.payload))
                    )

        except aiomqtt.MqttError as e:
            log.warning("mqtt.disconnected", error=str(e))
            await asyncio.sleep(5)
        except Exception as e:
            log.error("mqtt.subscriber.error", error=str(e))
            await asyncio.sleep(5)


async def _handle_message(topic: str, raw: bytes, received_at: Optional[datetime] = None) -> None:
    """
    Expected topic:  factory/{tenant_id}/sensors/{tag_id}
    Expected payload: {"value": 245.3, "unit": "°C", "ts": "2026-04-04T10:00:00Z"}

    `ts` is the source timestamp set by the edge (also for readings replayed from
    its SQLite backlog). It is what goes to Influx and last_seen_at. Readings
    older than replay_threshold() are history: they are stored, but not pushed to
    live WebSocket clients, they don't flip the machine to 'running' and alert
    rules are not evaluated on them (no alert for an excursion that ended hours ago).
    """
    received_at = received_at or datetime.now(timezone.utc)
    try:
        parts = topic.split("/")
        if len(parts) < 4:
            return
        try:
            tenant_id = ensure_uuid(parts[1], "tenant_id")
            tag_id    = ensure_uuid(parts[3], "tag_id")
        except InvalidIdentifier:
            log.warning("mqtt.invalid_topic", topic=topic[:200])
            return

        data  = json.loads(raw)
        value = float(data["value"])
        unit  = data.get("unit")
        ts    = parse_source_ts(data.get("ts"), received_at)
        replayed = received_at - ts > replay_threshold()

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
                influx.write(tenant_id=tenant_id, tag_id=tag_id, value=value, unit=unit, ts=ts),
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

        # 4. Evaluate alert rules (with timeout) — live data only
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
