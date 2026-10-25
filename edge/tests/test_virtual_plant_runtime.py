"""Planta virtual — servidores reais OPC UA / Modbus em processo, lidos pelos drivers do gateway."""

import asyncio
import json
import logging

import aiohttp
import pytest
from asyncua import Client

from edge.src.protocols.modbus import ModbusDriver, ModbusTagConfig
from edge.tests.conftest import free_port
from edge.virtual_plant.layout import addresses
from edge.virtual_plant.model import PlantModel
from edge.virtual_plant.runtime import PulseTrain, VirtualPlantRuntime, encode_modbus

logging.getLogger("asyncua").setLevel(logging.ERROR)


@pytest.fixture
async def plant():
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=1, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=free_port(), update_ms=20)
    async with rt:
        yield rt


def _addr(key):
    return next(a for a in addresses() if a.key == key)


def _modbus_tag(key, tag_id=None):
    a = _addr(key)
    return ModbusTagConfig(tag_id or key, a.register, a.register_type, scale=a.signal.scale,
                           data_type=a.data_type or "uint16", device_id=a.unit_id)


def test_pulse_train_emits_exactly_target_with_min_widths():
    t = PulseTrain(high_s=0.05, low_s=0.05)
    levels = []
    now = 0.0
    while now < 2.0:
        levels.append(t.update(5, now))
        now += 0.001
    rises = sum(1 for a, b in zip(levels, levels[1:]) if not a and b) + (1 if levels[0] else 0)
    assert rises == 5 == t.emitted and t.backlog == 0
    # cada nível alto/baixo dura ≥ 50 amostras de 1 ms
    runs, cur, n = [], levels[0], 0
    for lv in levels:
        if lv == cur:
            n += 1
        else:
            runs.append((cur, n))
            cur, n = lv, 1
    assert all(n >= 49 for lv, n in runs[1:] if lv)   # pulsos altos completos
    # o alvo nunca diminui (contagem do modelo é monotônica)
    t.update(2, now)
    assert t.target == 5


def test_encode_modbus():
    assert encode_modbus(_addr("montagem.robo.weld_cycles"), 0x0001_0002) == [1, 2]
    assert encode_modbus(_addr("montagem.fixacao.a1.stroke_time"), 185.3) == [1853]
    assert encode_modbus(_addr("montagem.fixacao.a1.eos"), True) == [1]


async def test_opcua_values_and_source_timestamp(plant):
    async with Client(url=plant.opcua_endpoint) as client:
        node = client.get_node("ns=2;s=usinagem.torno.run_state")
        for _ in range(100):          # tempo simulado 0 = tudo parado; o 1º passo liga a máquina
            dv = await node.read_data_value()
            if dv.Value.Value is True:
                break
            await asyncio.sleep(0.02)
        assert dv.Value.Value is True
        assert dv.SourceTimestamp is not None
        good = await client.get_node("ns=2;s=usinagem.torno.parts_good").read_value()
        assert good == plant.model.values()["usinagem.torno.parts_good"]
        speed = await client.get_node("ns=2;s=usinagem.torno.spindle.speed").read_value()
        assert isinstance(speed, float)


async def test_modbus_values_via_gateway_driver(plant):
    drv = ModbusDriver("127.0.0.1", plant.modbus_port, tags=[
        _modbus_tag("montagem.fixacao.a3.cycles"),
        _modbus_tag("montagem.fixacao.a2.stroke_time"),
        _modbus_tag("montagem.robo.run_state"),
        _modbus_tag("montagem.transportador.parts_good"),
    ])
    await asyncio.sleep(0.3)
    await plant.freeze()               # compara com o modelo sem corrida
    assert await drv.connect()
    try:
        vals = await drv.poll_once()
        truth = plant.model.values()
        assert vals["montagem.fixacao.a3.cycles"] == truth["montagem.fixacao.a3.cycles"]
        assert vals["montagem.fixacao.a2.stroke_time"] == pytest.approx(truth["montagem.fixacao.a2.stroke_time"], abs=0.05)
        assert vals["montagem.robo.run_state"] == 1.0
        assert vals["montagem.transportador.parts_good"] == truth["montagem.transportador.parts_good"]
    finally:
        await drv.stop()


async def test_rollover_visible_on_modbus_register():
    """A3 começa em 65 200 e golpeia 2×/peça: a 2000× a virada 65535 → 0 aparece no registrador."""
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=2000, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=20)
    async with rt:
        drv = ModbusDriver("127.0.0.1", rt.modbus_port, tags=[_modbus_tag("montagem.fixacao.a3.cycles", "a3")])
        await drv.connect()
        readings = []
        try:
            for _ in range(600):
                readings.append((await drv.poll_once())["a3"])
                if len(readings) > 1 and readings[-1] < readings[-2]:
                    break
                await asyncio.sleep(0.05)
        finally:
            await drv.stop()
    drops = [(a, b) for a, b in zip(readings, readings[1:]) if b < a]
    assert drops, f"sem rollover em {len(readings)} leituras (última {readings[-1]})"
    prev, cur = drops[0]
    assert prev >= 0.9 * 65_535 and cur < 100           # rollover, não reset (docs/domain.md §3.3)
    assert max(readings) <= 65_535


async def test_status_endpoint(plant):
    await asyncio.sleep(0.3)
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{plant.status_port}/state") as r:
            state = await r.json()
    assert state["seed"] == 42
    assert state["runtime"]["pulses"]["montagem.fixacao.a1.eos"]["backlog"] >= 0
    assert "montagem.fixacao.a1.cycles" in state["values"]
    json.dumps(state)
