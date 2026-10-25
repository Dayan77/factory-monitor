"""Modbus driver against an in-process pymodbus TCP server (virtual PLC)."""

import asyncio
import struct

import pytest
from pymodbus.simulator.simutils import DataType
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import SimData, SimDevice

from edge.src.protocols.modbus import ModbusDriver, ModbusTagConfig, decode_registers

FC_COIL, FC_DI, FC_HR, FC_IR = 1, 2, 3, 4


def split32(value: int | float, kind: str = "uint32", word_order: str = "big") -> list[int]:
    fmt = {"uint32": ">I", "int32": ">i", "float32": ">f"}[kind]
    b = struct.pack(fmt, value)
    words = [int.from_bytes(b[0:2], "big"), int.from_bytes(b[2:4], "big")]
    return words if word_order == "big" else words[::-1]


SIZE = 100
# function code → memory area (coils, discrete inputs, holding, input registers)
_AREA = {1: "co", 5: "co", 15: "co", 2: "di", 3: "hr", 6: "hr", 16: "hr", 4: "ir"}


class VirtualPlc:
    """Minimal PLC: 4 separate areas of SIZE points each, values settable from the test.

    pymodbus 3.15 SimDevice + an `action` hook that mirrors `self.memory` into the
    registers served on every request, so tests can change PLC values at runtime.
    """

    def __init__(self, port: int) -> None:
        self.memory = {area: [0] * SIZE for area in ("co", "di", "hr", "ir")}
        bits = lambda: [SimData(0, count=SIZE, values=False, datatype=DataType.BITS)]  # noqa: E731
        regs = lambda: [SimData(0, count=SIZE, values=0, datatype=DataType.REGISTERS)]  # noqa: E731
        device = SimDevice(1, simdata=(bits(), bits(), regs(), regs()), action=self._action)
        self.server = ModbusTcpServer(device, address=("127.0.0.1", port))
        self.port = port

    async def _action(self, function_code, start_address, address, count, current_registers, set_values):
        area = _AREA[function_code]
        mem = self.memory[area]
        for i in range(len(current_registers)):
            reg = start_address + i
            if area in ("co", "di"):
                # bit areas are stored packed: 16 bits per word, bit 0 = LSB
                word = 0
                for b in range(16):
                    n = reg * 16 + b
                    if n < SIZE and mem[n]:
                        word |= 1 << b
                current_registers[i] = word
            elif 0 <= reg < SIZE:
                current_registers[i] = mem[reg]
        return None

    def set(self, fc: int, address: int, values: list) -> None:
        mem = self.memory[_AREA[fc]]
        mem[address:address + len(values)] = list(values)

    async def __aenter__(self):
        await self.server.serve_forever(background=True)
        for _ in range(50):  # wait until listening
            try:
                _, w = await asyncio.open_connection("127.0.0.1", self.port)
                w.close()
                await w.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        await self.server.shutdown()


@pytest.fixture
async def plc(port):
    async with VirtualPlc(port) as p:
        yield p


@pytest.fixture
async def make_driver(plc):
    drivers: list[ModbusDriver] = []

    async def _make(tags, on_reading=None):
        d = ModbusDriver("127.0.0.1", plc.port, tags=tags, on_reading=on_reading)
        assert await d.connect()
        drivers.append(d)
        return d

    yield _make
    for d in drivers:
        await d.stop()


# ── pure decoding ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "regs,dtype,order,expected",
    [
        ([0x0001], "uint16", "big", 1),
        ([0xFFFF], "uint16", "big", 65535),
        ([0xFFFF], "int16", "big", -1),
        ([0x0001, 0x0000], "uint32", "big", 65536),
        ([0x0000, 0x0001], "uint32", "little", 65536),
        ([0xFFFF, 0xFFFE], "int32", "big", -2),
        ([0x4049, 0x0FDB], "float32", "big", struct.unpack(">f", bytes.fromhex("40490fdb"))[0]),
        ([0x0FDB, 0x4049], "float32", "little", struct.unpack(">f", bytes.fromhex("40490fdb"))[0]),
    ],
)
def test_decode_registers(regs, dtype, order, expected):
    assert decode_registers(regs, dtype, order) == pytest.approx(expected)


def test_decode_needs_two_registers_for_32bit():
    with pytest.raises(ValueError):
        decode_registers([1], "uint32")


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        ModbusTagConfig("t", 0, "holding", data_type="float64")
    with pytest.raises(ValueError):
        ModbusTagConfig("t", 0, "bogus")
    with pytest.raises(ValueError):
        ModbusTagConfig("t", 0, "holding", word_order="middle")


