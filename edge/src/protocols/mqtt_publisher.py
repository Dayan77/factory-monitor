"""
MQTT Publisher — publishes sensor readings to the broker.

Topic format:  factory/{tenant_id}/sensors/{tag_id}
Payload v1:    {"v": 1, "value": 123.4, "unit": "°C", "ts": "2026-01-01T00:00:00.123Z"}
               (ts = UTC timestamp of the reading at the source, ISO-8601, millisecond precision)

When an encryption key is configured (ENCRYPTION_KEY), the payload above is wrapped in an
AES-256-GCM envelope — see edge/src/security/payload_crypto.py for the exact format.
"""

import asyncio
import json
import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiomqtt
import structlog

from ..security.payload_crypto import PayloadCipher

log = structlog.get_logger()

PAYLOAD_VERSION = 1


def tls_context(ca_cert: str = "", client_cert: str = "", client_key: str = "") -> ssl.SSLContext:
    """Client TLS context: system CAs, or the PEM bundle at `ca_cert`. Hostname checked; TLS ≥ 1.2.
    `client_cert` + `client_key` (PEM): mutual TLS, for brokers that authenticate gateways by certificate."""
    ctx = ssl.create_default_context(cafile=ca_cert or None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if client_cert:
        ctx.load_cert_chain(certfile=client_cert, keyfile=client_key or None)
    return ctx


class MqttTlsError(ValueError):
    pass


@dataclass(frozen=True)
class MqttTlsConfig:
    """TLS settings of the broker connection (env MQTT_TLS, MQTT_CA_CERT, MQTT_CLIENT_CERT, MQTT_CLIENT_KEY)."""
    enabled: bool = False
    ca_cert: str = ""
    client_cert: str = ""
    client_key: str = ""
    warnings: tuple[str, ...] = ()

    def context(self) -> Optional[ssl.SSLContext]:
        return tls_context(self.ca_cert, self.client_cert, self.client_key) if self.enabled else None


def _readable_file(path: str, what: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise MqttTlsError(f"{what}: arquivo não encontrado: {path}")
    if not os.access(p, os.R_OK):
        raise MqttTlsError(f"{what}: sem permissão de leitura: {path}")
    return str(p)


def parse_tls_settings(enabled, ca_cert: str = "", client_cert: str = "", client_key: str = "") -> MqttTlsConfig:
    """Validates the TLS settings at startup (never falls back to plaintext): files must exist; a client
    certificate needs its key; TLS options without MQTT_TLS=true are reported (likely a mistake)."""
    if isinstance(enabled, str):
        txt = enabled.strip().lower()
        if txt not in ("1", "true", "yes", "on", "0", "false", "no", "off", ""):
            raise MqttTlsError(f"MQTT_TLS inválido: {enabled!r} (use true/false)")
        enabled = txt in ("1", "true", "yes", "on")
    ca_cert, client_cert, client_key = (x.strip() for x in (ca_cert or "", client_cert or "", client_key or ""))
    if not enabled:
        given = [n for n, v in (("MQTT_CA_CERT", ca_cert), ("MQTT_CLIENT_CERT", client_cert),
                                ("MQTT_CLIENT_KEY", client_key)) if v]
        warn = (f"{', '.join(given)} definido(s) com MQTT_TLS=false: conexão SEM TLS",) if given else ()
        return MqttTlsConfig(False, warnings=warn)
    if bool(client_cert) != bool(client_key):
        raise MqttTlsError("MQTT_CLIENT_CERT e MQTT_CLIENT_KEY vão juntos (TLS mútuo)")
    return MqttTlsConfig(
        True,
        ca_cert=_readable_file(ca_cert, "MQTT_CA_CERT") if ca_cert else "",
        client_cert=_readable_file(client_cert, "MQTT_CLIENT_CERT") if client_cert else "",
        client_key=_readable_file(client_key, "MQTT_CLIENT_KEY") if client_key else "",
    )


def effective_port(port: Optional[int], tls: bool) -> int:
    """MQTT_BROKER_PORT, or the standard port (8883 with TLS, 1883 without)."""
    if port in (None, 0, ""):
        return 8883 if tls else 1883
    port = int(port)
    if not 1 <= port <= 65535:
        raise MqttTlsError(f"MQTT_BROKER_PORT inválida: {port}")
    return port


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
        tls: bool = False,
        ca_cert: str = "",
        tls_config: Optional[MqttTlsConfig] = None,
        max_inflight: int = 100,
        pending_warn: int = 1000,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        # TLS (broker of production on 8883): verified certificate + host name; a missing/invalid CA
        # file fails at startup (never silently falls back to plaintext).
        if tls_config is None:
            tls_config = MqttTlsConfig(bool(tls), ca_cert=ca_cert)
        self._tls_context: Optional[ssl.SSLContext] = tls_config.context()
        # Raises ValueError on a malformed key: fail at startup, never publish in clear by mistake.
        self._cipher: Optional[PayloadCipher] = PayloadCipher.from_setting(encryption_key)
        self._client: Optional[aiomqtt.Client] = None
        self.is_connected: bool = False
        # The gateway pipelines its publishes (edge/src/gateway.py `publish_window`): up to `max_inflight` QoS 1
        # messages on the wire unacknowledged (paho queues the rest); aiomqtt warns per call above
        # `pending_warn` pending publishes (its default of 10 logged >1 M warnings per QA run).
        self._max_inflight = max(1, int(max_inflight))
        self._pending_warn = max(10, int(pending_warn))

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
                tls_context=self._tls_context,
                max_inflight_messages=self._max_inflight,
            )
            self._client.pending_calls_threshold = self._pending_warn
            await self._client.__aenter__()
            self.is_connected = True
            log.info(
                "mqtt_publisher.connected",
                host=self._host, port=self._port, encrypted=self.encryption_enabled,
                tls=self._tls_context is not None,
            )
        except Exception as e:  # noqa: BLE001
            self._client = None
            self.is_connected = False
            log.warning("mqtt_publisher.connect_failed", error=str(e))

    async def disconnect(self) -> None:
        if self._client:
            # an unexpected disconnect leaves aiomqtt's `_disconnected` future with an exception nobody awaits
            # ("Future exception was never retrieved" on every uplink cut): retrieve it before dropping the client
            fut = getattr(self._client, "_disconnected", None)
            if isinstance(fut, asyncio.Future) and fut.done() and not fut.cancelled():
                fut.exception()
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
