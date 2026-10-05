"""OPC UA driver against an in-process asyncua server."""

import asyncio
import logging

import pytest
from asyncua import Server, ua

from edge.src.protocols.opcua import OpcTagConfig, OpcUaDriver

logging.getLogger("asyncua").setLevel(logging.WARNING)


@pytest.fixture
async def opc_server(port):
    server = Server()
    await server.init()
    endpoint = f"opc.tcp://127.0.0.1:{port}/fm-test/"
    server.set_endpoint(endpoint)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace("urn:factory-monitor:test")
    plc = await server.nodes.objects.add_object(idx, "VirtualPLC")
    temp = await plc.add_variable(ua.NodeId(1001, idx), "Temperature", 20.0)
    count = await plc.add_variable(ua.NodeId("PartCount", idx), "PartCount", ua.Variant(0, ua.VariantType.UInt32))
    running = await plc.add_variable(ua.NodeId("Running", idx), "Running", False)
    text = await plc.add_variable(ua.NodeId("Text", idx), "Text", "hello")
    async with server:
        yield {
            "endpoint": endpoint, "idx": idx,
            "temp": temp, "count": count, "running": running, "text": text,
        }


class Collector:
    def __init__(self) -> None:
        self.items: list[tuple[str, float]] = []
        self._event = asyncio.Event()

    async def __call__(self, tag_id: str, value: float) -> None:
        self.items.append((tag_id, value))
        self._event.set()

    async def wait_for(self, predicate, timeout: float = 10.0) -> None:
        async def _loop():
            while not predicate(self.items):
                self._event.clear()
                await self._event.wait()
        await asyncio.wait_for(_loop(), timeout)

    def values(self, tag_id: str) -> list[float]:
        return [v for t, v in self.items if t == tag_id]


@pytest.fixture
async def run_driver():
    started: list[tuple[OpcUaDriver, asyncio.Task]] = []

    def _start(endpoint, tags, on_reading):
        d = OpcUaDriver(endpoint, tags, on_reading, publishing_interval_ms=50, reconnect_delay_s=0.2)
        started.append((d, asyncio.create_task(d.run())))
        return d

    yield _start
    for d, task in started:
        await d.stop()
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


async def test_initial_values_with_scale_offset_and_node_id_formats(opc_server, run_driver):
    idx = opc_server["idx"]
    col = Collector()
    tags = [
        OpcTagConfig("temp", f"ns={idx};i=1001", scale=1.8, offset=32.0, unit="°F"),  # numeric id
        OpcTagConfig("count", f"ns={idx};s=PartCount"),                                # string id
        OpcTagConfig("running", f"ns={idx};s=Running"),                                # boolean → 0/1
    ]
    run_driver(opc_server["endpoint"], tags, col)
    await col.wait_for(lambda items: {t for t, _ in items} >= {"temp", "count", "running"})
    assert col.values("temp")[0] == pytest.approx(68.0)
    assert col.values("count")[0] == 0.0
    assert col.values("running")[0] == 0.0


async def test_data_changes_are_delivered(opc_server, run_driver):
    idx = opc_server["idx"]
    col = Collector()
    run_driver(
        opc_server["endpoint"],
        [OpcTagConfig("count", f"ns={idx};s=PartCount"), OpcTagConfig("running", f"ns={idx};s=Running")],
        col,
    )
    await col.wait_for(lambda items: len({t for t, _ in items}) == 2)

    await opc_server["running"].write_value(True)
    for n in (1, 2, 3):
        await opc_server["count"].write_value(ua.Variant(n, ua.VariantType.UInt32))
        await col.wait_for(lambda items, n=n: n in [v for t, v in items if t == "count"])

    await col.wait_for(lambda items: 1.0 in [v for t, v in items if t == "running"])
    assert col.values("count") == [0.0, 1.0, 2.0, 3.0]


async def test_non_numeric_value_is_ignored(opc_server, run_driver):
    idx = opc_server["idx"]
    col = Collector()
    run_driver(
        opc_server["endpoint"],
        [OpcTagConfig("text", f"ns={idx};s=Text"), OpcTagConfig("temp", f"ns={idx};i=1001")],
        col,
    )
    await col.wait_for(lambda items: "temp" in {t for t, _ in items})
    await asyncio.sleep(0.3)
    assert col.values("text") == []


async def test_driver_retries_until_server_is_up(port, run_driver):
    """Driver started before the PLC is reachable must connect once it comes up."""
    endpoint = f"opc.tcp://127.0.0.1:{port}/late/"
    col = Collector()
    run_driver(endpoint, [OpcTagConfig("t", "ns=2;i=5001")], col)
    await asyncio.sleep(0.5)  # a couple of failed attempts

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    idx = await server.register_namespace("urn:late")
    assert idx == 2
    obj = await server.nodes.objects.add_object(idx, "PLC")
    await obj.add_variable(ua.NodeId(5001, idx), "T", 7.5)
    async with server:
        await col.wait_for(lambda items: ("t", 7.5) in items, timeout=15)


async def test_source_timestamps_flag_the_initial_notification_of_each_item(opc_server):
    """The first notification after subscribing carries the value's LAST CHANGE time (possibly hours old):
    it is flagged `initial` so the gateway clock does not take it for clock skew (Sprint 3 QA)."""
    idx = opc_server["idx"]
    got: list[tuple[str, float, object, bool]] = []
    ev = asyncio.Event()

    async def on_reading(tag_id, value, ts, initial=False):
        got.append((tag_id, value, ts, initial))
        ev.set()

    d = OpcUaDriver(opc_server["endpoint"], [OpcTagConfig("running", f"ns={idx};s=Running")], on_reading,
                    publishing_interval_ms=50, reconnect_delay_s=0.2, source_timestamps=True)
    task = asyncio.create_task(d.run())
    try:
        await asyncio.wait_for(ev.wait(), 10)
        await opc_server["running"].write_value(True)
        for _ in range(100):
            if len(got) >= 2:
                break
            await asyncio.sleep(0.05)
        assert [(t, v, i) for t, v, _ts, i in got[:2]] == [("running", 0.0, True), ("running", 1.0, False)]
        assert got[1][2] is not None and got[1][2].tzinfo is not None
    finally:
        await d.stop()
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
