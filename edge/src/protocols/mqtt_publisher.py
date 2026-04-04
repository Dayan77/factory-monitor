"""
MQTT Publisher — publishes sensor readings to the broker.

Topic format:  factory/{tenant_id}/sensors/{tag_id}
Payload:       {"value": 123.4, "unit": "°C", "ts": "2026-01-01T00:00:00Z"}
"""

import json
from datetime import datetime, timezone
from typing import Optional

import aiomqtt
import structlog

log = structlog.get_logger()


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        self._client: Optional[aiomqtt.Client] = None
        self.is_connected: bool = False

    async def connect(self) -> None:
        try:
            self._client = aiomqtt.Client(
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
            )
            await self._client.__aenter__()
            self.is_connected = True
            log.info("mqtt_publisher.connected", host=self._host, port=self._port)
        except Exception as e:
            self.is_connected = False
            log.warning("mqtt_publisher.connect_failed", error=str(e))

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
        self.is_connected = False

    def build_topic(self, tenant_id: str, tag_id: str) -> str:
        return f"factory/{tenant_id}/sensors/{tag_id}"

    def build_payload(
        self,
        value: float,
        unit: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> dict:
        return {
            "value": value,
            "unit": unit or "",
            "ts": (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    async def publish(
        self,
        tenant_id: str,
        tag_id: str,
        value: float,
        unit: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> bool:
        if not self.is_connected or self._client is None:
            return False
        topic = self.build_topic(tenant_id, tag_id)
        payload = self.build_payload(value, unit, ts)
        try:
            await self._client.publish(topic, json.dumps(payload).encode())
            log.debug("mqtt_publisher.sent", topic=topic, value=value)
            return True
        except Exception as e:
            self.is_connected = False
            log.warning("mqtt_publisher.send_failed", topic=topic, error=str(e))
            return False
