"""API de controle da manutenção da planta virtual (edge/virtual_plant/control.py)."""

import asyncio
import json
import logging

import aiohttp
import pytest
from aiohttp import web

from edge.src.protocols.modbus import ModbusDriver, ModbusTagConfig
from edge.tests.conftest import free_port
from edge.virtual_plant import control as ctl
from edge.virtual_plant.layout import addresses
from edge.virtual_plant.model import PlantConfig, PlantModel
from edge.virtual_plant.runtime import VirtualPlantRuntime

logging.getLogger("asyncua").setLevel(logging.ERROR)


@pytest.fixture
async def api():
    model = PlantModel(seed=42, config=PlantConfig(wear_repair="manual"))
    model.run_for(600)
    pushes = []

    async def after():
        pushes.append(model.sim_time_s)

    runner = web.AppRunner(ctl.build_app(ctl.PlantControl(model), after_change=after))
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    async with aiohttp.ClientSession(base_url=f"http://127.0.0.1:{port}") as s:
        yield s, model, pushes
    await runner.cleanup()


async def _post(s, path, body):
    async with s.post(path, data=json.dumps(body) if not isinstance(body, str) else body,
                      headers={"Content-Type": "application/json"}) as r:
        return r.status, await r.json()


async def test_status_exposes_true_remaining_life(api):
    s, model, _ = api
    async with s.get("/status") as r:
        assert r.status == 200
        st = await r.json()
    assert st["sim_time_s"] == 600.0 and st["repair_mode"] == "manual"
    a2 = st["components"]["montagem.fixacao.a2"]
    it = model.wear.items["montagem.fixacao.a2"]
    assert a2["remaining"] == round(it.remaining) and a2["life_to_failure"] == round(it.life)
    assert st["machines"]["montagem.robo"] == {"name": "Robô de Solda", "fault_code": 0, "fault_source": None,
                                               "fault_since_s": None, "fault_item": None}


async def test_force_failure_on_robot_until_repair(api):
    s, model, pushes = api
    st, out = await _post(s, "/force-failure", {"machine": "Robô de Solda", "code": 331})
    assert st == 200 and out["event"]["code"] == 331 and out["event"]["source"] == "forced"
    assert out["category"]["reason_code"] == "EQP-04" and pushes        # publicado na hora
    assert model.values(include_analog=False)["montagem.robo.fault_state"] == 331
    model.run_for(900)
    assert model.values(include_analog=False)["montagem.robo.fault_state"] == 331   # até o reparo
    st, out = await _post(s, "/repair", {"machine": "montagem.robo"})
    assert st == 200 and out["cleared_code"] == 331 and out["action"] == "repaired"
    ev = model.snapshot()["truth"]["faults"]["montagem.robo"][-1]
    assert ev["source"] == "forced" and ev["duration_s"] == 900.0
    st, out = await _post(s, "/repair", {"machine": "montagem.robo"})
    assert st == 200 and out["action"] == "nothing_to_repair"


async def test_force_failure_default_code_and_duration(api):
    s, model, _ = api
    st, out = await _post(s, "/force-failure", {"machine": "montagem.transportador", "duration_s": 120})
    assert st == 200 and out["event"]["code"] == 151 and out["event"]["duration_s"] == 120
    model.run_for(121)
    assert model.values(include_analog=False)["montagem.transportador.fault_state"] == 0


async def test_force_wear_failure_on_position_then_replace(api):
    s, model, _ = api
    st, out = await _post(s, "/force-failure", {"position": "montagem.robo.axis1"})
    assert st == 200 and out["component"]["state"] == "failed" and out["machine"]["fault_code"] == 132
    assert out["machine"]["fault_source"] == "wear" and out["machine"]["fault_item"] == "montagem.robo.axis1"
    st, out = await _post(s, "/force-failure", {"position": "montagem.robo.axis1"})
    assert st == 409
    st, out = await _post(s, "/force-failure", {"machine": "montagem.robo", "code": 331})
    assert st == 409                                    # falha por desgaste: só troca/reparo
    st, out = await _post(s, "/replace", {"position": "Robô de Solda / Eixo 1", "reset_counter": True})
    assert st == 200 and out["repaired_failure"] and out["removed"]["reason"] == "failure"
    assert out["installed"]["install_no"] == 1 and out["installed"]["usage_since_install"] == 0
    v = model.values(include_analog=False)
    assert v["montagem.robo.fault_state"] == 0 and v["montagem.robo.axis1.cycles"] == 0


