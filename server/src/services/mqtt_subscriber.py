"""
MQTT subscriber — consumes sensor readings published by the Edge Gateway.
Writes to InfluxDB, evaluates alert rules and broadcasts via WebSocket.
"""

import asyncio
import json
from base64 import b64decode
from typing import Optional

import aiomqtt
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import AsyncSessionLocal
from ..services.influx import influx
from ..rules.engine import evaluate
from ..websocket.manager import manager

log = structlog.get_logger()

TOPIC = "factory/+/sensors/#"   # factory/{tenant_id}/sensors/{tag_id}


async def run_subscriber() -> None:
    """Long-running task started on app lifespan."""
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

                async for message in client.messages:
                    await _handle_message(str(message.topic), message.payload)

        except aiomqtt.MqttError as e:
            log.warning("mqtt.disconnected", error=str(e))
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

        # 3. Broadcast to WebSocket clients (always, even if storage fails)
        await manager.broadcast(tenant_id, {
            "tag_id": tag_id,
            "value":  value,
            "unit":   unit,
            "timestamp": ts,
        })

        # 1. Persist to InfluxDB
        try:
            await influx.write(tenant_id=tenant_id, tag_id=tag_id, value=value, unit=unit)
        except Exception:
            log.warning("mqtt.influx.error", topic=topic)

        # 2. Evaluate alert rules
        try:
            async with AsyncSessionLocal() as db:
                await evaluate(db=db, tag_id=tag_id, tenant_id=tenant_id, value=value)
        except Exception:
            log.warning("mqtt.alerts.error", topic=topic)

    except Exception:
        log.exception("mqtt.message.error", topic=topic)