def test_from_dict_ignores_unknown_keys_and_deadband_defaults():
    cfg = ModbusTagConfig.from_dict(
        {"tag_id": "t", "address": 3, "register_type": "coil", "comment": "x"}
    )
    assert cfg.address == 3 and cfg.bypass_deadband is True
    assert ModbusTagConfig("a", 0, "holding").bypass_deadband is False
    assert ModbusTagConfig("a", 0, "holding", no_deadband=True).bypass_deadband is True


# ── against the in-process server ────────────────────────────────────────────

async def test_holding_register_uint16_with_scale_and_offset(plc, make_driver):
    plc.set(FC_HR, 10, [2537])
    tag = ModbusTagConfig("temp", 10, "holding", scale=0.1, offset=-273.15, unit="°C")
    d = await make_driver([tag])
    assert await d.read_tag(tag) == pytest.approx(253.7 - 273.15)


async def test_input_register_int16_negative(plc, make_driver):
    plc.set(FC_IR, 5, [0xFF9C])  # -100
    tag = ModbusTagConfig("t", 5, "input", data_type="int16", scale=0.5)
    d = await make_driver([tag])
    assert await d.read_tag(tag) == -50.0


@pytest.mark.parametrize("word_order", ["big", "little"])
async def test_uint32_counter_across_two_registers(plc, make_driver, word_order):
    count = 70_000 * 3 + 123  # > 65535: needs both words
    plc.set(FC_HR, 20, split32(count, "uint32", word_order))
    tag = ModbusTagConfig("cycles", 20, "holding", data_type="uint32", word_order=word_order)
    d = await make_driver([tag])
    assert await d.read_tag(tag) == count


async def test_int32_negative_across_two_registers(plc, make_driver):
    plc.set(FC_HR, 30, split32(-123456, "int32"))
    tag = ModbusTagConfig("pos", 30, "holding", data_type="int32", scale=0.01)
    d = await make_driver([tag])
    assert await d.read_tag(tag) == pytest.approx(-1234.56)


@pytest.mark.parametrize("word_order", ["big", "little"])
async def test_float32_with_scale_offset(plc, make_driver, word_order):
    plc.set(FC_IR, 40, split32(12.75, "float32", word_order))
    tag = ModbusTagConfig(
        "pressure", 40, "input", data_type="float32", word_order=word_order, scale=2.0, offset=1.0
    )
    d = await make_driver([tag])
    assert await d.read_tag(tag) == pytest.approx(26.5)


async def test_coil_and_discrete_input(plc, make_driver):
    plc.set(FC_COIL, 2, [1])
    plc.set(FC_DI, 7, [0])
    coil = ModbusTagConfig("running", 2, "coil")
    di = ModbusTagConfig("fault", 7, "discrete")
    d = await make_driver([coil, di])
    assert await d.read_tag(coil) == 1.0
    assert await d.read_tag(di) == 0.0
    plc.set(FC_DI, 7, [1])
    assert await d.read_tag(di) == 1.0


async def test_poll_once_calls_on_reading_and_tracks_changes(plc, make_driver):
    readings: list[tuple[str, float]] = []

    async def on_reading(tag_id, value):
        readings.append((tag_id, value))

    tags = [
        ModbusTagConfig("a", 0, "holding"),
        ModbusTagConfig("b", 1, "holding", data_type="uint32"),
    ]
    plc.set(FC_HR, 0, [7])
    plc.set(FC_HR, 1, split32(100_000))
    d = await make_driver(tags, on_reading)
    assert await d.poll_once() == {"a": 7.0, "b": 100_000.0}
    plc.set(FC_HR, 0, [8])
    await d.poll_once()
    assert readings == [("a", 7.0), ("b", 100_000.0), ("a", 8.0), ("b", 100_000.0)]


async def test_read_error_does_not_break_other_tags(plc, make_driver):
    good = ModbusTagConfig("good", 0, "holding")
    bad = ModbusTagConfig("bad", 5000, "holding")  # out of the PLC's address space
    plc.set(FC_HR, 0, [5])
    d = await make_driver([good, bad])
    assert await d.poll_once() == {"good": 5.0}


async def test_run_loop_polls_and_stops(plc, port):
    got = asyncio.Event()
    seen: list[float] = []

    async def on_reading(tag_id, value):
        seen.append(value)
        got.set()

    plc.set(FC_HR, 3, [42])
    d = ModbusDriver("127.0.0.1", plc.port, [ModbusTagConfig("x", 3, "holding", poll_interval_ms=50)], on_reading)
    task = asyncio.create_task(d.run())
    await asyncio.wait_for(got.wait(), 5)
    await d.stop()
    await asyncio.wait_for(task, 5)
    assert seen[0] == 42.0
