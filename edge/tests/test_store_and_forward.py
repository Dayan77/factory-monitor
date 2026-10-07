"""Store-and-forward under load (Sprint 5b QA (d)): after an uplink outage at ~230 readings/s the gateway never
flushed (COUNT(*) + INSERT + COMMIT per reading starved the flush on the single SQLite queue), memory grew to 4 GB
(one task per OPC UA notification) and SIGTERM was ignored. The sender now batches SQLite writes, bounds memory
(outbox + OPC UA queue, backpressure), flushes the backlog first and keeps FIFO order."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from edge.src import gateway as gw_mod
from edge.src.buffer.local_buffer import LocalBuffer
from edge.src.protocols.opcua import OpcUaDriver, _SubscriptionHandler

TENANT = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class LinkClient:
    """Fake aiomqtt client: a round trip per publish (PUBACK), and an uplink that can be cut."""

    def __init__(self, rtt_s: float = 0.0) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.online = True
        self.rtt_s = rtt_s
        self.inflight = 0
        self.max_inflight = 0

    async def publish(self, topic, payload, qos=0):
        if not self.online:
            raise ConnectionError("uplink down")
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        body = json.loads(payload)
        try:
            await asyncio.sleep(self.rtt_s)
            if self.rtt_s and not self.online:               # cut while waiting for the PUBACK
                raise ConnectionError("uplink down")
            self.sent.append((topic, body))
        finally:
            self.inflight -= 1


@pytest.fixture
def make_gateway(tmp_path, monkeypatch):
    made = []

    async def _make(rtt_s=0.0, **cfg):
        monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / f"buf{len(made)}.db"))
        monkeypatch.setattr(gw_mod.settings, "tenant_id", TENANT)
        monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
        for k, v in cfg.items():
            monkeypatch.setattr(gw_mod.settings, k, v)
        g = gw_mod.EdgeGateway()
        await g.buffer.initialize()
        client = LinkClient(rtt_s)
        g.publisher._client, g.publisher.is_connected = client, True

        async def fake_connect():
            g.publisher.is_connected = client.online
        g.publisher.connect = fake_connect
        g.client = client
        made.append(g)
        return g

    return _make


async def _close(g):
    await g._stop_sender()
    await g.buffer.close()


def _values(g) -> list[int]:
    return [int(p["value"]) for _, p in g.client.sent]


def _first_occurrences(vals: list[int]) -> list[int]:
    seen, out = set(), []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def _wait(pred, timeout=30.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        assert loop.time() < end, "timeout"
        await asyncio.sleep(0.01)


async def test_outage_under_load_recovers_in_order_with_batched_sqlite_writes(make_gateway):
    """Readings keep arriving fast through outage AND reconnect: the backlog is flushed while new readings arrive,
    every reading reaches the broker exactly in production order, and SQLite sees few, batched transactions."""
    g = await make_gateway(rtt_s=0.002, outbox_max=2000, publish_window=50, spill_batch=200, spill_max_age_s=0.05)
    stores: list[int] = []
    real_store_many = g.buffer.store_many

    async def counting_store_many(items):
        stores.append(len(items))
        return await real_store_many(items)
    g.buffer.store_many = counting_store_many
    g.start_sender()
    max_outbox = 0

    async def produce(start, n):
        nonlocal max_outbox
        for i in range(start, start + n):
            await g._publish("p", float(i), T0 + timedelta(milliseconds=i))
            max_outbox = max(max_outbox, len(g._outbox))
            if i % 20 == 0:
                await asyncio.sleep(0)

    await produce(0, 1000)                                   # online
    g.client.online = False                                  # uplink cut
    await produce(1000, 3000)
    await _wait(lambda: not g._outbox)
    assert g.publisher.is_connected is False and g.buffer.rows >= 3000 - 50
    g.client.online = True                                   # uplink back: reconnect, keep producing meanwhile
    await g.flush_once()
    await produce(4000, 3000)
    await _wait(lambda: g.buffer_depth() == 0)
    assert await g.buffer.count() == 0 and g.buffer.rows == 0
    vals = _values(g)
    assert _first_occurrences(vals) == list(range(7000))    # nothing lost, FIFO order end to end
    stored = sum(stores)
    assert stored >= 3000
    assert len(stores) <= stored / 20, f"{len(stores)} SQLite transactions for {stored} rows"
    assert max_outbox <= 2000                                 # bounded memory (backpressure)
    assert g.client.max_inflight > 1                          # pipelined publishes (window), not one RTT each
    await _close(g)


async def test_backlog_flush_outpaces_new_readings(make_gateway):
    """The flush has priority: with a large backlog and readings still arriving, the backlog shrinks (the old path
    stored ~5 rows/s against ~230/s arriving and never drained)."""
    g = await make_gateway(rtt_s=0.001, outbox_max=5000, publish_window=100, spill_batch=500, spill_max_age_s=0.2)
    await g.buffer.store_many([(f"factory/{TENANT}/sensors/p", {"v": 1, "value": float(i), "unit": "",
                                                                  "ts": "2026-10-01T12:00:00.000Z"})
                               for i in range(20000)])
    g.start_sender()
    stop = asyncio.Event()

    async def produce():
        i = 20000
        while not stop.is_set():
            await g._publish("p", float(i), T0 + timedelta(milliseconds=i))
            i += 1
            await asyncio.sleep(0.002)                        # ~500/s offered (QA: 230/s)
        return i

    task = asyncio.create_task(produce())
    await _wait(lambda: g.buffer.rows == 0, timeout=60)
    stop.set()
    last = await task
    await _wait(lambda: g.buffer_depth() == 0)
    assert _first_occurrences(_values(g)) == list(range(last))
    await _close(g)


async def test_failure_inside_a_window_moves_the_rest_to_the_buffer_in_order(make_gateway):
    g = await make_gateway(publish_window=10, spill_batch=1000, spill_max_age_s=10.0)
    calls = {"n": 0}
    real = g.client.publish

    async def flaky(topic, payload, qos=0):
        calls["n"] += 1
        if calls["n"] == 5:
            g.client.online = False                          # the link dies in the middle of the window
        return await real(topic, payload, qos)
    g.client.publish = flaky
    for i in range(10):
        g._outbox.append((f"factory/{TENANT}/sensors/p", {"value": float(i)}, 0.0))
    await g._pump(flush=True)
    assert _values(g) == [0, 1, 2, 3]
    assert [p["value"] for _, _, p in await g.buffer.peek()] == [float(i) for i in range(4, 10)]
    g.client.online = True
    assert await g.flush_once() == 6
    assert _values(g) == list(range(10))
    await _close(g)


async def test_stop_writes_the_outbox_to_sqlite_and_a_new_gateway_flushes_it(make_gateway, tmp_path):
    g = await make_gateway(spill_batch=10000, spill_max_age_s=60.0)
    g.client.online = False
    g.publisher.is_connected = False
    g.start_sender()
    for i in range(500):
        await g._publish("p", float(i), T0 + timedelta(milliseconds=i))
    assert len(g._outbox) == 500 and g.buffer.rows == 0      # still in memory (spill not due)
    await g.stop()                                            # SIGTERM path
    b = LocalBuffer(gw_mod.settings.buffer_db_path)
    await b.initialize()
    assert b.rows == 500 and [p["value"] for _, _, p in await b.peek(5)] == [0.0, 1.0, 2.0, 3.0, 4.0]
    await b.close()


async def test_full_outbox_applies_backpressure_instead_of_growing(make_gateway):
    """Broker up but slow (RTT 50 ms, window 5): the producer waits — the outbox never exceeds outbox_max."""
    g = await make_gateway(rtt_s=0.05, outbox_max=20, publish_window=5)
    g.start_sender()
    peak = 0

    async def produce():
        nonlocal peak
        for i in range(100):
            await g._publish("p", float(i), T0 + timedelta(milliseconds=i))
            peak = max(peak, len(g._outbox))
    await asyncio.wait_for(produce(), timeout=30)
    assert peak <= 20
    await _wait(lambda: g.buffer_depth() == 0)
    assert _values(g) == list(range(100)) and g.buffer.rows == 0   # nothing spilled: it just waited
    await _close(g)


async def test_heartbeat_reports_memory_and_sqlite_backlog_without_counting_the_table(make_gateway):
    g = await make_gateway(spill_batch=10000, spill_max_age_s=60.0)
    g.publisher.is_connected = False
    g.client.online = False
    g.start_sender()
    for i in range(30):
        await g._publish("p", float(i), T0)
    await g.buffer.store_many([("t", {"value": 1.0})] * 7)

    async def no_count():
        raise AssertionError("COUNT(*) on the hot path")
    g.buffer.count = no_count
    assert g.buffer_depth() == 37
    assert (await g.fleet_heartbeat_body())["buffer_depth"] == 37
    g.buffer.count = LocalBuffer.count.__get__(g.buffer)
    await _close(g)


# ── OPC UA: one ordered consumer, bounded queue ─────────────────────────────

async def test_opcua_notifications_go_through_one_bounded_ordered_queue():
    got: list[float] = []
    gate = asyncio.Event()

    async def on_reading(tag_id, value, ts=None, initial=False):
        await gate.wait()                                     # the gateway is slow (outbox full)
        got.append(value)

    d = OpcUaDriver("opc.tcp://127.0.0.1:1/x/", [], on_reading, max_pending=100)
    d._pending_evt = asyncio.Event()
    consumer = asyncio.create_task(d._consume())
    for i in range(150):
        d._push(("st", float(i), None, False))
    await asyncio.sleep(0.01)
    tasks_before = len(asyncio.all_tasks())
    assert d.pending <= 100 and d.dropped >= 49               # bounded, and the overflow is counted (not silent)
    gate.set()
    await _wait(lambda: d.pending == 0)
    assert got == sorted(got) and got[0] == 0.0               # in arrival order
    assert len(asyncio.all_tasks()) <= tasks_before           # no task per notification
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)


async def test_opcua_handler_hands_items_to_the_sink_without_spawning_tasks():
    from asyncua import ua
    items = []
    nid = ua.NodeId(1, 2)

    class Cfg:
        tag_id, scale, offset = "st", 1.0, 0.0

    class Node:
        nodeid = nid

    async def on_reading(*a, **k):
        raise AssertionError("not called directly")

    h = _SubscriptionHandler({nid: Cfg()}, on_reading, source_timestamps=True, sink=items.append)
    before = len(asyncio.all_tasks())
    for v in range(5):
        h.datachange_notification(Node(), v, None)
    assert [(t, v, i) for t, v, _ts, i in items] == [("st", float(v), v == 0) for v in range(5)]
    assert len(asyncio.all_tasks()) == before


def test_default_opcua_queue_survives_a_one_second_pause_at_500x():
    """F1 at 500×: ~116 strokes/s per tag. 64 overflowed after ~0.55 s of gateway pause (QA (b))."""
    assert gw_mod.settings.opcua_queue_size >= 116 * 30
    assert OpcUaDriver("opc.tcp://x:1/").__dict__["_queue_size"] >= 116 * 30
