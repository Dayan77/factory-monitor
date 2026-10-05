"""Gateway pipeline: deadband → publish, offline buffering and ordered re-send."""

import json

import pytest

from edge.src import gateway as gw_mod
from edge.src.protocols.modbus import ModbusTagConfig

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.online = True

    async def publish(self, topic, payload, qos=0):
        if not self.online:
            raise ConnectionError("broker down")
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", TENANT)
    monkeypatch.setattr(gw_mod.settings, "deadband_pct", 1.0)
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    client = FakeClient()
    g.publisher._client, g.publisher.is_connected = client, True

    async def fake_connect():
        g.publisher.is_connected = client.online
    g.publisher.connect = fake_connect  # no real broker
    g.client = client
    yield g
    await g.buffer.close()


async def test_reading_published_with_unit_and_deadband(gateway):
    gateway.register_modbus_tags([ModbusTagConfig("temp", 0, "holding", unit="°C")])
    await gateway._handle_reading("temp", 100.0)
    await gateway._handle_reading("temp", 100.5)   # < 1 % → dropped
    await gateway._handle_reading("temp", 102.0)
    sent = gateway.client.sent
    assert [p["value"] for _, p in sent] == [100.0, 102.0]
    assert sent[0][0] == f"factory/{TENANT}/sensors/temp"
    assert sent[0][1]["unit"] == "°C" and sent[0][1]["v"] == 1


async def test_digital_and_counter_tags_bypass_deadband(gateway):
    gateway.register_modbus_tags([
        ModbusTagConfig("run", 0, "coil"),
        ModbusTagConfig("parts", 1, "holding", data_type="uint32", no_deadband=True),
    ])
    for v in (1000.0, 1001.0, 1002.0):
        await gateway._handle_reading("parts", v)
    await gateway._handle_reading("run", 0.0)
    await gateway._handle_reading("run", 1.0)
    vals = [(t.rsplit("/", 1)[1], p["value"]) for t, p in gateway.client.sent]
    assert vals == [("parts", 1000.0), ("parts", 1001.0), ("parts", 1002.0), ("run", 0.0), ("run", 1.0)]


async def test_offline_readings_buffered_then_flushed_in_order(gateway):
    gateway.client.online = False
    for v in (10.0, 20.0, 30.0):
        await gateway._handle_reading("p", v)
    assert gateway.publisher.is_connected is False
    assert await gateway.buffer.count() == 3
    # still offline: flush keeps everything
    assert await gateway.flush_once() == 0
    assert await gateway.buffer.count() == 3

    gateway.client.online = True
    await gateway._handle_reading("p", 40.0)  # backlog exists → goes behind it
    assert await gateway.flush_once() == 4
    assert [p["value"] for _, p in gateway.client.sent] == [10.0, 20.0, 30.0, 40.0]
    assert await gateway.buffer.count() == 0


async def test_buffered_payload_keeps_source_timestamp(gateway):
    gateway.client.online = False
    await gateway._handle_reading("p", 1.0)
    (_, _, stored), = await gateway.buffer.peek()
    gateway.client.online = True
    await gateway.flush_once()
    assert gateway.client.sent[0][1]["ts"] == stored["ts"]


async def test_encrypted_gateway_buffers_plaintext_and_encrypts_on_send(tmp_path, monkeypatch):
    key = bytes(range(32)).hex()
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", TENANT)
    monkeypatch.setattr(gw_mod.settings, "encryption_key", key)
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    client = FakeClient()
    g.publisher._client, g.publisher.is_connected = client, True
    await g._handle_reading("x", 5.0)
    topic, env = client.sent[0]
    assert env["alg"] == "AES-256-GCM"
    from edge.src.security.payload_crypto import PayloadCipher
    assert PayloadCipher.from_setting(key).decrypt_json(topic, env)["value"] == 5.0
    await g.buffer.close()


def test_modbus_tags_from_settings_dicts_are_converted():
    cfg = [ModbusTagConfig.from_dict(d) for d in [
        {"tag_id": "a", "address": 0, "register_type": "holding", "data_type": "float32"},
    ]]
    assert cfg[0].register_count == 2


# ── timestamps: one clock per tag, monotonic (server drops out-of-order readings) ──
from datetime import datetime, timedelta, timezone  # noqa: E402


def _ts(p: dict) -> datetime:
    return datetime.strptime(p["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class _Connected:
    connected = True


def _refreshable(gateway):
    """Tag "run" refreshed like an OPC UA tag; heartbeat re-publish on every refresh."""
    gateway.register_modbus_tags([ModbusTagConfig("run", 0, "coil", no_deadband=True)])
    gateway._refresh_tags["run"] = _Connected()
    gateway.filter._max_silence = 1e-9


async def test_plc_clock_180s_behind_uses_gateway_time_and_warns_once(gateway, monkeypatch):
    """A PLC clock 3 min behind must not make every live reading look like a replay (> 120 s old)
    nor fall behind the refresh timestamps."""
    _refreshable(gateway)
    warnings = []
    monkeypatch.setattr(gw_mod.log, "warning", lambda ev, **kw: warnings.append(ev))
    offset = timedelta(seconds=-180)
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    await gateway._handle_reading("run", 1.0, datetime.now(timezone.utc) + offset)
    await gateway.refresh_once()
    await gateway._handle_reading("run", 0.0, datetime.now(timezone.utc) + offset)
    await gateway._handle_reading("run", 1.0, datetime.now(timezone.utc) + offset)
    sent = [p for _, p in gateway.client.sent]
    assert [p["value"] for p in sent] == [1.0, 1.0, 0.0, 1.0]
    stamps = [_ts(p) for p in sent]
    assert all(s >= before for s in stamps), "skewed source ts must be replaced by gateway time"
    assert all(b > a for a, b in zip(stamps, stamps[1:])), "timestamps strictly increasing"
    assert warnings.count("edge_gateway.clock_skew") == 1


async def test_change_racing_a_refresh_is_not_published_behind_it(gateway):
    """Refresh stamps run_state=1 at gateway time T; a stop that happened at PLC time T-0.3 s
    arrives afterwards. It must still be published after T (else the server drops it)."""
    _refreshable(gateway)
    t0 = datetime.now(timezone.utc)
    await gateway._handle_reading("run", 1.0, t0 - timedelta(seconds=2))
    await gateway.refresh_once()                      # stamped ~now
    await gateway._handle_reading("run", 0.0, datetime.now(timezone.utc) - timedelta(milliseconds=300))
    sent = [p for _, p in gateway.client.sent]
    assert [p["value"] for p in sent] == [1.0, 1.0, 0.0]
    stamps = [_ts(p) for p in sent]
    assert stamps[0] < stamps[1] < stamps[2]


async def test_small_skew_keeps_source_timestamp(gateway):
    gateway.register_modbus_tags([ModbusTagConfig("run", 0, "coil", no_deadband=True)])
    src = datetime.now(timezone.utc) - timedelta(seconds=1.5)
    await gateway._handle_reading("run", 1.0, src)
    assert _ts(gateway.client.sent[0][1]) == src.replace(microsecond=src.microsecond // 1000 * 1000)
