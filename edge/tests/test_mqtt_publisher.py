import asyncio
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone

import aiomqtt
import pytest

from edge.src.protocols.mqtt_publisher import MqttPublisher, format_ts
from edge.src.security.payload_crypto import PayloadCipher

KEY_HEX = bytes(range(32)).hex()
TENANT = "11111111-1111-1111-1111-111111111111"
TAG = "22222222-2222-2222-2222-222222222222"


def test_topic_format():
    assert MqttPublisher("h").build_topic(TENANT, TAG) == f"factory/{TENANT}/sensors/{TAG}"


def test_payload_v1_format():
    ts = datetime(2026, 3, 4, 10, 20, 30, 123456, tzinfo=timezone.utc)
    p = MqttPublisher("h").build_payload(245.3, "°C", ts)
    assert p == {"v": 1, "value": 245.3, "unit": "°C", "ts": "2026-03-04T10:20:30.123Z"}


def test_payload_defaults_unit_and_now_utc():
    p = MqttPublisher("h").build_payload(1.0)
    assert p["unit"] == ""
    ts = datetime.strptime(p["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert abs(datetime.now(timezone.utc) - ts) < timedelta(seconds=5)


def test_format_ts_converts_to_utc_and_assumes_utc_for_naive():
    brt = timezone(timedelta(hours=-3))
    assert format_ts(datetime(2026, 1, 1, 7, 0, 0, tzinfo=brt)) == "2026-01-01T10:00:00.000Z"
    assert format_ts(datetime(2026, 1, 1, 7, 0, 0)) == "2026-01-01T07:00:00.000Z"


def test_encode_plaintext_is_server_compatible_json():
    pub = MqttPublisher("h")
    assert not pub.encryption_enabled
    payload = pub.build_payload(3.5, "bar")
    raw = pub.encode("factory/t/sensors/x", payload)
    data = json.loads(raw)
    # fields the current server subscriber reads
    assert float(data["value"]) == 3.5 and data["unit"] == "bar" and "ts" in data


def test_encode_encrypted_envelope():
    pub = MqttPublisher("h", encryption_key=KEY_HEX)
    assert pub.encryption_enabled
    topic = f"factory/{TENANT}/sensors/{TAG}"
    payload = pub.build_payload(9.0, "A")
    env = json.loads(pub.encode(topic, payload))
    assert set(env) == {"v", "alg", "nonce", "ct"}
    assert PayloadCipher.from_setting(KEY_HEX).decrypt_json(topic, env) == payload


def test_bad_key_fails_fast():
    with pytest.raises(ValueError):
        MqttPublisher("h", encryption_key="short")


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, bytes, int]] = []
        self.fail = fail

    async def publish(self, topic, payload, qos=0):
        if self.fail:
            raise aiomqtt.MqttError("connection lost")
        self.sent.append((topic, payload, qos))


async def test_publish_when_disconnected_returns_false():
    pub = MqttPublisher("h")
    assert await pub.publish(TENANT, TAG, 1.0) is False


async def test_publish_uses_qos1_and_marks_disconnected_on_error():
    pub = MqttPublisher("h")
    pub._client, pub.is_connected = FakeClient(), True
    assert await pub.publish(TENANT, TAG, 1.0, "°C") is True
    topic, raw, qos = pub._client.sent[0]
    assert topic == f"factory/{TENANT}/sensors/{TAG}" and qos == 1
    assert json.loads(raw)["value"] == 1.0

    pub._client = FakeClient(fail=True)
    assert await pub.publish(TENANT, TAG, 2.0) is False
    assert pub.is_connected is False


async def test_connect_failure_is_reported_not_raised():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    pub = MqttPublisher("127.0.0.1", dead_port)
    await pub.connect()
    assert pub.is_connected is False


def _broker_up(host="localhost", port=1883) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _broker_up(), reason="no MQTT broker on localhost:1883")
@pytest.mark.parametrize("encrypted", [False, True])
async def test_end_to_end_with_real_broker(encrypted):
    """Publishes through a real Mosquitto and reads it back like the server does."""
    tenant = f"edge-test-{uuid.uuid4()}"
    key = KEY_HEX if encrypted else ""
    pub = MqttPublisher("localhost", 1883, encryption_key=key)
    topic = pub.build_topic(tenant, TAG)
    async with aiomqtt.Client("localhost", 1883) as sub:
        await sub.subscribe(f"factory/{tenant}/sensors/#", qos=1)
        await pub.connect()
        assert pub.is_connected
        try:
            assert await pub.publish(tenant, TAG, 42.5, "°C")
            msg = await asyncio.wait_for(anext(aiter(sub.messages)), 5)
        finally:
            await pub.disconnect()
    assert str(msg.topic) == topic
    data = json.loads(bytes(msg.payload))
    if encrypted:
        assert data["alg"] == "AES-256-GCM"
        data = PayloadCipher.from_setting(key).decrypt_json(str(msg.topic), data)
    assert data["value"] == 42.5 and data["unit"] == "°C" and data["v"] == 1


def test_tls_is_opt_in_and_a_bad_ca_fails_at_startup(tmp_path):
    """MQTT_TLS / MQTT_CA_CERT (production broker on 8883): verified context, never a silent plaintext."""
    import ssl
    assert MqttPublisher("h")._tls_context is None
    ctx = MqttPublisher("h", tls=True)._tls_context
    assert ctx is not None and ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    with pytest.raises(FileNotFoundError):
        MqttPublisher("h", tls=True, ca_cert=str(tmp_path / "missing.pem"))
