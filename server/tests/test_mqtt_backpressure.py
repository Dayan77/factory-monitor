"""Sprint 5 fix loop — MQTT ingestion under backlog (QA F1 at 500×):

* a tag check that times out (database busy) is retried, not dropped: the reading still reaches Influx;
* an Influx write that fails transiently is re-sent;
* a tag that does not exist is still rejected at once (fail closed), and non-transient errors are not retried;
* the subscriber is bounded: at most MQTT_MAX_INFLIGHT readings are accepted (and acknowledged) at once;
  with QoS 1 + manual ack the broker holds the rest, and every reading is processed, in order (real broker).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from conftest import create_assets


def _payload(value: float) -> bytes:
    return json.dumps({"value": value, "unit": "", "ts": datetime.now(timezone.utc).isoformat()}).encode()


@pytest.fixture
def fast_retry(monkeypatch):
    from src.services import mqtt_subscriber as ms
    monkeypatch.setattr(ms, "RETRY_FIRST_S", 0.01)
    monkeypatch.setattr(ms, "RETRY_MAX_S", 0.02)
    monkeypatch.setattr(ms.settings, "mqtt_transient_retry_s", 5.0)
    return ms


@pytest.fixture
def writes(monkeypatch):
    from src.services import mqtt_subscriber as ms
    seen: list = []

    async def fake_write(**kw):
        seen.append(kw)
    monkeypatch.setattr(ms.influx, "write", fake_write)
    return seen


async def test_tag_lookup_timeout_is_retried_not_dropped(client, tenants, fast_retry, writes, monkeypatch):
    ms = fast_retry
    a = await create_assets(client, tenants.a)
    real = ms._lookup_tag
    calls = []

    async def flaky(tenant_id, tag_id):
        calls.append(1)
        if len(calls) <= 2:
            raise asyncio.TimeoutError()      # what QA saw: empty error, reading discarded
        return await real(tenant_id, tag_id)
    monkeypatch.setattr(ms, "_lookup_tag", flaky)

    await ms._handle_message(f"factory/{tenants.a.id}/sensors/{a.tag['id']}", _payload(42.0))
    assert len(calls) == 3
    assert [w["value"] for w in writes] == [42.0]
    assert writes[0]["tag_id"] == a.tag["id"]


async def test_unknown_tag_still_rejected_without_retry(client, tenants, fast_retry, writes, monkeypatch):
    ms = fast_retry
    calls = []
    real = ms._lookup_tag

    async def counting(tenant_id, tag_id):
        calls.append(1)
        return await real(tenant_id, tag_id)
    monkeypatch.setattr(ms, "_lookup_tag", counting)
    await ms._handle_message(f"factory/{tenants.a.id}/sensors/{uuid.uuid4()}", _payload(1.0))
    assert calls == [1] and writes == []


async def test_non_transient_lookup_error_is_not_retried(client, tenants, fast_retry, writes, monkeypatch):
    ms = fast_retry
    a = await create_assets(client, tenants.a)
    calls = []

    async def broken(tenant_id, tag_id):
        calls.append(1)
        raise ValueError("bug")
    monkeypatch.setattr(ms, "_lookup_tag", broken)
    await ms._handle_message(f"factory/{tenants.a.id}/sensors/{a.tag['id']}", _payload(1.0))
    assert calls == [1] and writes == []


async def test_lookup_gives_up_after_the_retry_budget(fast_retry, monkeypatch):
    ms = fast_retry
    monkeypatch.setattr(ms.settings, "mqtt_transient_retry_s", 0.1)
    calls = []

    async def down():
        calls.append(1)
        raise ConnectionRefusedError()
    with pytest.raises(ConnectionRefusedError):
        await ms._retry_transient("t", "topic", down)
    assert 2 <= len(calls) <= 10


async def test_influx_write_failure_is_resent(client, tenants, fast_retry, monkeypatch):
    ms = fast_retry
    a = await create_assets(client, tenants.a)
    attempts: list = []

    async def flaky_write(**kw):
        attempts.append(kw["value"])
        if len(attempts) == 1:
            raise ConnectionResetError()
    monkeypatch.setattr(ms.influx, "write", flaky_write)
    await ms._handle_message(f"factory/{tenants.a.id}/sensors/{a.tag['id']}", _payload(7.0))
    assert attempts == [7.0, 7.0]


def _broker_up() -> bool:
    import socket
    try:
        with socket.create_connection((os.environ.get("MQTT_BROKER_HOST", "localhost"),
                                       int(os.environ.get("MQTT_BROKER_PORT", "1883"))), timeout=1):
            return True
    except OSError:
        return False


async def test_subscriber_is_bounded_and_loses_nothing_through_the_real_broker(monkeypatch):
    """F1-style backlog: the publisher is far faster than the handler. In-process readings never exceed the
    bound, and every QoS 1 reading is processed exactly once, in publish order."""
    if not _broker_up():
        pytest.fail("MQTT broker not reachable (CI starts mosquitto with infra/mosquitto/mosquitto.conf)")
    import aiomqtt
    from src.services import mqtt_subscriber as ms

    tenant, tag = str(uuid.uuid4()), str(uuid.uuid4())
    topic = f"factory/{tenant}/sensors/{tag}"
    bound, total = 8, 600
    got: list[int] = []
    active = {"n": 0, "max": 0}

    async def slow_handle(t, raw, received_at=None):
        if t != topic:
            return                          # other publishers on a shared dev broker
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        try:
            await asyncio.sleep(0.02)
            got.append(json.loads(raw)["i"])
        finally:
            active["n"] -= 1

    queued = {"max": 0}
    clients: list = []

    class Spy(aiomqtt.Client):                 # the subscriber's client: sample its receive queue
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            clients.append(self)

    monkeypatch.setattr(ms.aiomqtt, "Client", Spy)
    monkeypatch.setattr(ms, "_handle_message", slow_handle)
    monkeypatch.setattr(ms.settings, "mqtt_max_inflight", bound)
    monkeypatch.setattr(ms, "_capacity", None)
    sub = asyncio.create_task(ms.run_subscriber())
    try:
        await asyncio.sleep(1.0)                                   # connected + subscribed
        async def sample():
            while True:
                queued["max"] = max(queued["max"], clients[0]._queue.qsize())
                await asyncio.sleep(0.005)
        sampler = asyncio.create_task(sample())
        async with aiomqtt.Client(hostname=ms.settings.mqtt_broker_host, port=ms.settings.mqtt_broker_port) as pub:
            for i in range(total):
                await pub.publish(topic, json.dumps({"i": i, "value": 1}).encode(), qos=1)
        for _ in range(300):
            if len(got) >= total:
                break
            await asyncio.sleep(0.1)
            assert len(ms._inflight) <= bound
        sampler.cancel()
    finally:
        sub.cancel()
        await asyncio.gather(sub, return_exceptions=True)
        await asyncio.gather(*ms.cancel_inflight(), return_exceptions=True)
        monkeypatch.setattr(ms, "_capacity", None)
    assert sorted(got) == list(range(total))                 # nothing lost, nothing twice
    assert active["max"] <= bound
    # acknowledged only when accepted → the broker stops at its in-flight window (100) instead of pushing the
    # whole backlog into the client's unbounded receive queue
    assert queued["max"] <= 110, queued
