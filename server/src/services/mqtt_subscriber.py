"""
MQTT subscriber — consumes sensor readings published by the Edge Gateway.
Writes to InfluxDB, evaluates alert rules and broadcasts via WebSocket.
"""

import asyncio
import json

import aiomqtt
import structlog

from ..config import settings
from ..db import AsyncSessionLocal
from ..services.influx import influx
from ..rules.engine import evaluate
from ..websocket.manager import manager

log = structlog.get_logger()

TOPIC = "factory/+/sensors/#"   # factory/{tenant_id}/sensors/{tag_id}


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


async def _handle_message(topic: str, raw: bytes) -> None:
    """
    Expected topic:  factory/{tenant_id}/sensors/{tag_id}
    Expected payload: {"value": 245.3, "unit": "°C", "ts": "2026-04-04T10:00:00Z"}
    """
    try:
        parts = topic.split("/")
        if len(parts) < 4:
            return
        tenant_id = parts[1]
        tag_id    = parts[3]

        data  = json.loads(raw)
        value = float(data["value"])
        unit  = data.get("unit")
        ts    = data.get("ts")

        log.debug("mqtt.message", tenant_id=tenant_id[:8], tag_id=tag_id[:8], value=value)

        # 1. Broadcast to WebSocket clients immediately
        await manager.broadcast(tenant_id, {
            "tag_id":    tag_id,
            "value":     value,
            "unit":      unit,
            "timestamp": ts,
        })

        # 2. Persist to InfluxDB (with timeout so it never blocks forever)
        try:
            await asyncio.wait_for(
                influx.write(tenant_id=tenant_id, tag_id=tag_id, value=value, unit=unit),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            log.warning("mqtt.influx.timeout", topic=topic)
        except Exception as e:
            log.warning("mqtt.influx.error", topic=topic, error=str(e))

        # 3. Evaluate alert rules (with timeout)
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
