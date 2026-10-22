import pytest

from edge.src.buffer.local_buffer import LocalBuffer


@pytest.fixture
async def buf(tmp_path):
    b = LocalBuffer(str(tmp_path / "buffer.db"))
    await b.initialize()
    yield b
    await b.close()


async def test_store_and_count(buf):
    assert await buf.count() == 0
    await buf.store("factory/t/sensors/a", {"value": 1.0})
    await buf.store("factory/t/sensors/b", {"value": 2.0})
    assert await buf.count() == 2


async def test_flush_returns_fifo_and_clears(buf):
    for i in range(5):
        await buf.store(f"topic/{i}", {"value": float(i), "unit": "°C"})
    rows = await buf.flush()
    assert [t for t, _ in rows] == [f"topic/{i}" for i in range(5)]
    assert rows[0][1] == {"value": 0.0, "unit": "°C"}
    assert await buf.count() == 0


async def test_survives_restart(tmp_path):
    """Offline data persists on disk across gateway restarts."""
    path = str(tmp_path / "persist.db")
    b1 = LocalBuffer(path)
    await b1.initialize()
    await b1.store("x", {"value": 42})
    await b1.close()

    b2 = LocalBuffer(path)
    await b2.initialize()
    assert await b2.count() == 1
    assert (await b2.peek())[0][1:] == ("x", {"value": 42})
    await b2.close()


async def test_flush_to_sends_in_order_and_deletes(buf):
    for i in range(7):
        await buf.store(f"t/{i}", {"value": i})
    sent: list[tuple[str, dict]] = []

    async def publish(topic, payload):
        sent.append((topic, payload))
        return True

    n = await buf.flush_to(publish, batch_size=3)  # several batches
    assert n == 7
    assert [p["value"] for _, p in sent] == list(range(7))
    assert await buf.count() == 0


async def test_flush_to_stops_on_failure_without_losing_data(buf):
    for i in range(5):
        await buf.store(f"t/{i}", {"value": i})
    calls: list[int] = []

    async def flaky(topic, payload):
        calls.append(payload["value"])
        return payload["value"] < 2  # 0 and 1 go through, 2 fails (broker dropped)

    assert await buf.flush_to(flaky) == 2
    assert calls == [0, 1, 2]  # stopped at first failure, did not skip ahead
    remaining = [p["value"] for _, _, p in await buf.peek()]
    assert remaining == [2, 3, 4]

    async def ok(topic, payload):
        calls.append(payload["value"])
        return True

    assert await buf.flush_to(ok) == 3
    assert calls[-3:] == [2, 3, 4]
    assert await buf.count() == 0


async def test_flush_to_treats_exception_as_failure(buf):
    await buf.store("t", {"value": 1})

    async def boom(topic, payload):
        raise ConnectionError("broker gone")

    assert await buf.flush_to(boom) == 0
    assert await buf.count() == 1


async def test_uninitialized_buffer_is_noop(tmp_path):
    b = LocalBuffer(str(tmp_path / "x.db"))
    assert await b.count() == 0
    assert await b.flush() == []
    assert await b.peek() == []
