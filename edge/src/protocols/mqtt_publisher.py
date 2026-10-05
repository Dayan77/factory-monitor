"""
MQTT Publisher — publishes sensor readings to the broker.

Topic format:  factory/{tenant_id}/sensors/{tag_id}
Payload v1:    {"v": 1, "value": 123.4, "unit": "°C", "ts": "2026-01-01T00:00:00.123Z"}
               (ts = UTC timestamp of the reading at the source, ISO-8601, millisecond precision)

When an encryption key is configured (ENCRYPTION_KEY), the payload above is wrapped in an
AES-256-GCM envelope — see edge/src/security/payload_crypto.py for the exact format.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import aiomqtt
import structlog

from ..security.payload_crypto import PayloadCipher

log = structlog.get_logger()

PAYLOAD_VERSION = 1


def format_ts(ts: datetime) -> str:
    """UTC ISO-8601 with milliseconds and 'Z' (naive datetimes are assumed UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str = "",
        password: str = "",
        encryption_key: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        # Raises ValueError on a malformed key: fail at startup, never publish in clear by mistake.
        self._cipher: Optional[PayloadCipher] = PayloadCipher.from_setting(encryption_key)
        self._client: Optional[aiomqtt.Client] = None
        self.is_connected: bool = False

    @property
    def encryption_enabled(self) -> bool:
        return self._cipher is not None

    async def connect(self) -> None:
        await self.disconnect()
        try:
            self._client = aiomqtt.Client(
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
            )
            await self._client.__aenter__()
            self.is_connected = True
            log.info(
                "mqtt_publisher.connected",
                host=self._host, port=self._port, encrypted=self.encryption_enabled,
            )
        except Exception as e:  # noqa: BLE001
            self._client = None
            self.is_connected = False
            log.warning("mqtt_publisher.connect_failed", error=str(e))

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self._client = None
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
            "v": PAYLOAD_VERSION,
            "value": value,
            "unit": unit or "",
            "ts": format_ts(ts or datetime.now(timezone.utc)),
        }

    def encode(self, topic: str, payload: dict) -> bytes:
        """Serializes the payload to the wire format (encrypted envelope when enabled)."""
        if self._cipher is not None:
            body = self._cipher.encrypt_json(topic, payload)
        else:
            body = payload
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    async def publish_payload(self, topic: str, payload: dict) -> bool:
        """Publishes an already-built payload. Returns False (and marks disconnected) on failure."""
        if not self.is_connected or self._client is None:
            return False
        try:
            await self._client.publish(topic, self.encode(topic, payload), qos=1)
            log.debug("mqtt_publisher.sent", topic=topic)
            return True
        except Exception as e:  # noqa: BLE001
            self.is_connected = False
            log.warning("mqtt_publisher.send_failed", topic=topic, error=str(e))
            return False

    async def publish(
        self,
        tenant_id: str,
        tag_id: str,
        value: float,
        unit: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> bool:
        topic = self.build_topic(tenant_id, tag_id)
        return await self.publish_payload(topic, self.build_payload(value, unit, ts))
