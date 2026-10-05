"""Gateway com mapa de planta contra a planta virtual real (OPC UA + Modbus em processo)."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from edge.src import gateway as gw_mod
from edge.src.plant_map import parse_plant_map
from edge.tests.conftest import free_port
from edge.tests.test_plant_map import provisioned_doc
from edge.virtual_plant.model import PlantModel
from edge.virtual_plant.runtime import VirtualPlantRuntime

logging.getLogger("asyncua").setLevel(logging.ERROR)
TENANT = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(gw_mod.settings, "counter_db_path", str(tmp_path / "cnt.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", "")
    monkeypatch.setattr(gw_mod.settings, "gateway_id", "")
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    monkeypatch.setattr(gw_mod.settings, "deadband_pct", 0.5)
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    client = FakeClient()
    g.publisher._client, g.publisher.is_connected = client, True
    g.client = client
    yield g
    g._running = False
    for d in g.drivers:
        await d.stop()
    await g.buffer.close()
    if g.counter_store:
        g.counter_store.close()


async def test_full_path_virtual_plant_to_mqtt(gateway):
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=100, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=20)
    doc = provisioned_doc()
    pm = parse_plant_map(doc, env={"VIRTUAL_PLANT_HOST": "127.0.0.1",
                                   "VIRTUAL_PLANT_OPCUA_PORT": str(rt.opcua_port),
                                   "VIRTUAL_PLANT_MODBUS_PORT": str(rt.modbus_port)})
    key_of = {t.tag_id: t.key for t in pm.tags}
    async with rt:
        gateway.load_plant_map(pm)
        assert gateway.tenant_id == TENANT and gateway.gateway_id == "gw-planta-manaus-01"
        for d in gateway.drivers:   # drivers reconectam rápido nos testes
            if hasattr(d, "_reconnect_delay"):
                d._reconnect_delay = 0.2
        gateway._running = True
        tasks = [asyncio.create_task(d.run()) for d in gateway.drivers]
        await asyncio.sleep(5.0)
        await rt.freeze()
        await asyncio.sleep(1.5)    # última rodada de leitura com o modelo parado
        truth = rt.model.values(include_analog=False)
        emitted = rt.pulses["montagem.fixacao.a1.eos"]
        while emitted.backlog or emitted.level:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        assert await gateway.publish_heartbeat()
        hb = gateway.heartbeat_payload()
        gateway._running = False
        for d in gateway.drivers:
            await d.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    sent = gateway.client.sent
    readings = [(key_of[t.rsplit("/", 1)[1]], p) for t, p in sent if "/sensors/" in t]
    assert all(t.startswith(f"factory/{TENANT}/") for t, _ in sent)
    last = {}
    for key, p in readings:
        last[key] = p
    # todos os sinais chegaram ao broker
    assert set(last) == {t.key for t in pm.tags}

    # contadores do CLP: valor publicado == valor do CLP (acumulado bruto, servidor trata rollover)
    for key in ("montagem.fixacao.a3.cycles", "montagem.robo.weld_cycles", "montagem.robo.axis1.cycles",
                "montagem.transportador.parts_good", "usinagem.torno.parts_good", "usinagem.centro.gripper.cycles"):
        assert last[key]["value"] == truth[key], key
    # contadores publicados nunca diminuem, exceto rollover do CLP de 16 bits
    a1_vals = [p["value"] for k, p in readings if k == "montagem.fixacao.a1.cycles"]
    assert a1_vals == sorted(a1_vals) and len(a1_vals) > 3   # sem deadband: cada incremento publicado

    # cycle_pulse: acumulado do edge == pulsos emitidos pelo bit de fim de curso
    pulse_vals = [p["value"] for k, p in readings if k == "montagem.fixacao.a1.eos"]
    assert pulse_vals == sorted(pulse_vals)
    assert pulse_vals[-1] == gateway.counter_store.get("montagem.fixacao.a1.eos")
    assert abs(pulse_vals[-1] - emitted.emitted) <= 1   # o 1º pulso pode estar alto ao conectar
    assert pulse_vals[-1] >= 5

    # OPC UA: timestamp de origem (SourceTimestamp do servidor), recente e em UTC
    p = last["usinagem.torno.parts_good"]
    ts = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
    assert timedelta(0) <= datetime.now(timezone.utc) - ts < timedelta(seconds=30)
    assert p["unit"] == "pcs" and p["v"] == 1

    # heartbeat por gateway, fora do tópico de sensores
    hb_msgs = [(t, p) for t, p in sent if t.endswith("/heartbeat")]
    assert hb_msgs and hb_msgs[0][0] == f"factory/{TENANT}/gateways/gw-planta-manaus-01/heartbeat"
    assert hb_msgs[0][1]["gateway_id"] == "gw-planta-manaus-01" and hb_msgs[0][1]["buffered"] == 0
    assert hb["sources"] == {"opcua-plc": True, "modbus-plc": True, "modbus-plc:pulses": True}


async def test_refresh_republishes_opcua_only_when_source_connected(gateway, monkeypatch):
    doc = provisioned_doc()
    pm = parse_plant_map(doc, env={})
    gateway.load_plant_map(pm)
    gateway.filter._max_silence = 0.01
    tag = next(t for t in pm.tags if t.key == "usinagem.torno.run_state")
    await gateway._handle_reading(tag.tag_id, 1.0)
    drv = gateway._refresh_tags[tag.tag_id]
    n_before = len(gateway.client.sent)

    drv.connected = False
    await asyncio.sleep(0.02)
    assert await gateway.refresh_once() == 0           # fonte caída: não finge dado novo
    assert len(gateway.client.sent) == n_before

    drv.connected = True
    assert await gateway.refresh_once() == 1
    assert len(gateway.client.sent) == n_before + 1     # heartbeat do valor estável (max_silence)
    assert gateway.client.sent[-1][1]["value"] == 1.0