async def test_replace_preventive_keeps_counter_by_default(api):
    s, model, _ = api
    before = model.values()["montagem.fixacao.a2.cycles"]
    st, out = await _post(s, "/replace", {"position": "montagem.fixacao.a2"})
    assert st == 200 and out["removed"]["reason"] == "preventive_replacement" and not out["repaired_failure"]
    assert model.values()["montagem.fixacao.a2.cycles"] == before
    assert out["installed"]["indicators"]["drift_ms"] < 1


@pytest.mark.parametrize("path,body,status", [
    ("/replace", {}, 400),
    ("/replace", {"position": "montagem.fixacao.a9"}, 404),
    ("/replace", {"position": "montagem.fixacao.a1", "reset_counter": "yes"}, 400),
    ("/replace", {"position": "montagem.fixacao.a1", "reason": "broken"}, 400),
    ("/repair", {"machine": "Prensa"}, 404),
    ("/repair", {}, 400),
    ("/force-failure", {"machine": "montagem.robo", "code": 999}, 400),
    ("/force-failure", {"machine": "montagem.robo", "code": 443}, 400),     # código de outra máquina
    ("/force-failure", {"machine": "montagem.robo", "duration_s": -1}, 400),
    ("/force-failure", {"machine": "montagem.robo", "position": "montagem.robo.axis1"}, 400),
    ("/force-failure", "não é json", 400),
    ("/force-failure", [1, 2], 400),
])
async def test_validation_errors(api, path, body, status):
    s, _, _ = api
    st, out = await _post(s, path, body)
    assert st == status and "error" in out


def test_loopback_detection():
    assert ctl._is_loopback("127.0.0.1") and ctl._is_loopback("::1")
    assert not ctl._is_loopback("172.18.0.5") and not ctl._is_loopback(None) and not ctl._is_loopback("x")


async def test_scenario_model_answers_409():
    class NoWear:
        sim_time_s = 0.0
    c = ctl.PlantControl(NoWear())
    with pytest.raises(ctl.ControlError) as e:
        c.status()
    assert e.value.status == 409


async def _noop(*_a, **_k):
    return None


def _modbus(key):
    a = next(x for x in addresses() if x.key == key)
    return ModbusTagConfig(key, a.register, a.register_type, scale=a.signal.scale,
                           data_type=a.data_type or "uint16", device_id=a.unit_id)


async def test_runtime_control_port_publishes_fault_to_plc_and_cli():
    """Caminho real: API de controle → modelo → registrador Modbus lido pelo driver do gateway; CLI stdlib."""
    cport = free_port()
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=1, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=20, control_port=cport)
    async with rt:
        await rt.freeze()                           # tempo parado: o valor só muda pela API
        drv = ModbusDriver("127.0.0.1", rt.modbus_port, tags=[_modbus("montagem.robo.fault_state")],
                           on_reading=_noop)
        await drv.connect()
        try:
            assert (await drv.poll_once())["montagem.robo.fault_state"] == 0
            url = f"http://127.0.0.1:{cport}"
            rc = await asyncio.to_thread(ctl.main, ["--url", url, "force-failure", "--machine", "montagem.robo",
                                                    "--code", "331"])
            assert rc == 0
            assert (await drv.poll_once())["montagem.robo.fault_state"] == 331
            rc = await asyncio.to_thread(ctl.main, ["--url", url, "repair", "Robô de Solda"])
            assert rc == 0
            assert (await drv.poll_once())["montagem.robo.fault_state"] == 0
            rc = await asyncio.to_thread(ctl.main, ["--url", url, "replace", "montagem.fixacao.a9"])
            assert rc == 1
        finally:
            await drv.stop()


async def test_runtime_refuses_control_on_public_host_without_token():
    rt = VirtualPlantRuntime(PlantModel(seed=1, faults=False), speed=1, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, control_port=free_port(),
                             control_host="0.0.0.0")
    async with rt:
        assert rt._control_runner is None
