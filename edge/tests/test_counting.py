"""Contagem de borda no edge (cycle_pulse): sem perda de pulso na taxa configurada e persistente."""

import asyncio
import time

import pytest

from edge.src.counting import EdgeCounterStore, PulseEdgeCounter, PulsePoller
from edge.src.plant_map import MapTag
from edge.tests.conftest import free_port
from edge.virtual_plant.model import PlantModel
from edge.virtual_plant.runtime import ModbusPlc, PulseTrain, VirtualPlantRuntime


def test_store_is_monotonic_and_persistent(tmp_path):
    db = str(tmp_path / "c.db")
    s = EdgeCounterStore(db)
    assert s.get("k") == 0
    assert s.add("k") == 1
    assert s.add_many({"k": 4, "j": 2}) == {"k": 5, "j": 2}
    with pytest.raises(ValueError):
        s.add("k", -1)
    s.close()
    s2 = EdgeCounterStore(db)
    assert s2.get("k") == 5 and s2.get("j") == 2
    s2.close()


def test_edge_counter_counts_rising_edges_only():
    e = PulseEdgeCounter()
    seq = [True, True, False, True, True, False, False, True, False]
    # primeira amostra (alto) não conta: pode ser um pulso já contado antes de reiniciar
    assert sum(e.sample(v) for v in seq) == 2
    e.reset_level()
    assert e.sample(True) is False


class Recorder:
    def __init__(self):
        self.items = []

    async def __call__(self, tag_id, value, ts=None):
        self.items.append((tag_id, value, ts))

    def last(self, tag_id):
        return [v for t, v, _ in self.items if t == tag_id][-1]


def _pulse_tag(address=0, unit=1):
    return MapTag(key="cell.a1.eos", source="modbus", semantic_role="cycle_pulse", address=address,
                  tag_id="tag-a1", unit_id=unit, register_type="discrete")


async def _drive(plc: ModbusPlc, train: PulseTrain, target: int, unit=1, address=0, stop=None):
    while not stop.is_set():
        plc.write(unit, "di", address, [1 if train.update(target) else 0])
        await asyncio.sleep(0.001)


async def _run_pulses(tmp_path, n_pulses, high_ms, low_ms, poll_ms, db_name="c.db", start_offset=0):
    port = free_port()
    plc = ModbusPlc("127.0.0.1", port, [1])
    await plc.start()
    store = EdgeCounterStore(str(tmp_path / db_name))
    rec = Recorder()
    poller = PulsePoller("127.0.0.1", port, [_pulse_tag()], store, rec, interval_ms=poll_ms)
    task = asyncio.create_task(poller.run())
    try:
        for _ in range(200):
            if poller.connected and poller.polls > 2:
                break
            await asyncio.sleep(0.01)
        train = PulseTrain(high_ms / 1000, low_ms / 1000)
        stop = asyncio.Event()
        driver = asyncio.create_task(_drive(plc, train, n_pulses, stop=stop))
        deadline = time.monotonic() + n_pulses * (high_ms + low_ms) / 1000 * 1.5 + 2
        while (train.emitted < n_pulses or train.level) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)       # último pulso amostrado
        stop.set()
        await driver
        assert train.emitted == n_pulses
        return store.get("cell.a1.eos"), rec, poller
    finally:
        await poller.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        store.close()
        await plc.stop()


async def test_no_pulse_lost_at_configured_rate(tmp_path):
    """Pulsos de 50 ms alto / 50 ms baixo (10 Hz) com polling de 20 ms: contagem exata."""
    total, rec, poller = await _run_pulses(tmp_path, 60, 50, 50, 20)
    assert total == 60
    assert rec.last("tag-a1") == 60.0
    # publicou acumulado (valores crescentes), nunca delta
    vals = [v for t, v, _ in rec.items if t == "tag-a1"]
    assert vals == sorted(vals) and max(vals) == 60.0
    assert poller.max_gap_s < 0.045, f"polling atrasou {poller.max_gap_s * 1000:.0f} ms (> metade do pulso)"


async def test_counter_survives_gateway_restart(tmp_path):
    first, _, _ = await _run_pulses(tmp_path, 15, 50, 50, 20, db_name="persist.db")
    assert first == 15
    # "reinício do gateway": novo processo/poller, mesmo arquivo SQLite → continua de 15
    second, rec, _ = await _run_pulses(tmp_path, 10, 50, 50, 20, db_name="persist.db")
    assert second == 25
    first_report = [v for t, v, _ in rec.items if t == "tag-a1"][0]
    assert first_report == 15.0      # ao reconectar publica o acumulado persistido, não zero


async def test_end_to_end_virtual_plant_pulses(tmp_path):
    """Bit de fim de curso do A1 na planta virtual acelerada (200×): contagem do edge = pulsos emitidos."""
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=200, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=20)
    addr = next(a for a in rt.addresses if a.key == "montagem.fixacao.a1.eos")
    store = EdgeCounterStore(str(tmp_path / "vp.db"))
    rec = Recorder()
    async with rt:
        poller = PulsePoller("127.0.0.1", rt.modbus_port,
                             [_pulse_tag(address=addr.register, unit=addr.unit_id)], store, rec, interval_ms=20)
        # sincroniza: começa a contar só a partir do que for emitido depois de conectado
        task = asyncio.create_task(poller.run())
        while not (poller.connected and poller.polls > 0):
            await asyncio.sleep(0.01)
        train = rt.pulses["montagem.fixacao.a1.eos"]
        while train.level:
            await asyncio.sleep(0.005)
        base_emitted = train.emitted
        base_count = store.get("cell.a1.eos")
        await asyncio.sleep(4.0)
        await rt.freeze()              # congela o modelo; o trem de pulsos esvazia a fila já devida
        while train.backlog or train.level:
            await asyncio.sleep(0.01)
        emitted = train.emitted
        await asyncio.sleep(0.1)       # último pulso amostrado
        counted = store.get("cell.a1.eos") - base_count
        await poller.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    store.close()
    assert emitted - base_emitted >= 10
    assert counted == emitted - base_emitted
